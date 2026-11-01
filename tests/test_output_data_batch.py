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
        from swe.harness import rate_limits

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
        from swe.sessions.commands import cmd_models

        raw = {
            "claude": {("", "m-one"): 3},
            "codex": {("", "m-two"): 1},
        }
        out = io.StringIO()
        with patch(
            "swe.sessions.commands.collect_model_usage", return_value=raw
        ), redirect_stdout(out):
            cmd_models(["-t"])
        lines = out.getvalue().splitlines()
        i1 = next(i for i, l in enumerate(lines) if "m-one" in l)
        i2 = next(i for i, l in enumerate(lines) if "m-two" in l)
        self.assertEqual(lines[i1 + 1], "")
        self.assertEqual(lines[i2 - 1], "")

    def test_one_separator_per_group_not_per_row(self):
        from swe.sessions.commands import cmd_models

        raw = {
            "claude": {("", "m-a"): 3},
            "codex": {("", "m-b"): 2, ("", "m-c"): 1},
        }
        out = io.StringIO()
        with patch(
            "swe.sessions.commands.collect_model_usage", return_value=raw
        ), redirect_stdout(out):
            cmd_models(["-t"])
        lines = out.getvalue().splitlines()
        ib = next(i for i, l in enumerate(lines) if "m-b" in l)
        ic = next(i for i, l in enumerate(lines) if "m-c" in l)
        # The group's second row follows its first with no gap.
        self.assertEqual(ic - ib, 1)
        # And the boundary still lands once, before the group.
        self.assertEqual(lines[ib - 1], "")


