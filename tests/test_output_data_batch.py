"""Regression tests for the wrong-output / data-quality batch.

Each test names the finding it pins: stdout/stderr separation, cache
merge semantics, null-vs-absent config reads, strict-conversion false
positives, key-reader Esc handling, JSONC comment preservation, prune
scoping, broken-parser lists, --here matching, status mappings, table
placeholders, codex plugin parsing, completion hygiene, CRLF handling,
wide-glyph columns, and C1-ST escape stripping.
"""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


class CaWarningGoesToStderrTest(unittest.TestCase):
    """1: a warning on stdout corrupts `swe list --json --usage`."""

    def test_warn_untrusted_ca_prints_to_stderr_only(self):
        from quiver.harness import rate_limits

        rate_limits._CA_WARNED = False
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rate_limits._warn_untrusted_ca()
        finally:
            rate_limits._CA_WARNED = False
        self.assertEqual(out.getvalue(), "")
        self.assertIn("certificate", err.getvalue())


class ModelsByToolSeparatorsTest(unittest.TestCase):
    """2: blank separators belong between tool groups, not above."""

    def test_separator_sits_between_tool_groups(self):
        from quiver.sessions.commands import cmd_models

        raw = {
            "claude": {("", "m-one"): 3},
            "codex": {("", "m-two"): 1},
        }
        out = io.StringIO()
        with patch(
            "quiver.sessions.commands.collect_model_usage", return_value=raw
        ), redirect_stdout(out):
            cmd_models(["-t"])
        lines = out.getvalue().splitlines()
        i1 = next(i for i, l in enumerate(lines) if "m-one" in l)
        i2 = next(i for i, l in enumerate(lines) if "m-two" in l)
        self.assertEqual(lines[i1 + 1], "")
        self.assertEqual(lines[i2 - 1], "")


class RateLimitCacheMergeTest(unittest.TestCase):
    """3: a filtered run must not drop unrequested providers' readings."""

    def test_filtered_run_preserves_other_providers_cache_entries(self):
        from quiver.harness import rate_limits
        from quiver.harness.models import RateLimitInfo

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits.json"
            now = 1_700_000_000.0
            cache.write_text(json.dumps({
                "cached_at": now,
                "limits": {
                    "cursor": {"tool_name": "cursor", "used_percent": 7,
                               "limit_reached": False, "reset_at": 0.0,
                               "plan_type": "p", "window_seconds": 0},
                },
                "updated_at": {"cursor": now},
                "no_data": {"gemini": now},
            }))

            def fake():
                return RateLimitInfo(
                    tool_name="claude", used_percent=10,
                    limit_reached=False, reset_at=0.0, plan_type="pro",
                    window_seconds=0,
                )

            with patch.object(rate_limits, "RATE_LIMITS_CACHE_FILE", cache), \
                 patch.object(rate_limits, "_FETCHERS", {"claude": fake}), \
                 patch.object(rate_limits.time, "time", return_value=now):
                rate_limits.get_all_rate_limits(
                    use_cache=False, tool_names={"claude"})

            saved = json.loads(cache.read_text())
        self.assertIn("cursor", saved["limits"])
        self.assertEqual(saved["updated_at"]["cursor"], now)
        self.assertIn("gemini", saved["no_data"])
        self.assertIn("claude", saved["limits"])


class ConfigGetNullTest(unittest.TestCase):
    """4: an explicit null differs from an absent key."""

    def _run(self, args, config):
        from quiver.config_commands import cmd_config

        out = io.StringIO()
        with patch(
            "quiver.config_commands.load_resolved_config", return_value=config
        ), patch(
            "quiver.config_commands.load_config", return_value=config
        ), redirect_stdout(out):
            rc = cmd_config(args)
        return rc, out.getvalue()

    def test_null_valued_key_prints_null(self):
        rc, out = self._run(
            ["get", "report.writer.model"],
            {"report": {"writer": {"model": None}}},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "null")

    def test_absent_key_still_unknown(self):
        rc, out = self._run(["get", "no.such.key"], {"a": 1})
        self.assertEqual(rc, 1)
        self.assertIn("Unknown configuration key", out)


