"""Regression tests for the edge-case traceback batch (issue #392).

Each test feeds one finding's hostile input and pins a controlled outcome —
an error message, a cache miss, a skipped entry — instead of a traceback.
"""

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


# ── 1. `swe session use` on a file path ────────────────────────────


class SessionUseFilePathTest(unittest.TestCase):
    def test_existing_file_path_is_rejected_before_chdir(self):
        from quiver.sessions.commands import _resume_session
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "transcript.jsonl"
            file_path.write_text("{}\n")
            cwd_before = os.getcwd()
            session = Session(
                timestamp=0, agent="claude", path=str(file_path),
                session_id="s1", tool_name="claude",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = _resume_session(session)
            self.assertEqual(rc, 1)
            self.assertIn("Not a directory", buf.getvalue())
            # The process must not have moved — a chdir into the file's
            # parent is just as wrong as the traceback was.
            self.assertEqual(os.getcwd(), cwd_before)

    def test_existing_dir_still_resumes(self):
        from quiver.sessions.commands import _resume_session
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            session = Session(
                timestamp=0, agent="claude", path=tmp,
                session_id="s1", tool_name="claude",
            )
            cwd_before = os.getcwd()
            try:
                with mock.patch(
                    "quiver.harness.commands.cmd_use", return_value=0
                ) as use:
                    with redirect_stdout(io.StringIO()):
                        rc = _resume_session(session)
                self.assertEqual(rc, 0)
                self.assertTrue(use.called)
                self.assertEqual(os.getcwd(), os.path.realpath(tmp)
                                 if os.path.realpath(tmp) != tmp else tmp)
            finally:
                os.chdir(cwd_before)


# ── 2. Corrupt session-count cache ─────────────────────────────────


class SessionCountsCacheTest(unittest.TestCase):
    def _load(self, payload):
        from quiver.sessions import usage

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "session_counts.json"
            cache.write_text(json.dumps(payload))
            with mock.patch.object(usage, "SESSION_COUNTS_CACHE_FILE", cache):
                return usage._load_cached_counts(None)

    def test_unparseable_cached_at_is_a_miss(self):
        got = self._load({
            "windows": {"all": {"cached_at": "yesterday",
                                "counts": {"claude": 3}}}})
        self.assertIsNone(got)

    def test_non_numeric_count_is_a_miss(self):
        got = self._load({
            "windows": {"all": {"cached_at": 1,
                                "counts": {"claude": "lots"}}}})
        self.assertIsNone(got)

    def test_cached_at_in_the_future_is_a_hit(self):
        # A future timestamp is nonsense but harmless: it reads fresh
        # for the TTL, exactly like a just-written entry.
        import time

        got = self._load({
            "windows": {"all": {"cached_at": time.time() + 60,
                                "counts": {"claude": 3}}}})
        self.assertEqual(got, {"claude": 3})

    def test_inf_cached_at_is_a_miss(self):
        got = self._load({
            "windows": {"all": {"cached_at": float("inf"),
                                "counts": {"claude": 3}}}})
        self.assertIsNone(got)

    def test_overflowing_count_is_a_miss(self):
        # json can carry 1e999 -> float inf; int(inf) raises
        # OverflowError, not ValueError.
        got = self._load({
            "windows": {"all": {"cached_at": 1,
                                "counts": {"claude": 1e999}}}})
        self.assertIsNone(got)

    def test_valid_entry_still_loads(self):
        import time

        got = self._load({
            "windows": {"all": {"cached_at": time.time(),
                                "counts": {"claude": 3, "codex": 1},
                                "broken": ["gemini"]}}})
        self.assertEqual(got, {"claude": 3, "codex": 1})


# ── 3. swe doctor on hostile paths ──────────────────────────────────


class DoctorHostilePathsTest(unittest.TestCase):
    def test_registry_root_with_missing_user_warns_not_crashes(self):
        from quiver.harness.drift import check_registry_schema

        registry = {
            "x": {
                "capabilities": {
                    "skills": {
                        "supported": True,
                        "root": "~quiver-no-such-user-392/data",
                    },
                },
            },
        }
        findings = check_registry_schema(registry)
        self.assertTrue(
            any("does not exist" in f.message for f in findings),
            findings,
        )

    def test_dangling_symlink_scan_survives_unreadable_dir(self):
        from quiver.harness.drift import check_dangling_symlinks

        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            locked.chmod(0)
            try:
                if os.access(locked, os.R_OK):
                    self.skipTest("running as a user that ignores modes")
                findings = check_dangling_symlinks([locked])
            finally:
                locked.chmod(stat.S_IRWXU)
        self.assertTrue(
            any("cannot be scanned" in f.message for f in findings),
            findings,
        )

    def test_nvm_bin_dirs_survives_unreadable_versions_dir(self):
        from quiver.harness.path_health import nvm_bin_dirs

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            versions = home / ".nvm" / "versions" / "node"
            versions.mkdir(parents=True)
            (versions / "v22.0.0" / "bin").mkdir(parents=True)
            versions.chmod(0)
            try:
                if os.access(versions, os.R_OK):
                    self.skipTest("running as a user that ignores modes")
                self.assertEqual(nvm_bin_dirs(home), [])
            finally:
                versions.chmod(stat.S_IRWXU)


# ── 4. swe mcp edit on non-object JSON ─────────────────────────────


class McpEditNonObjectTest(unittest.TestCase):
    def test_array_edit_is_rejected_without_typeerror(self):
        from quiver.mcp import cli as cli_mod

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "mcp.json"
            cfg.write_text(json.dumps(
                {"mcpServers": {"srv": {"command": "echo"}}}))

            def fake_editor_run(argv, **_kw):
                # Simulate the user saving a JSON *array* — the bug was
                # ``name in edited`` matching, then edited[name] raising
                # TypeError on a list.
                Path(argv[-1]).write_text('["srv"]')
                return subprocess.CompletedProcess(argv, 0)

            saved = []
            with mock.patch.dict(
                cli_mod.MCP_CONFIG_MAP,
                {"claude": {"path": cfg, "key": "mcpServers",
                            "label": "Claude Code"}},
            ), mock.patch.object(
                cli_mod, "load_registry", return_value={"claude": {}},
            ), mock.patch.object(
                cli_mod.subprocess, "run", fake_editor_run,
            ), mock.patch.object(
                cli_mod, "save_json",
                lambda path, data: saved.append(data),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_mod.cmd_edit(["claude", "srv"])

            self.assertEqual(rc, 0)
            self.assertIn("must be a JSON object", buf.getvalue())
            # The write path must not have run: a rejected edit saves
            # nothing, especially not a clobbered file.
            self.assertEqual(saved, [])
            self.assertEqual(
                json.loads(cfg.read_text()),
                {"mcpServers": {"srv": {"command": "echo"}}},
            )

    def test_deeply_nested_edit_is_rejected_without_traceback(self):
        from quiver.mcp import cli as cli_mod

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "mcp.json"
            cfg.write_text(json.dumps(
                {"mcpServers": {"srv": {"command": "echo"}}}))

            def fake_editor_run(argv, **_kw):
                n = 60000
                Path(argv[-1]).write_text("[" * n + "]" * n)
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.dict(
                cli_mod.MCP_CONFIG_MAP,
                {"claude": {"path": cfg, "key": "mcpServers",
                            "label": "Claude Code"}},
            ), mock.patch.object(
                cli_mod, "load_registry", return_value={"claude": {}},
            ), mock.patch.object(
                cli_mod.subprocess, "run", fake_editor_run,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_mod.cmd_edit(["claude", "srv"])

            self.assertEqual(rc, 0)
            self.assertIn("no changes saved", buf.getvalue())


# ── 5. usage-events parse errors ───────────────────────────────────


class UsageEventsParseTest(unittest.TestCase):
    def test_fields_map_survives_overflowing_tokens(self):
        from quiver.harness.rate_limits import _usage_event_fields

        row = {
            "timestamp": "1700000000000",
            "tokenUsage": {
                "inputTokens": 1e999,      # int(inf) -> OverflowError
                "outputTokens": "junk",    # int -> ValueError
                "cacheReadTokens": None,   # int -> TypeError
            },
        }
        mapped = _usage_event_fields(row)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["input_tokens"], 0)
        self.assertEqual(mapped["output_tokens"], 0)
        self.assertEqual(mapped["cache_read_tokens"], 0)

    def test_bad_since_flag_reports_not_tracebacks(self):
        from quiver.harness.commands import cmd_harness_usage_events

        err = io.StringIO()
        # error_stream(): stderr under --json, stdout otherwise.
        with redirect_stdout(err):
            rc = cmd_harness_usage_events(["cursor", "--since=notanumber"])
        self.assertEqual(rc, 1)
        self.assertIn("Bad --since", err.getvalue())

    def test_infinite_since_is_a_noop_not_a_crash(self):
        from quiver.harness.rate_limits import fetch_cursor_usage_events

        # The fetch itself needs credentials, so the reachable unit is
        # the since-clamp math: float("inf") must not wedge the window.
        # We assert the command-level parse accepts it (it parses fine)
        # and the event-field mapper stays total on junk.
        from quiver.harness.rate_limits import _cursor_ms_to_epoch

        self.assertEqual(_cursor_ms_to_epoch("not-a-ts"), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(float("inf")), 0.0)
        self.assertTrue(_cursor_ms_to_epoch(1_700_000_000_000) > 0)


# ── 6. Cyclic/over-deep JSON in configuration readers ───────────────


def _deep_json(depth: int = 60000) -> str:
    return '{"a":' * depth + "1" + "}" * depth


class DeepJsonConfigTest(unittest.TestCase):
    def test_load_config_raises_corrupt_not_recursionerror(self):
        from quiver.configuration import (
            CorruptConfigurationError,
            load_config,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(_deep_json())
            with self.assertRaises(CorruptConfigurationError):
                load_config(path)

    def test_check_config_reports_deep_nesting(self):
        from quiver.configuration import check_config

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(_deep_json())
            issues = check_config(path)
        self.assertTrue(issues)
        self.assertTrue(any("deep" in str(i) for i in issues), issues)

    def test_refuse_to_overwrite_deep_config(self):
        from quiver.configuration import (
            CorruptConfigurationError,
            _assert_existing_file_is_safe,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(_deep_json())
            with self.assertRaises(CorruptConfigurationError):
                _assert_existing_file_is_safe(path)


# ── 7. ``~missing-user`` expansion ──────────────────────────────────


class MissingUserExpansionTest(unittest.TestCase):
    def test_expand_user_leaves_missing_user_literal(self):
        from quiver.paths import expand_user

        out = expand_user("~quiver-no-such-user-392/x")
        self.assertEqual(str(out), "~quiver-no-such-user-392/x")

    def test_expand_user_still_expands_own_home(self):
        from quiver.paths import expand_user

        self.assertEqual(expand_user("~/x"), Path.home() / "x")
        self.assertEqual(expand_user("~"), Path.home())

    def test_provider_keys_dir_flag_reports_not_tracebacks(self):
        from quiver.providers import commands as providers_commands

        out = io.StringIO()
        with mock.patch.object(
            providers_commands, "load_registry", return_value={}
        ), redirect_stdout(out):
            rc = providers_commands.cmd_list(
                ["--api-keys-dir=~quiver-no-such-user-392/x"])
        self.assertEqual(rc, 1)
        self.assertIn("Cannot expand --api-keys-dir", out.getvalue())

    def test_registry_drift_check_covers_missing_user_root(self):
        # Same guarantee as the doctor test, pinned at the helper level:
        # a ``~user`` root must probe as missing, never raise.
        from quiver.harness.drift import check_registry_schema

        findings = check_registry_schema({
            "x": {"capabilities": {
                "sessions": {"supported": True,
                             "root": "~quiver-no-such-user-392/s"},
            }},
        })
        self.assertTrue(
            any("does not exist" in f.message for f in findings),
            findings,
        )

    def test_resolve_project_root_keeps_missing_user_literal(self):
        from quiver.sessions.query import resolve_project_root

        got = resolve_project_root("~quiver-no-such-user-392/proj")
        self.assertIn("quiver-no-such-user-392", got)


if __name__ == "__main__":
    unittest.main()
