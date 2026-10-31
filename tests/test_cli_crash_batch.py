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
        import time

        # A fresh timestamp so the freshness check passes and the count
        # conversion guard itself is what rejects the entry.
        got = self._load({
            "windows": {"all": {"cached_at": time.time(),
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

    def test_overflowing_cached_at_is_a_miss(self):
        # json.dumps writes a 400-digit int verbatim; float() on it
        # raises OverflowError, which used to escape the guard.
        got = self._load({
            "windows": {"all": {"cached_at": 10 ** 400,
                                "counts": {"claude": 3}}}})
        self.assertIsNone(got)

    def test_overflowing_count_is_a_miss(self):
        import time

        # json can carry 1e999 -> float inf; int(inf) raises
        # OverflowError, not ValueError. Fresh timestamp so the guard
        # itself, not the staleness check, rejects the entry.
        got = self._load({
            "windows": {"all": {"cached_at": time.time(),
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
                # NVM_DIR wins over the home argument — pin it so an
                # ambient developer setting can't deflect the probe.
                with mock.patch.dict(
                    os.environ, {"NVM_DIR": str(home / ".nvm")}
                ):
                    self.assertEqual(nvm_bin_dirs(home), [])
            finally:
                versions.chmod(stat.S_IRWXU)

    def test_nvm_bin_dirs_survives_unsearchable_nvm_parent(self):
        """Lock the parent, not the versions dir: stat() on
        ``.nvm/versions/node`` then raises EACCES inside ``is_dir()``
        itself, before the iterdir guard can run."""
        from quiver.harness.path_health import nvm_bin_dirs

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            nvm_dir = home / ".nvm"
            versions = nvm_dir / "versions" / "node"
            (versions / "v22.0.0" / "bin").mkdir(parents=True)
            nvm_dir.chmod(0)
            try:
                if os.access(nvm_dir, os.X_OK):
                    self.skipTest("running as a user that ignores modes")
                with mock.patch.dict(
                    os.environ, {"NVM_DIR": str(nvm_dir)}
                ):
                    self.assertEqual(nvm_bin_dirs(home), [])
            finally:
                nvm_dir.chmod(stat.S_IRWXU)

    def test_nvm_bin_dirs_skips_unstatable_version_dir(self):
        """iterdir succeeds but ``child/bin`` cannot be stat()'d."""
        from quiver.harness.path_health import nvm_bin_dirs

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            versions = home / ".nvm" / "versions" / "node"
            version = versions / "v22.0.0"
            (version / "bin").mkdir(parents=True)
            version.chmod(0)
            try:
                if os.access(version, os.X_OK):
                    self.skipTest("running as a user that ignores modes")
                with mock.patch.dict(
                    os.environ, {"NVM_DIR": str(home / ".nvm")}
                ):
                    self.assertEqual(nvm_bin_dirs(home), [])
            finally:
                version.chmod(stat.S_IRWXU)

    def test_dangling_symlink_scan_survives_unstatable_dir(self):
        """is_dir() itself raises EACCES on Python 3.11-3.13 when a
        parent lacks +x. The mode bit alone cannot prove it (3.14+
        returns False instead), so the raise is patched in."""
        from quiver.harness.drift import check_dangling_symlinks

        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            with mock.patch.object(
                Path, "is_dir",
                side_effect=PermissionError(13, "Permission denied"),
            ):
                findings = check_dangling_symlinks([locked])
        self.assertTrue(
            any("cannot be scanned" in f.message for f in findings),
            findings,
        )

    def test_dangling_symlink_scan_skips_unstatable_entries(self):
        """A directory with +r but no +x lists names it cannot lstat().
        The PermissionError is patched in for the same reason as above:
        mode bits express it only on the Pythons that still raise."""
        from quiver.harness.drift import check_dangling_symlinks

        with tempfile.TemporaryDirectory() as tmp:
            listed = Path(tmp) / "listed"
            listed.mkdir()
            (listed / "dangling").symlink_to(listed / "gone")
            with mock.patch.object(
                Path, "is_symlink",
                side_effect=PermissionError(13, "Permission denied"),
            ):
                findings = check_dangling_symlinks([listed])
        # No crash, and nothing fabricated: the entry could not be
        # classified, so it reports nothing at all.
        self.assertEqual(findings, [])


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

    def test_fields_map_survives_overflowing_cents(self):
        from quiver.harness.rate_limits import _usage_event_fields

        # A 400-digit integer parses fine from JSON but float() cannot
        # convert it — math.isfinite() used to take the OverflowError.
        mapped = _usage_event_fields({
            "timestamp": "1700000000000",
            "tokenUsage": {"totalCents": 10 ** 400},
        })
        self.assertIsNotNone(mapped)
        self.assertIsNone(mapped["cost_usd"])

    def test_bad_since_flag_reports_not_tracebacks(self):
        from quiver.harness.commands import cmd_harness_usage_events

        err = io.StringIO()
        # error_stream(): stderr under --json, stdout otherwise.
        with redirect_stdout(err):
            rc = cmd_harness_usage_events(["cursor", "--since=notanumber"])
        self.assertEqual(rc, 1)
        self.assertIn("Bad --since", err.getvalue())

    def test_cursor_timestamps_stay_total_on_junk_and_inf(self):
        # The event-field mapper reads Cursor's millisecond timestamps;
        # junk and infinity map to 0 rather than raising or overflowing.
        from quiver.harness.rate_limits import _cursor_ms_to_epoch

        self.assertEqual(_cursor_ms_to_epoch("not-a-ts"), 0.0)
        self.assertEqual(_cursor_ms_to_epoch(float("inf")), 0.0)
        self.assertTrue(_cursor_ms_to_epoch(1_700_000_000_000) > 0)


# ── 6. Cyclic/over-deep JSON in configuration readers ───────────────


def _deep_json(depth: int = 300000) -> str:
    # 300k frames, not 60k: the C scanner's recursion budget is measured in
    # remaining C stack, so on 3.15+ 60k nests parse without ever raising.
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


# ── 8. Guard-branch pin-down ───────────────────────────────────────
#
# codecov/patch counts the defensive branches this change adds. Each
# test below forces exactly the hostile input the guard exists for, so
# the except clause is exercised rather than merely plausible.


class PathGuardBranchesTest(unittest.TestCase):
    def test_find_mcps_survives_missing_user_view_path(self):
        from quiver.find import commands as find_commands
        from quiver.find import mcps as find_mcps

        view = find_mcps.ToolView(
            name="claude", path="~quiver-no-such-user-392/x",
            present={"srv"})
        hub = find_mcps.HubView(servers={"srv": {"command": "echo"}})
        with mock.patch.object(find_mcps, "hub_view", return_value=hub), \
                mock.patch.object(find_mcps, "tool_views",
                                  return_value=[view]), \
                mock.patch.object(find_mcps, "scan_configs",
                                  return_value=[]), \
                mock.patch.object(find_mcps, "unmanaged",
                                  return_value={}), \
                mock.patch.object(find_commands, "load_registry_if_present",
                                  return_value={}):
            with redirect_stdout(io.StringIO()):
                find_commands.cmd_find_mcps()   # must not raise

    def test_doctor_preferred_npm_symlink_loop_falls_back(self):
        from types import SimpleNamespace

        from quiver.harness.doctor import collect_findings

        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.symlink_to(loop)          # resolve() -> RuntimeError
            env = SimpleNamespace(
                node="/x/node", npm="/x/npm",
                global_bin=None, global_bin_on_path=True)
            findings = collect_findings(
                env, str(loop / "npm"), "/usr/bin/npm", [], [], [])
        self.assertTrue(
            any(f.check == "npm.preferred" for f in findings), findings)

    def test_search_dirs_for_command_survives_resolve_loop(self):
        from quiver.harness import path_health

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bindir = home / ".npm-global" / "bin"
            bindir.mkdir(parents=True)
            tool = bindir / "quiver-nosuch-cmd-392"
            tool.write_text("#!/bin/sh\n")
            tool.chmod(0o755)
            with mock.patch.object(
                Path, "resolve", side_effect=RuntimeError("loop")
            ):
                hits = path_health.search_dirs_for_command(
                    "quiver-nosuch-cmd-392", home)
        self.assertEqual(hits, [])

    def test_mcp_registered_paths_missing_user(self):
        from quiver.mcp import discover as mcp_discover

        with mock.patch.object(
            mcp_discover, "get_tool_config",
            return_value={"path": "~quiver-no-such-user-392/x"},
        ):
            self.assertEqual(
                mcp_discover._registered_paths(["claude"]), set())

    def test_providers_info_missing_user_keys_dir(self):
        from quiver.providers import commands as providers_commands

        out = io.StringIO()
        with mock.patch.object(
            providers_commands, "load_registry",
            return_value={"openai": {"name": "OpenAI"}},
        ), mock.patch.object(
            providers_commands, "resolve", return_value="openai",
        ), redirect_stdout(out):
            rc = providers_commands.cmd_info(
                ["openai", "--api-keys-dir=~quiver-no-such-user-392/x"])
        self.assertEqual(rc, 1)
        self.assertIn("Cannot expand --api-keys-dir", out.getvalue())

    def test_resume_session_survives_unenterable_dir(self):
        from quiver.sessions.commands import _resume_session
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked"
            locked.mkdir()
            locked.chmod(0)
            try:
                if os.access(locked, os.X_OK):
                    self.skipTest("running as a user that ignores modes")
                session = Session(
                    timestamp=0, agent="claude", path=str(locked),
                    session_id="s1", tool_name="claude",
                )
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = _resume_session(session)
            finally:
                locked.chmod(stat.S_IRWXU)
        self.assertEqual(rc, 1)
        self.assertIn("Cannot enter", buf.getvalue())

    def test_catalog_root_scan_survives_pathless_entry(self):
        from quiver.skills import catalog_discover

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                catalog_discover, "load_skill_catalogs",
                return_value=[{"label": "x"}],   # no "path" key
            ):
                known = catalog_discover._known_root_paths(
                    Path(tmp), Path(tmp))
        self.assertIsInstance(known, set)

    def test_skill_layout_survives_looping_link_target(self):
        from quiver.skills.layout import enumerate_skill_roots

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # .cursor/skills -> shared/skills (relative), resolving to a
            # real dir so the entry keeps kind="symlink".
            real = home / ".cursor" / "shared" / "skills"
            real.mkdir(parents=True)
            (home / ".cursor" / "skills").symlink_to("shared/skills")
            # A *different* "shared" at cwd is a symlink loop: resolving
            # the raw link text relative to cwd must not crash the scan.
            (home / "shared").symlink_to("shared")
            cwd_before = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.object(
                    __import__("quiver.skills.layout", fromlist=["x"]),
                    "load_skill_catalogs", return_value=[],
                ):
                    entries = enumerate_skill_roots(home=home, cwd=home)
            finally:
                os.chdir(cwd_before)
        cursor = next(e for e in entries if e.label == "cursor")
        self.assertEqual(cursor.kind, "symlink")
        self.assertIsNone(cursor.link_target_label)


class ReportGuardBranchesTest(unittest.TestCase):
    def test_stdout_error_detail_skips_undecodable(self):
        from quiver.reports.runners import _stdout_error_detail

        # Neither the blob nor any reversed line parses as JSON.
        out = _stdout_error_detail("{not json at all")
        self.assertIsInstance(out, str)

    def test_tool_message_keeps_undecodable_arguments(self):
        from quiver.reports.transcripts import _tool_message

        msg = _tool_message("runner", "{not json")
        self.assertIsNotNone(msg)

    def test_read_jsonl_skips_corrupt_lines(self):
        from quiver.reports.transcripts import _read_jsonl

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text("{bad\n{}\n")
            msgs = _read_jsonl(path)
        self.assertIsInstance(msgs, list)

    def test_cline_reader_survives_corrupt_history_file(self):
        from quiver.reports import transcripts
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / ".cline" / "data" / "tasks" / "s1")
            root.mkdir(parents=True)
            (root / "api_conversation_history.json").write_text("{bad")
            session = Session(
                timestamp=0, agent="cline", path=tmp,
                session_id="s1", tool_name="cline",
            )
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                tr = transcripts._read_cline(session)
        self.assertIsNotNone(tr)

    def test_devin_reader_skips_corrupt_node_payload(self):
        import sqlite3

        from quiver.reports import transcripts
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            dbdir = Path(tmp) / ".local" / "share" / "devin" / "cli"
            dbdir.mkdir(parents=True)
            db = dbdir / "sessions.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE sessions (id TEXT, main_chain_id INTEGER)")
            conn.execute(
                "CREATE TABLE message_nodes (node_id INTEGER,"
                " parent_node_id INTEGER, session_id TEXT,"
                " chat_message TEXT)")
            conn.execute(
                "INSERT INTO sessions VALUES ('s1', 1)")
            conn.execute(
                "INSERT INTO message_nodes VALUES (1, NULL, 's1', '{bad')")
            conn.commit()
            conn.close()
            session = Session(
                timestamp=0, agent="devin", path=tmp,
                session_id="s1", tool_name="devin",
            )
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                tr = transcripts._read_devin(session)
        self.assertIsNotNone(tr)

    def test_resume_plan_survives_path_vanishing_between_checks(self):
        from quiver.reports import work as work_mod
        from quiver.reports.models import FollowUp
        from quiver.sessions.models import Session

        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp) / "loop"
            loop.symlink_to(loop)
            session = Session(
                timestamp=1, agent="claude", path=tmp,
                session_id="s1", tool_name="claude",
            )
            follow_up = FollowUp(
                id="f1", text="do it", project_root=tmp,
                source_session_ids=["s1"])
            calls = {"n": 0}

            def flaky_expand_user(value):
                calls["n"] += 1
                if calls["n"] <= 2:
                    return Path(str(value))
                return loop / "x"       # resolve(strict) -> RuntimeError

            with mock.patch.object(
                work_mod, "expand_user", flaky_expand_user
            ):
                with self.assertRaises(work_mod.FollowUpWorkError):
                    work_mod.prepare_follow_up_work(
                        follow_up, mode="resume",
                        session_loader=lambda: [session])


class RateLimitGuardBranchesTest(unittest.TestCase):
    def test_fetch_json_retry_failure_returns_none(self):
        import urllib.error
        import urllib.request
        import ssl

        from quiver.harness import rate_limits as rl

        req = urllib.request.Request("https://example.invalid/")
        first = urllib.error.URLError(ssl.SSLError("verify failed"))
        second = urllib.error.URLError("connection reset")
        with mock.patch.object(
            rl.urllib.request, "urlopen", side_effect=[first, second]
        ), mock.patch.object(rl, "_verified_context",
                             return_value=object()):
            self.assertIsNone(rl._fetch_json(req))

    def test_fetch_codex_corrupt_auth_file(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / ".codex"
            auth.mkdir()
            (auth / "auth.json").write_text("{bad")
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                self.assertIsNone(rl._fetch_codex())

    def test_fetch_codex_overflowing_reset_at(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / ".codex"
            auth.mkdir()
            (auth / "auth.json").write_text(json.dumps(
                {"tokens": {"access_token": "t"}}))
            payload = {
                "rate_limit": {
                    "primary_window": {
                        "reset_at": 10 ** 400,   # float() overflows
                        "used_percent": 5,
                    },
                    "limit_reached": False,
                },
                "plan_type": "plus",
            }
            with mock.patch.dict(os.environ, {"HOME": tmp}), \
                    mock.patch.object(rl, "_fetch_json",
                                      return_value=payload):
                info = rl._fetch_codex()
        self.assertIsNotNone(info)
        self.assertEqual(info.reset_at, 0.0)

    def test_copilot_fields_survive_junk_entitlement(self):
        from quiver.harness.rate_limits import _derive_copilot_fields

        used, limited = _derive_copilot_fields(
            {"percent_remaining": 50, "entitlement": "junk"})
        self.assertEqual(used, 50)
        self.assertFalse(limited)

    def test_claude_credentials_file_corrupt_returns_none(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / ".claude"
            creds.mkdir()
            (creds / ".credentials.json").write_text("{bad")
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                self.assertIsNone(rl._read_claude_credentials_file())

    def test_fetch_claude_skips_window_with_junk_utilization(self):
        from quiver.harness import rate_limits as rl

        payload = {
            "five_hour": {"utilization": "junk"},
            "seven_day": {
                "utilization": 40.0,
                "resets_at": "2026-01-01T00:00:00Z",
            },
        }
        with mock.patch.object(
            rl, "_get_claude_oauth_credentials",
            return_value={"accessToken": "tok"},
        ), mock.patch.object(
            rl, "_load_claude_state", return_value=(None, 0.0, "", 0.0)
        ), mock.patch.object(
            rl, "_fetch_claude_url", return_value=payload
        ), mock.patch.object(rl, "_save_claude_state"):
            info = rl._fetch_claude()
        self.assertIsNotNone(info)
        self.assertEqual(info.used_percent, 40)
        self.assertEqual(info.window, "7d")

    def test_droid_auth_decrypt_survives_nonjson_plaintext(self):
        import base64
        import ctypes

        from quiver.harness import rate_limits as rl

        class _FakeCrypto:
            def EVP_CIPHER_CTX_new(self):
                return 1

            def EVP_aes_256_gcm(self):
                return 1

            def EVP_CipherInit_ex(self, *a):
                return 1

            def EVP_CIPHER_CTX_ctrl(self, *a):
                return 1

            def EVP_CipherUpdate(self, ctx, out, outl, ct, n):
                data = b"this is not json"
                ctypes.memmove(out, data, len(data))
                outl._obj.value = len(data)
                return 1

            def EVP_CipherFinal_ex(self, ctx, fin, finl):
                finl._obj.value = 0
                return 1

            def EVP_CIPHER_CTX_free(self, ctx):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            factory = Path(tmp) / ".factory"
            factory.mkdir()
            b64 = lambda b: base64.b64encode(b)
            (factory / "auth.v2.key").write_bytes(b64(b"k" * 32))
            (factory / "auth.v2.file").write_bytes(
                b64(b"i" * 12) + b":" + b64(b"t" * 16) + b":" + b64(b"ct"))
            with mock.patch.dict(os.environ, {"HOME": tmp}), \
                    mock.patch.object(rl, "_load_libcrypto",
                                      return_value=_FakeCrypto()):
                self.assertIsNone(rl._decrypt_droid_auth_file())

    def test_freebuff_token_corrupt_credentials(self):
        from quiver.harness import rate_limits as rl

        with tempfile.TemporaryDirectory() as tmp:
            creds = Path(tmp) / ".config" / "manicode"
            creds.mkdir(parents=True)
            (creds / "credentials.json").write_text("{bad")
            with mock.patch.dict(os.environ, {"HOME": tmp}):
                self.assertIsNone(rl._get_freebuff_access_token())

    def test_freebuff_quota_junk_glm_fields(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        self.assertIsNone(_parse_freebuff_quota({
            "rateLimitsByModel": {
                "z-ai/glm-5.2": {"limit": "x", "recentCount": 1},
            },
        }))

    def test_freebuff_quota_junk_referral_fields(self):
        from quiver.harness.rate_limits import _parse_freebuff_quota

        self.assertIsNone(_parse_freebuff_quota({
            "referral": {
                "weeklySessionsRemaining": "junk",
                "qualifiedCount": 3,
            },
        }))

    def test_cursor_usage_events_bad_since_is_ignored(self):
        from quiver.harness import rate_limits as rl

        with mock.patch.object(
            rl, "_get_cursor_access_token", return_value="tok"
        ), mock.patch.object(
            rl, "_cursor_token_claims", return_value={"sub": "u"}
        ), mock.patch.object(
            rl, "_cursor_session_cookie", return_value="c"
        ), mock.patch.object(
            rl, "_fetch_json", return_value={"usageEventsDisplay": []}
        ):
            status, events, account = rl.fetch_cursor_usage_events(
                since="junk")
        self.assertEqual(status, "ok")
        self.assertEqual(events, [])
        self.assertEqual(account, "u")

    def test_devin_status_junk_credit_fields(self):
        from quiver.harness.rate_limits import _parse_devin_status

        self.assertIsNone(_parse_devin_status({
            "userStatus": {
                "planStatus": {
                    "planInfo": {
                        "planName": "core",
                        "monthlyPromptCredits": "junk",
                    },
                    "availablePromptCredits": 5,
                },
            },
        }))

    def test_rate_limits_cache_guards(self):
        import time

        from quiver.harness import rate_limits as rl

        now = time.time()
        info = {
            "tool_name": "codex", "used_percent": 1,
            "limit_reached": False, "reset_at": 0.0,
            "plan_type": "x", "window_seconds": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "rate_limits_cache.json"
            with mock.patch.object(rl, "RATE_LIMITS_CACHE_FILE", cache):
                # Non-numeric envelope timestamp: miss, not a traceback.
                cache.write_text(json.dumps(
                    {"cached_at": {"x": 1}, "limits": {"codex": info}}))
                self.assertIsNone(rl._load_cached())
                self.assertEqual(rl._load_cached_no_data(), set())
                # Per-provider timestamp that will not parse: the entry
                # alone is skipped, the others still load.
                cache.write_text(json.dumps({
                    "cached_at": now,
                    "limits": {"a": info, "b": info},
                    "updated_at": {"a": "junk", "b": now},
                }))
                stale, _ts = rl._load_stale_cached()
                self.assertEqual(set(stale), {"b"})


if __name__ == "__main__":
    unittest.main()