class CopilotStrictArgsTest(unittest.TestCase):
    """5: absent args and args: [] are the same conversion outcome."""

    def test_absent_args_is_not_lossy(self):
        from quiver.mcp.formats import get_conversion_issues

        issues = get_conversion_issues(
            {"command": "run-server"}, "standard", "copilot"
        )
        self.assertNotIn("args", "; ".join(issues))


class DoubleEscTest(unittest.TestCase):
    """6: Esc Esc is two presses, and the pair still cancels."""

    def _pipe(self, payload: bytes):
        r, w = os.pipe()
        os.write(w, payload)
        os.close(w)
        return r

    def test_esc_esc_returns_escape(self):
        from quiver.keys import read_key

        fd = self._pipe(b"\x1b\x1b")
        try:
            self.assertEqual(read_key(fd), "escape")
        finally:
            os.close(fd)

    def test_arrows_still_parse(self):
        from quiver.keys import read_key

        fd = self._pipe(b"\x1b[A")
        try:
            self.assertEqual(read_key(fd), "up")
        finally:
            os.close(fd)


class JsoncWritePreservesCommentsTest(unittest.TestCase):
    """7: saving into a .jsonc file keeps its comments and siblings."""

    def test_comments_outside_the_managed_key_survive(self):
        from quiver.mcp.cli import get_tool_saver, load_json

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kilo.jsonc"
            p.write_text(
                '{\n  // user note\n  "mcp": {\n'
                '    "old": {"command": "gone"}\n  },\n'
                '  // trailing note\n  "theme": "dark"\n}\n'
            )
            get_tool_saver("kilo")({"new": {"command": "ok"}}, p)
            raw = p.read_text()
            self.assertIn("// user note", raw)
            self.assertIn("// trailing note", raw)
            data = load_json(p)
            self.assertEqual(data["theme"], "dark")
            self.assertEqual(data["mcp"], {"new": {"command": "ok"}})

    def test_missing_key_inserts_without_touching_comments(self):
        from quiver.mcp.cli import get_tool_saver, load_json

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kilo.jsonc"
            p.write_text('{\n  // keep\n  "theme": "dark"\n}\n')
            get_tool_saver("kilo")({"srv": {"command": "x"}}, p)
            raw = p.read_text()
            self.assertIn("// keep", raw)
            self.assertEqual(load_json(p)["mcp"], {"srv": {"command": "x"}})


class BrokenCountsPreservedTest(unittest.TestCase):
    """9: a cache-fed second pass must not erase recorded failures."""

    def test_second_save_keeps_broken_list(self):
        from quiver.sessions import usage

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "counts.json"
            with patch.object(usage, "SESSION_COUNTS_CACHE_FILE", cache):
                usage._save_cached_counts(7, {"claude": 3}, {"bad.jsonl"})
                usage._save_cached_counts(7, {"claude": 4}, set())
            windows = json.loads(cache.read_text())["windows"]
        entry = next(iter(windows.values()))
        self.assertEqual(entry["broken"], ["bad.jsonl"])
        self.assertEqual(entry["counts"]["claude"], 4)


class HereFilterSkipsBadPathsTest(unittest.TestCase):
    """10: empty/relative paths never match --here."""

    def test_empty_and_relative_paths_never_match(self):
        from quiver.sessions import aggregator
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            inside = str(Path(tmp) / "sub")
            sessions = [
                Session(timestamp=1, agent="a", path="",
                        session_id="e", tool_name="claude"),
                Session(timestamp=1, agent="a", path="relative",
                        session_id="r", tool_name="claude"),
                Session(timestamp=1, agent="a", path=inside,
                        session_id="ok", tool_name="claude"),
            ]
            with patch.object(
                aggregator, "_load_cached_sessions", return_value=sessions
            ):
                got = aggregator.get_all_sessions(
                    cwd=tmp, use_cache=True, limit=None)
        self.assertEqual([s.session_id for s in got], ["ok"])