class RateLimitCacheMergeTest(unittest.TestCase):
    """3: a filtered run must not drop unrequested providers' readings."""

    def test_filtered_run_preserves_other_providers_cache_entries(self):
        from swe.harness import rate_limits
        from swe.harness.models import RateLimitInfo

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
        from swe.config_commands import cmd_config

        out = io.StringIO()
        with patch(
            "swe.config_commands.load_resolved_config", return_value=config
        ), patch(
            "swe.config_commands.load_config", return_value=config
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
        from swe.mcp.formats import get_conversion_issues

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
        from swe.keys import read_key

        fd = self._pipe(b"\x1b\x1b")
        try:
            self.assertEqual(read_key(fd), "escape")
        finally:
            os.close(fd)

    def test_arrows_still_parse(self):
        from swe.keys import read_key

        fd = self._pipe(b"\x1b[A")
        try:
            self.assertEqual(read_key(fd), "up")
        finally:
            os.close(fd)


class JsoncWritePreservesCommentsTest(unittest.TestCase):
    """7: saving into a .jsonc file keeps its comments and siblings."""

    def test_comments_outside_the_managed_key_survive(self):
        from swe.mcp.cli import get_tool_saver, load_json

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
        from swe.mcp.cli import get_tool_saver, load_json

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
        from swe.sessions import usage

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "counts.json"
            with patch.object(usage, "SESSION_COUNTS_CACHE_FILE", cache):
                usage._save_cached_counts(7, {"claude": 3}, {"bad.jsonl"})
                usage._save_cached_counts(
                    7, {"claude": 4}, set(), merge_prior=True)
            windows = json.loads(cache.read_text())["windows"]
        entry = next(iter(windows.values()))
        self.assertEqual(entry["broken"], ["bad.jsonl"])
        self.assertEqual(entry["counts"]["claude"], 4)

    def test_fresh_scan_replaces_broken_list(self):
        # A fresh parse is authoritative: a recovered file must stop
        # reading as broken once the scan runs clean.
        from swe.sessions import usage

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "counts.json"
            with patch.object(usage, "SESSION_COUNTS_CACHE_FILE", cache):
                usage._save_cached_counts(7, {"claude": 3}, {"bad.jsonl"})
                usage._save_cached_counts(
                    7, {"claude": 4}, set(), merge_prior=False)
            windows = json.loads(cache.read_text())["windows"]
        entry = next(iter(windows.values()))
        self.assertEqual(entry["broken"], [])


class HereFilterSkipsBadPathsTest(unittest.TestCase):
    """10: empty/relative paths never match --here."""

    def test_empty_and_relative_paths_never_match(self):
        from swe.sessions import aggregator
        from swe.sessions.models import Session

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
        from swe.sessions.models import Session
        from swe.sessions import status

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
        from swe.sessions.models import Session
        from swe.sessions import status

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
        from swe.sessions.parsers import _claude_transcript_paths

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
        from swe.sessions import parsers
        from swe.sessions.models import Session

        older = Session(timestamp=1, agent="claude", path="/a",
                        session_id="s1", tool_name="claude")
        newer = Session(timestamp=2, agent="claude", path="/b",
                        session_id="s1", tool_name="claude")
        with patch.object(
            parsers, "parse_jsonl_projects", return_value=[older, newer]
        ), patch.object(
            parsers, "_claude_transcript_paths", return_value={}
        ), patch.object(
            parsers, "_account_homes", return_value=[]
        ):
            sessions = parsers.parse_claude()
        self.assertEqual(len(sessions), 1)
        self.assertIs(sessions[0], newer)

    def test_probe_reads_newest_transcript_copy(self):
        from swe.sessions import status
        from swe.sessions.models import Session

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
        from swe.table import Table

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
        from swe.find.plugins import _from_codex_toml

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
        from swe.cli import cmd_complete

        out = io.StringIO()
        with patch(
            "swe.completion.get_completions",
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

        from swe import prompt

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
            # Pad the 50ms production window: on a loaded builder the writer
            # thread's 20ms nap can slip past it and the leftover LF reads as
            # a phantom empty line. The test pins the branch, not the race.
            with patch.object(prompt, "_CRLF_PEEK_TIMEOUT", 1.0):
                self.assertEqual(prompt._read_line_bytes(r, echo=False), "a")
                self.assertEqual(prompt._read_line_bytes(r, echo=False), "b")
                with self.assertRaises(EOFError):
                    prompt._read_line_bytes(r, echo=False)
        finally:
            os.close(r)


class WideGlyphTableTest(unittest.TestCase):
    """19: CJK cells measure two columns, so padding lines up."""

    def test_cjk_cell_pads_to_column_width(self):
        from swe.console import cell_len
        from swe.table import Table

        t = Table()
        t.add_column("name", "NAME", width=8, fit="fixed", kind="text")
        t.add_column("v", "V", width=3, fit="fixed", kind="number")
        t.add_row({"name": "開発", "v": 1})       # 4 cells in a width-8 column
        lines = t.render()
        for line in lines:
            self.assertEqual(cell_len(line), 11 + 2)  # 8 + gap 2 + 3

    def test_truncate_cells_never_exceeds_budget(self):
        from swe.console import cell_len, truncate_cells

        cut = truncate_cells("開発テスト", 5)
        self.assertLessEqual(cell_len(cut), 5)

    def test_vs16_promotion_counts_in_the_cut(self):
        from swe.console import cell_len, truncate_cells

        # ❤ + VS16 renders 2 cells each; a naive per-char scan let the
        # ellipsis push the result to 7 cells and shifted later columns.
        cut = truncate_cells("❤️❤️❤️", 5)
        self.assertLessEqual(cell_len(cut), 5)


class C1StStripTest(unittest.TestCase):
    """20: a C1 ST terminates an OSC sequence — text after it survives."""

    def test_osc_terminated_by_c1_st(self):
        from swe.console import strip_ansi

        self.assertEqual(
            strip_ansi("\x1b]8;;https://example.com\x9cVISIBLE"),
            "VISIBLE",
        )


class CellWidthEdgeCasesTest(unittest.TestCase):
    """19-continued: every zero-width class, and the boundary branches of
    the cell-measured truncate/elide helpers."""

    def test_zero_width_classes_measure_zero(self):
        from swe.console import _char_cells

        self.assertEqual(_char_cells("\u0301"), 0)       # combining mark
        self.assertEqual(_char_cells("\u200d"), 0)       # ZWJ
        self.assertEqual(_char_cells("\ufe0f"), 0)       # variation selector
        self.assertEqual(_char_cells("\U0001f3fb"), 0)   # skin-tone modifier
        self.assertEqual(_char_cells("\U000e0061"), 0)   # tag char
        self.assertEqual(_char_cells("\U0001f1e6"), 2)   # regional indicator
        self.assertEqual(_char_cells("開"), 2)
        self.assertEqual(_char_cells("x"), 1)

    def test_joined_sequences_still_count_once(self):
        from swe.console import cell_len

        self.assertEqual(cell_len("a\u200db"), 1)                 # ZWJ merge
        self.assertEqual(cell_len("\U0001f1fa\U0001f1f8"), 2)     # flag pair
        self.assertEqual(cell_len("e\u0301"), 1)                  # precomposed-ish

    def test_take_cells_end_drops_dangling_mark(self):
        from swe.console import _take_cells_end

        # Scanning backwards ends on the combining mark whose base char
        # was cut — leaving it would paint the mark against the ellipsis.
        self.assertEqual(_take_cells_end("q\u0301zz", 2), "zz")

    def test_truncate_and_elide_edges(self):
        from swe.console import elide_cells, truncate_cells

        self.assertEqual(truncate_cells("abcdef", 0), "")
        # A budget too small for "..." falls back to a bare cut.
        self.assertEqual(truncate_cells("abcdef", 3), "abc")
        self.assertEqual(elide_cells("abcdef", 0), "")
        self.assertEqual(elide_cells("abcdef", 1), "…")


class EscAbortsSequenceTest(unittest.TestCase):
    """6-continued: an Esc arriving mid-CSI aborts it — the widget still
    sees a single cancel rather than a swallowed pair."""

    def test_esc_mid_sequence_is_escape(self):
        from swe.keys import read_key

        r, w = os.pipe()
        os.write(w, b"\x1b[\x1b")
        os.close(w)
        try:
            self.assertEqual(read_key(r), "escape")
        finally:
            os.close(r)


class JsoncEdgeCasesTest(unittest.TestCase):
    """7-continued: the surgical writer bails on layouts it cannot map,
    and the caller falls back to a full save with a stderr warning."""

    def test_key_span_returns_none_on_unmapped_layouts(self):
        from swe.mcp.cli import _jsonc_key_span

        self.assertIsNone(_jsonc_key_span("[1, 2]", "mcp"))        # not an object
        self.assertIsNone(_jsonc_key_span("{}", "mcp"))            # key absent
        self.assertIsNone(_jsonc_key_span('{ 5: 1 }', "mcp"))      # bare member
        self.assertIsNone(_jsonc_key_span('{ "mcp" 5 }', "mcp"))   # no colon
        self.assertIsNone(_jsonc_key_span('{"mcp": {', "mcp"))     # unterminated

    def test_block_comments_are_skipped(self):
        from swe.mcp.cli import _jsonc_key_span

        # A /* */ before the root, ahead of the value, and mid-value.
        text = ('/* head */\n{\n  "mcp": /* inline */ '
                '{"a": /* mid */ 1}\n}\n')
        span = _jsonc_key_span(text, "mcp")
        self.assertIsNotNone(span)
        self.assertIsNone(_jsonc_key_span('{/* never closed', "mcp"))

    def test_slice_save_insert_paths_and_failures(self):
        from swe.mcp.cli import _save_jsonc_slice, load_json

        with tempfile.TemporaryDirectory() as tmp:
            # Empty object: member inserted after the opening brace.
            p1 = Path(tmp) / "a.jsonc"
            p1.write_text("{}\n")
            self.assertTrue(_save_jsonc_slice(p1, "mcp", {"s": {"command": "x"}}))
            self.assertEqual(load_json(p1)["mcp"], {"s": {"command": "x"}})

            # Trailing comma: member inserted without doubling it.
            p2 = Path(tmp) / "b.jsonc"
            p2.write_text('{\n  "theme": "dark",\n}\n')
            self.assertTrue(_save_jsonc_slice(p2, "mcp", {}))
            self.assertEqual(load_json(p2)["theme"], "dark")

            # A // comment trailing the last member: the comma must land
            # before it, not inside the comment where it would vanish.
            p4 = Path(tmp) / "d.jsonc"
            p4.write_text('{\n  "theme": "dark"\n  // keep\n}\n')
            self.assertTrue(_save_jsonc_slice(p4, "mcp", {"s": {}}))
            data = load_json(p4)
            self.assertEqual(data["theme"], "dark")
            self.assertEqual(data["mcp"], {"s": {}})
            self.assertIn("// keep", p4.read_text())

            # No closing brace at all: refuse rather than guess.
            p3 = Path(tmp) / "c.jsonc"
            p3.write_text("// just a note\n")
            self.assertFalse(_save_jsonc_slice(p3, "mcp", {}))

            # Unreadable file: False, not a traceback.
            self.assertFalse(
                _save_jsonc_slice(Path(tmp) / "absent.jsonc", "mcp", {}))

    def test_unmapped_layout_warns_then_fails_cleanly(self):
        from swe.mcp.cli import CorruptConfigurationError, get_tool_saver

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kilo.jsonc"
            p.write_text("// only a comment, no object\n")
            err = io.StringIO()
            # The slice fails, the warning fires, and the strict loader
            # refuses to treat the file as {} — write paths propagate.
            with redirect_stderr(err), self.assertRaises(
                CorruptConfigurationError
            ):
                get_tool_saver("kilo")({"s": {"command": "x"}}, p)
            self.assertIn("comments could not be preserved", err.getvalue())
            # And the file itself was never rewritten.
            self.assertEqual(p.read_text(), "// only a comment, no object\n")

    def test_unreadable_file_falls_back_without_warning(self):
        from swe.mcp.cli import CorruptConfigurationError, get_tool_saver

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kilo.jsonc"
            p.write_text('{\n  // note\n  "mcp": {}\n}\n')
            err = io.StringIO()
            with redirect_stderr(err), \
                 patch("pathlib.Path.read_text", side_effect=OSError("busy")), \
                 self.assertRaises(CorruptConfigurationError):
                get_tool_saver("kilo")({"s": {"command": "x"}}, p)
            self.assertNotIn("comments could not be preserved", err.getvalue())


class RateLimitLegacyNoDataTest(unittest.TestCase):
    """3-continued: the pre-timestamp ``no_data: [name, ...]`` list form
    also survives a filtered run."""

    def test_legacy_list_no_data_entries_carry_over(self):
        from swe.harness import rate_limits
        from swe.harness.models import RateLimitInfo

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits.json"
            now = 1_700_000_000.0
            cache.write_text(json.dumps({
                "cached_at": now,
                "limits": {},
                "updated_at": {},
                "no_data": ["gemini"],
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
        self.assertIn("gemini", saved["no_data"])
        self.assertIn("claude", saved["limits"])


class RateLimitMergeRobustnessTest(unittest.TestCase):
    """3-continued: a cache that parses but is the wrong shape merges as
    empty, and carried readings keep their original age."""

    def _run_filtered(self, cache, now=1_700_000_000.0):
        from swe.harness import rate_limits
        from swe.harness.models import RateLimitInfo

        def fake():
            return RateLimitInfo(
                tool_name="claude", used_percent=10,
                limit_reached=False, reset_at=0.0, plan_type="pro",
                window_seconds=0,
            )

        with patch.object(rate_limits, "RATE_LIMITS_CACHE_FILE", cache), \
             patch.object(rate_limits, "_FETCHERS", {"claude": fake}), \
             patch.object(rate_limits.time, "time", return_value=now):
            return rate_limits.get_all_rate_limits(
                use_cache=False, tool_names={"claude"})

    def test_malformed_cache_shapes_do_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            for body in ("[]", '{"limits": [1]}', '"text"'):
                cache = Path(tmp) / "rate_limits.json"
                cache.write_text(body)
                result = self._run_filtered(cache)
                self.assertIn("claude", result)

    def test_carried_reading_keeps_its_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits.json"
            old = 1_600_000_000.0          # the file's own cached_at
            now = 1_700_000_000.0
            cache.write_text(json.dumps({
                "cached_at": old,
                "limits": {
                    "cursor": {"tool_name": "cursor", "used_percent": 7,
                               "limit_reached": False, "reset_at": 0.0,
                               "plan_type": "p", "window_seconds": 0},
                },
                # cursor has no updated_at entry: its age is the file's.
                "updated_at": {},
            }))
            self._run_filtered(cache, now=now)
            saved = json.loads(cache.read_text())
        self.assertIn("cursor", saved["limits"])
        self.assertEqual(saved["updated_at"]["cursor"], old)


class ClaudeWorkingJobStalenessTest(unittest.TestCase):
    """11-continued: a day-old ``working`` mark is a killed job, not a
    live one."""

    def _write_state(self, home, state, age):
        d = Path(home) / ".claude" / "jobs" / "j1"
        d.mkdir(parents=True)
        f = d / "state.json"
        f.write_text(json.dumps({"sessionId": "s1", "state": state}))
        old = os.path.getmtime(str(f)) - age
        os.utime(f, (old, old))

    def test_stale_working_state_is_dropped(self):
        from swe.sessions import status

        with tempfile.TemporaryDirectory() as tmp:
            self._write_state(tmp, "working", status._JOB_WORKING_STALE_S + 10)
            with patch.dict(os.environ, {"HOME": tmp}):
                self.assertEqual(status._claude_job_states(), {})

    def test_fresh_working_state_is_kept(self):
        from swe.sessions import status

        with tempfile.TemporaryDirectory() as tmp:
            self._write_state(tmp, "working", 0)
            with patch.dict(os.environ, {"HOME": tmp}):
                self.assertEqual(
                    status._claude_job_states(), {"s1": "working"})


class CopilotStrictNullArgsTest(unittest.TestCase):
    """5-continued: an explicit ``args: null`` is also not a lossy
    conversion when the emitter stamps ``args: []``."""

    def test_null_args_is_not_lossy(self):
        from swe.mcp.formats import (
            McpFormatHandler, get_conversion_issues, register_format_handler,
        )

        class _Stub(McpFormatHandler):
            def parse(self, raw):
                return {"command": "x", "args": None}

            def emit(self, canonical):
                return dict(canonical)

        register_format_handler("_stub_null_args", _Stub())
        try:
            issues = get_conversion_issues({}, "_stub_null_args", "copilot")
        finally:
            from swe.mcp import formats
            formats._FORMAT_MCP_HANDLERS.pop("_stub_null_args", None)
        self.assertNotIn("args", "; ".join(issues))


class CodexPluginNoFlagTest(unittest.TestCase):
    """16-continued: a plugins block without ``enabled`` is skipped, and
    an indented header still counts as a header."""

    def test_indented_header_is_found(self):
        from swe.find.plugins import _from_codex_toml

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / ".codex"
            cfg.mkdir()
            (cfg / "config.toml").write_text(
                '  [plugins."nested@openai-curated"]\n'
                "  enabled = true\n"
            )
            found = _from_codex_toml(home)
        self.assertEqual([p.name for p in found], ["nested"])

    def test_block_without_enabled_is_skipped(self):
        from swe.find.plugins import _from_codex_toml

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg = home / ".codex"
            cfg.mkdir()
            (cfg / "config.toml").write_text(
                '[plugins."declared@openai-curated"]\n'
                'note = "no flag here"\n'
                '[plugins."github@openai-curated"]\n'
                "enabled = true\n"
            )
            found = _from_codex_toml(home)
        self.assertEqual([p.name for p in found], ["github"])


class ClaudeTranscriptStatFailureTest(unittest.TestCase):
    """13-continued: a stat failure still records the path (mtime 0.0),
    and a sid-less session survives the dedupe filter."""

    def test_stat_oserror_falls_back_to_zero(self):
        from swe.sessions import parsers

        class _Entry:
            def __init__(self, name, path, is_dir=False, broken=False):
                self.name, self.path = name, path
                self._dir, self._broken = is_dir, broken

            def is_dir(self):
                return self._dir

            def stat(self):
                if self._broken:
                    raise OSError("vanished")
                return os.stat(self.path)

        project = _Entry("-proj", "/proj", is_dir=True)
        entry = _Entry("s1.jsonl", "/proj/s1.jsonl", broken=True)

        def _scandir(path):
            m = unittest.mock.MagicMock()
            m.__enter__.return_value = iter(
                [project] if path == "base" else [entry])
            m.__exit__.return_value = False
            return m

        with patch("os.scandir", side_effect=_scandir):
            paths = parsers._claude_transcript_paths("base")
        self.assertEqual(paths["s1"], "/proj/s1.jsonl")

    def test_dedupe_keeps_sessions_without_id(self):
        from swe.sessions import parsers
        from swe.sessions.models import Session

        anon = Session(timestamp=0, agent="claude", path="/x",
                       session_id="", tool_name="claude")
        older = Session(timestamp=1, agent="claude", path="/a",
                        session_id="s1", tool_name="claude")
        newer = Session(timestamp=2, agent="claude", path="/b",
                        session_id="s1", tool_name="claude")
        with patch.object(
            parsers, "parse_jsonl_projects",
            return_value=[anon, older, newer],
        ), patch.object(
            parsers, "_claude_transcript_paths", return_value={}
        ), patch.object(
            parsers, "_account_homes", return_value=[]
        ):
            sessions = parsers.parse_claude()
        self.assertEqual(len(sessions), 2)
        self.assertIn(anon, sessions)
        self.assertIn(newer, sessions)


class ClaudeProbeStatFailureTest(unittest.TestCase):
    """13-continued: an OSError while ordering transcript copies falls
    back to glob order rather than dropping the probe."""

    def test_getmtime_failure_still_probes(self):
        from swe.sessions import status
        from swe.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / ".claude" / "projects"
            for name in ("-a", "-b"):
                d = root / name
                d.mkdir(parents=True)
                (d / "s1.jsonl").write_text(
                    json.dumps({
                        "type": "assistant",
                        "message": {"content": [
                            {"type": "text", "text": "live"}
                        ], "stop_reason": "end_turn"},
                    }) + "\n"
                )
            real = os.path.getmtime

            def _flaky(p):
                if str(p).endswith(".jsonl"):
                    raise OSError("boom")
                return real(p)

            sess = Session(timestamp=0, agent="claude", path="/p",
                           session_id="s1", tool_name="claude")
            with patch.dict(os.environ, {"HOME": tmp}), patch(
                "os.path.getmtime", side_effect=_flaky
            ):
                phase, text = status._probe_claude(sess, {})
        self.assertEqual(phase, "finished")
        self.assertEqual(text, "live")


if __name__ == "__main__":
    unittest.main()