class ClaudeWorkingStatusTest(unittest.TestCase):
    """11: a background job's \"working\" state is active, not done."""

    def test_working_job_reports_active(self):
        from quiver.sessions.models import Session
        from quiver.sessions import status

        sess = Session(timestamp=0, agent="claude", path="/p",
                       session_id="s1", tool_name="claude")
        ctx = {"claude_jobs": {"s1": "working"}}
        with patch.dict(status._PROBES,
                        {"claude": lambda s, c: ("finished", "")}):
            self.assertEqual(status._one_status(sess, 0.0, ctx), status.ACTIVE)


class DevinFallbackRowidTest(unittest.TestCase):
    """12: without a chain head, the last *written* node wins — node_id
    ties or lexicographic order must not pick a sibling branch."""

    def test_fallback_reads_last_inserted_node(self):
        from quiver.sessions.models import Session
        from quiver.sessions import status

        with tempfile.TemporaryDirectory() as tmp:
            dbdir = Path(tmp) / ".local" / "share" / "devin" / "cli"
            dbdir.mkdir(parents=True)
            conn = sqlite3.connect(str(dbdir / "sessions.db"))
            conn.execute("CREATE TABLE sessions (id TEXT, main_chain_id TEXT)")
            conn.execute(
                "CREATE TABLE message_nodes "
                "(node_id TEXT, parent_node_id TEXT, session_id TEXT, "
                " chat_message TEXT)"
            )
            conn.execute("INSERT INTO sessions VALUES ('s1', NULL)")
            # Abandoned sibling branch written first but carrying the
            # lexicographically-later node_id the old ordering picked.
            conn.execute(
                "INSERT INTO message_nodes VALUES (?, ?, ?, ?)",
                ("9", None, "s1",
                 json.dumps({"role": "assistant",
                             "content": "abandoned branch"})),
            )
            conn.execute(
                "INSERT INTO message_nodes VALUES (?, ?, ?, ?)",
                ("10", "9", "s1",
                 json.dumps({"role": "assistant",
                             "content": "main chain tip"})),
            )
            conn.commit()
            conn.close()

            sess = Session(timestamp=0, agent="devin", path="/p",
                           session_id="s1", tool_name="devin")
            with patch.dict(os.environ, {"HOME": tmp}):
                conn2 = status._open_devin_db()
                try:
                    phase, text = status._probe_devin(sess, {"devin_conn": conn2})
                finally:
                    conn2.close()
        self.assertEqual(phase, "finished")
        self.assertEqual(text, "main chain tip")


class ClaudeCwdDedupeTest(unittest.TestCase):
    """13: one session resumed from two cwd values is one row, and the
    transcript map points at the live copy."""

    def test_transcript_paths_keep_newest_mtime(self):
        from quiver.sessions.parsers import _claude_transcript_paths

        with tempfile.TemporaryDirectory() as tmp:
            old_dir = Path(tmp) / "-old"
            new_dir = Path(tmp) / "-new"
            old_dir.mkdir()
            new_dir.mkdir()
            old = old_dir / "s1.jsonl"
            new = new_dir / "s1.jsonl"
            old.write_text("{}\n")
            new.write_text("{}\n")
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            paths = _claude_transcript_paths(tmp)
        self.assertEqual(paths["s1"], str(new))

    def test_parse_dedupes_resumed_session_rows(self):
        from quiver.sessions import parsers
        from quiver.sessions.models import Session

        older = Session(timestamp=1, agent="claude", path="/a",
                        session_id="s1", tool_name="claude")
        newer = Session(timestamp=2, agent="claude", path="/b",
                        session_id="s1", tool_name="claude")
        with patch.object(
            parsers, "parse_jsonl_projects", return_value=[older, newer]
        ), patch.object(
            parsers, "_claude_transcript_paths", return_value={}
        ):
            sessions = parsers.parse_claude()
        self.assertEqual(len(sessions), 1)
        self.assertIs(sessions[0], newer)

    def test_probe_reads_newest_transcript_copy(self):
        from quiver.sessions import status
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".claude" / "projects"
            for name, text, mtime in (
                ("-old", "stale", 1000),
                ("-new", "live", 2000),
            ):
                d = root / name
                d.mkdir(parents=True)
                f = d / "s1.jsonl"
                f.write_text(
                    json.dumps({
                        "type": "assistant",
                        "message": {"content": [
                            {"type": "text", "text": text}
                        ], "stop_reason": "end_turn"},
                    }) + "\n"
                )
                os.utime(f, (mtime, mtime))
            sess = Session(timestamp=0, agent="claude", path="/p",
                           session_id="s1", tool_name="claude")
            with patch.dict(os.environ, {"HOME": tmp}):
                phase, text = status._probe_claude(sess, {})
        self.assertEqual(phase, "finished")
        self.assertEqual(text, "live")


class TablePlaceholderTest(unittest.TestCase):
    """15: a missing key renders the marker, not a traceback."""

    def test_missing_key_in_numeric_and_timestamp_columns(self):
        from quiver.table import Table

        t = Table()
        t.add_column("n", "N", width=6, kind="number")
        t.add_column("c", "C", width=6, kind="count_threshold")
        t.add_column("t", "T", width=10, kind="timestamp",
                     formatter=lambda v: f"{v}s")
        t.add_row({"unrelated": True})
        lines = t.render()
        self.assertIn("-", lines[-1])
        self.assertNotIn("raceback", lines[-1])


class CodexPluginEnabledPlacementTest(unittest.TestCase):
    """16: enabled need not be the first line of a plugins block."""

    def test_enabled_after_other_keys_is_found(self):
        from quiver.find.plugins import _from_codex_toml

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / ".codex"
            cfg.mkdir()
            (cfg / "config.toml").write_text(
                '[plugins."github@openai-curated"]\n'
                'marketplace = "curated"\n'
                "enabled = true\n"
            )
            found = _from_codex_toml(home)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].enabled)


class CompletionSanitisedTest(unittest.TestCase):
    """17: newlines in a description cannot inject protocol lines."""

    def test_control_chars_flattened(self):
        from quiver.cli import cmd_complete

        out = io.StringIO()
        with patch(
            "quiver.completion.get_completions",
            return_value=[("cand", "line1\nline2\textra")],
        ), redirect_stdout(out):
            cmd_complete([])
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "cand\tline1 line2 extra")
        self.assertEqual(lines[1], ":4")


class CrLfReadLineTest(unittest.TestCase):
    """18: a paste-split CRLF does not hand back a phantom empty line."""

    def test_crlf_split_across_chunks_is_one_break(self):
        import threading
        import time

        from quiver.prompt import _read_line_bytes

        r, w = os.pipe()
        os.write(w, b"a\r")

        def _rest():
            time.sleep(0.02)
            try:
                os.write(w, b"\nb\r\n")
                os.close(w)
            except OSError:
                pass

        threading.Thread(target=_rest, daemon=True).start()
        try:
            self.assertEqual(_read_line_bytes(r, echo=False), "a")
            self.assertEqual(_read_line_bytes(r, echo=False), "b")
            with self.assertRaises(EOFError):
                _read_line_bytes(r, echo=False)
        finally:
            os.close(r)


class WideGlyphTableTest(unittest.TestCase):
    """19: CJK cells measure two columns, so padding lines up."""

    def test_cjk_cell_pads_to_column_width(self):
        from quiver.console import cell_len
        from quiver.table import Table

        t = Table()
        t.add_column("name", "NAME", width=8, fit="fixed", kind="text")
        t.add_column("v", "V", width=3, fit="fixed", kind="number")
        t.add_row({"name": "開発", "v": 1})       # 4 cells in a width-8 column
        lines = t.render()
        for line in lines:
            self.assertEqual(cell_len(line), 11 + 2)  # 8 + gap 2 + 3

    def test_truncate_cells_never_exceeds_budget(self):
        from quiver.console import cell_len, truncate_cells

        cut = truncate_cells("開発テスト", 5)
        self.assertLessEqual(cell_len(cut), 5)


class C1StStripTest(unittest.TestCase):
    """20: a C1 ST terminates an OSC sequence — text after it survives."""

    def test_osc_terminated_by_c1_st(self):
        from quiver.console import strip_ansi

        self.assertEqual(
            strip_ansi("\x1b]8;;https://example.com\x9cVISIBLE"),
            "VISIBLE",
        )


if __name__ == "__main__":
    unittest.main()
