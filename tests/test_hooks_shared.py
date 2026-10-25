"""The shared hook tier, foreign rows, and the hook CRUD surface.

Covered end to end against throwaway homes: the contract registry and its
registry-override merge, the events: header parser, deploy_targets'
coverage rule, apply_deploy's generated files and pruning, the generated
wrapper actually shimmming a cursor payload into the normalized shape
(and the mandatory-JSON failure paths), plan_hooks' reserved-dir and
.disabled handling, foreign-row detection, and delete/disable/enable/
sync through cmd_hooks.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from quiver.hooks import commands as hooks_commands
from quiver.hooks import shared as _shared
from quiver.hooks.contracts import CONTRACTS, contract_for
from quiver.hooks.inventory import foreign_rows, hook_rows
from quiver.hooks.settings import declarations
from quiver.init.hooks import plan_hooks
from quiver.init.layout import hooks_dir


def _home(tmp: str) -> Path:
    home = Path(tmp)
    (home / ".quiver").mkdir()
    return home


def _registry(home: Path, data: dict) -> None:
    path = home / ".quiver" / "config" / "harness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _shared_hook(home: Path, name: str, body: str) -> Path:
    path = _shared.shared_dir(home) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _run(home: Path, *args: str) -> tuple[int, str]:
    out = StringIO()
    with mock.patch.object(Path, "home", staticmethod(lambda: home)):
        with redirect_stdout(out):
            code = hooks_commands.cmd_hooks(list(args))
    return code, _plain(out.getvalue())


# A shared hook that records the normalized payload it received, then
# prints the output the caller baked into {out}.
def _recorder(out_path: Path, out: str = "") -> str:
    return (
        "# events: [PreToolUse, Stop]\n"
        "import json, sys\n"
        f"data = json.load(sys.stdin)\n"
        f"open({str(out_path)!r}, 'w').write(json.dumps(data))\n"
        + (f"print({out!r})\n" if out else "")
    )


class ScriptEventsTest(unittest.TestCase):
    def test_bracket_and_bare_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            a = _shared_hook(home, "a.py",
                             "# events: [PreToolUse, Stop]\nx = 1\n")
            b = _shared_hook(home, "b.py",
                             "# events: UserPromptSubmit\nx = 1\n")
            c = _shared_hook(home, "c.py",
                             '"""\nevents: [SessionStart]\n"""\nx = 1\n')
            self.assertEqual(_shared.script_events(a),
                             ["PreToolUse", "Stop"])
            self.assertEqual(_shared.script_events(b), ["UserPromptSubmit"])
            self.assertEqual(_shared.script_events(c), ["SessionStart"])

    def test_garbage_reads_as_undeclared_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            bad = _shared_hook(home, "bad.py",
                               "# events: see the docstring\nx = 1\n")
            none = _shared_hook(home, "none.py", "import sys\n")
            self.assertEqual(_shared.script_events(bad), [])
            self.assertEqual(_shared.script_events(none), [])
            self.assertEqual(
                _shared.script_events(home / "gone.py"), [])


class ContractForTest(unittest.TestCase):
    def test_defaults_come_from_the_table(self):
        home = Path("/h")
        claude = contract_for({}, "claude", home)
        self.assertEqual(claude.root, Path(".claude/hooks"))
        self.assertEqual(claude.schema, "grouped")
        self.assertTrue(claude.shim_able)
        cursor = contract_for({}, "cursor", home)
        self.assertEqual(cursor.schema, "flat")
        self.assertTrue(cursor.must_print_json)
        self.assertEqual(
            cursor.event_map["preToolUse"], "PreToolUse")

    def test_registry_archetype_and_overrides_merge(self):
        home = Path("/h")
        reg = {"x": {"capabilities": {"hooks": {
            "supported": True, "contract": "claude",
            "root": "~/.x/hooks",
            "event_map": {"Weird": "Stop"}}}}}
        contract = contract_for(reg, "x", home)
        self.assertEqual(contract.root, home / ".x/hooks")
        self.assertEqual(contract.schema, "grouped")
        self.assertIn("PreToolUse", contract.events)
        self.assertEqual(contract.event_map["Weird"], "Stop")

    def test_supported_false_and_unknown_mechanisms(self):
        reg = {"claude": {"capabilities": {"hooks": {"supported": False}}}}
        self.assertFalse(contract_for(reg, "claude", Path("/h")).supported)
        for name in ("opencode", "pi", "kilo"):
            self.assertFalse(contract_for({}, name, Path("/h")).shim_able)
        for name in ("freebuff", "artemis", "grok", "hermes"):
            self.assertEqual(
                contract_for({}, name, Path("/h")).mechanism, "none")

    def test_gemini_ms_and_copilot_worktree_marks(self):
        home = Path("/h")
        self.assertEqual(
            contract_for({}, "gemini", home).timeout_unit, "ms")
        self.assertEqual(
            contract_for({}, "copilot", home).mechanism, "worktree")
        self.assertEqual(
            contract_for({}, "cline", home).root,
            Path("Documents/Cline/Hooks"))


class DeployTargetsTest(unittest.TestCase):
    def test_shared_script_deploys_where_events_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            (home / ".cursor").mkdir()
            script = _shared_hook(
                home, "gate.py",
                "# events: [PreToolUse]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                targets = _shared.deploy_targets(home, {})
            by_harness = {t.harness: t for t in targets}
            self.assertIn("claude", by_harness)
            self.assertIn("cursor", by_harness)
            self.assertEqual(
                by_harness["claude"].covered, ("PreToolUse",))
            self.assertIn("preToolUse", by_harness["cursor"].covered)
            self.assertEqual(
                by_harness["claude"].wrapper,
                _shared.generated_dir(home, "claude") / "gate.py")
            # Plugin/eventfile mechanisms can never take a wrapper.
            self.assertNotIn("opencode", by_harness)
            self.assertNotIn("cline", by_harness)

    def test_no_header_no_deploy_and_disabled_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "quiet.py", "import sys\n")
            script = _shared_hook(
                home, "off.py", "# events: [Stop]\nimport sys\n")
            _shared.set_disabled(
                _shared.shared_dir(home), script.name, True)
            with mock.patch("shutil.which", return_value=None):
                self.assertEqual(_shared.deploy_targets(home, {}), [])
            # include_disabled still surfaces the row for list.
            with mock.patch("shutil.which", return_value=None):
                targets = _shared.deploy_targets(
                    home, {}, include_disabled=True)
            self.assertEqual([t.source.name for t in targets], ["off.py"])


class ApplyDeployTest(unittest.TestCase):
    def test_writes_runtime_and_stubs_then_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            script = _shared_hook(
                home, "gate.py",
                "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                written, pruned = _shared.apply_deploy(home, {})
            runtime = _shared.runtime_file(home)
            stub = _shared.generated_dir(home, "claude") / "gate.py"
            self.assertIn(runtime, written)
            self.assertIn(stub, written)
            self.assertTrue(runtime.read_text().startswith(
                "# GENERATED by quiver"))
            text = stub.read_text()
            self.assertIn("GENERATED by quiver", text)
            self.assertIn(str(script), text)
            self.assertTrue(os.access(stub, os.X_OK))
            # Idempotent: nothing changes on a second run.
            with mock.patch("shutil.which", return_value=None):
                written2, pruned2 = _shared.apply_deploy(home, {})
            self.assertEqual((written2, pruned2), ([], []))
            # The shared source going away prunes the stub.
            script.unlink()
            with mock.patch("shutil.which", return_value=None):
                written3, pruned3 = _shared.apply_deploy(home, {})
            self.assertIn(stub, pruned3)
            self.assertFalse(stub.exists())
            self.assertFalse(
                _shared.generated_dir(home, "claude").exists())

    def test_stale_marker_files_prune_but_user_files_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            gdir = _shared.generated_dir(home, "claude")
            gdir.mkdir(parents=True)
            stale = gdir / "stale.py"
            stale.write_text("# GENERATED by quiver — old\n")
            keep = gdir / "mine.py"
            keep.write_text("# mine\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            self.assertFalse(stale.exists())
            self.assertTrue(keep.exists())


class ShimEndToEndTest(unittest.TestCase):
    """The generated stub + runtime really translate a harness payload."""

    def _run_stub(self, stub: Path, payload: dict, *argv: str):
        return subprocess.run(
            [sys.executable, str(stub)] + list(argv),
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=15)

    def test_cursor_payload_normalizes_through_the_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cursor").mkdir()
            capture = home / "capture.json"
            _shared_hook(home, "gate.py",
                         _recorder(capture, '{"ok": true}'))
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            stub = _shared.generated_dir(home, "cursor") / "gate.py"
            proc = self._run_stub(stub, {
                "hook_event_name": "preToolUse",
                "conversation_id": "conv-1",
                "workspace_roots": ["/work/root"],
                "tool_name": "Shell",
                "tool_response": {"exit": 0},
            })
            self.assertEqual(proc.returncode, 0, proc.stderr)
            seen = json.loads(capture.read_text())
            self.assertEqual(seen["event"], "PreToolUse")
            self.assertEqual(seen["harness"], "cursor")
            self.assertEqual(seen["session_id"], "conv-1")
            self.assertEqual(seen["cwd"], "/work/root")
            self.assertEqual(seen["tool_output"], {"exit": 0})
            self.assertEqual(seen["raw"]["conversation_id"], "conv-1")
            self.assertIn("timestamp", seen)
            # cursor's flat contract gets exactly the key it reads.
            self.assertEqual(json.loads(proc.stdout),
                             {"permission": "allow"})

    def test_claude_payload_and_normalized_output_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            capture = home / "capture.json"
            _shared_hook(home, "gate.py",
                         _recorder(capture, '{"decision": "approve"}'))
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            stub = _shared.generated_dir(home, "claude") / "gate.py"
            proc = self._run_stub(stub, {
                "hook_event_name": "Stop",
                "session_id": "s-1", "cwd": "/tmp",
                "stop_hook_active": True})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            seen = json.loads(capture.read_text())
            self.assertEqual(seen["event"], "Stop")
            self.assertEqual(seen["session_id"], "s-1")
            self.assertIs(seen["stop_hook_active"], True)
            # Grouped contract: the script's JSON passes through verbatim.
            self.assertEqual(json.loads(proc.stdout),
                             {"decision": "approve"})

    def test_crash_still_prints_valid_default_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cursor").mkdir()
            (home / ".gemini").mkdir()
            _shared_hook(
                home, "boom.py",
                "# events: [PreToolUse, Stop, BeforeTool]\n"
                "raise RuntimeError('dead')\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            cur = self._run_stub(
                _shared.generated_dir(home, "cursor") / "boom.py",
                {"hook_event_name": "preToolUse",
                 "conversation_id": "c"})
            # Must-print contracts always exit 0 with valid JSON — the
            # failure fails open, the stderr still carries the crash.
            self.assertEqual(cur.returncode, 0, cur.stderr)
            self.assertEqual(json.loads(cur.stdout),
                             {"permission": "allow"})
            self.assertIn("dead", cur.stderr)
            gem = self._run_stub(
                _shared.generated_dir(home, "gemini") / "boom.py",
                {"hook_event_name": "BeforeTool", "session_id": "g"})
            self.assertEqual(gem.returncode, 0, gem.stderr)
            self.assertEqual(json.loads(gem.stdout), {})

    def test_exit_2_blocks_with_the_contracts_json_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cursor").mkdir()
            (home / ".claude").mkdir()
            _shared_hook(
                home, "deny.py",
                "# events: [PreToolUse, Stop]\n"
                "import sys\nsys.stderr.write('no dice')\nsys.exit(2)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            cur = self._run_stub(
                _shared.generated_dir(home, "cursor") / "deny.py",
                {"hook_event_name": "preToolUse",
                 "conversation_id": "c"})
            self.assertEqual(cur.returncode, 0, cur.stderr)
            self.assertEqual(json.loads(cur.stdout),
                             {"permission": "deny", "continue": False})
            # Claude's own contract keeps the native exit-2 + stderr.
            cla = self._run_stub(
                _shared.generated_dir(home, "claude") / "deny.py",
                {"hook_event_name": "PreToolUse", "session_id": "s"})
            self.assertEqual(cla.returncode, 2)
            self.assertIn("no dice", cla.stderr)

    def test_missing_runtime_falls_back_to_default_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cursor").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            _shared.runtime_file(home).unlink()
            stub = _shared.generated_dir(home, "cursor") / "gate.py"
            proc = self._run_stub(stub, {
                "hook_event_name": "stop", "conversation_id": "c"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout),
                             {"permission": "allow", "continue": True})

    def test_runtime_translates_directly(self):
        # The runtime is importable: unit-test the translate edges that
        # subprocess coverage finds awkward.
        from quiver.hooks import shim_runtime

        flat = {"harness": "cursor", "schema": "flat",
                "event_map": {"preToolUse": "PreToolUse"},
                "must_print_json": True}
        norm, native = shim_runtime.normalize(
            {"hook_event_name": "preToolUse", "conversation_id": "x",
             "workspace_roots": ["/w"], "tool_response": 1}, flat, "")
        self.assertEqual((norm["event"], norm["session_id"],
                          norm["cwd"], norm["tool_output"]),
                         ("PreToolUse", "x", "/w", 1))
        self.assertEqual(
            shim_runtime.translate(flat, "preToolUse",
                                   '{"hookSpecificOutput":'
                                   ' {"permissionDecision": "deny"}}'),
            '{"permission": "deny"}')
        self.assertEqual(
            shim_runtime.translate(flat, "stop",
                                   '{"decision": "block"}'),
            '{"continue": false}')
        grouped_must = {"schema": "grouped", "must_print_json": True}
        self.assertEqual(
            shim_runtime.translate(grouped_must, "BeforeTool", "junk"),
            "{}")
        self.assertEqual(
            json.loads(shim_runtime.translate(
                grouped_must, "BeforeTool",
                '{"hookSpecificOutput": {"permissionDecision": "deny"}}')
            )["decision"], "deny")


class PlanHooksReservedTest(unittest.TestCase):
    def test_shared_and_runtime_dirs_are_not_harnesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\n")
            (hooks_dir(home) / "_runtime").mkdir(parents=True)
            (hooks_dir(home) / "_runtime/shim.py").write_text("x")
            labels = {s.label for s in plan_hooks(home)}
            self.assertNotIn("shared", labels)
            self.assertNotIn("_runtime", labels)

    def test_disabled_marker_plans_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/hooks").mkdir(parents=True)
            src = hooks_dir(home) / "claude/off.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            _shared.set_disabled(
                hooks_dir(home) / "claude", "off.py", True)
            [status] = plan_hooks(home)
            self.assertEqual((status.state, status.source),
                             ("ignored", src))

    def test_generated_dir_links_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
                statuses = plan_hooks(home)
            gen = [s for s in statuses
                   if s.source and s.source.name == "_generated"]
            self.assertEqual(len(gen), 1)
            self.assertEqual(gen[0].state, "create")
            self.assertEqual(
                gen[0].path, home / ".claude/hooks/_generated")


class CursorAndMuseSettingsTest(unittest.TestCase):
    def test_cursor_flat_schema_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            path = home / ".cursor/hooks.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "version": 1,
                "hooks": {
                    "preToolUse": [
                        {"command": "/x/gate", "timeout": 30,
                         "matcher": "Shell"},
                        {"command": "python3 /y/other.py"},
                    ],
                    "stop": "not-a-list",
                }}))
            decls = declarations(path, "cursor", {}, home)
            self.assertEqual(len(decls), 2)
            self.assertEqual(decls[0].event, "preToolUse")
            self.assertEqual(decls[0].matcher, "Shell")
            self.assertEqual(decls[1].command, "python3 /y/other.py")

    def test_muse_managed_hooks_path_indirection(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            managed = home / "managed/hooks.json"
            managed.parent.mkdir(parents=True)
            managed.write_text(json.dumps({"hooks": {
                "Stop": [{"hooks": [{"command": "x.sh"}]}]}}))
            path = home / ".muse/settings.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "managed_hooks_path": "~/managed/hooks.json",
                "managed_hooks_env_vars": ["A"]}))
            [decl] = declarations(path, "muse", {}, home)
            self.assertEqual((decl.command, decl.settings),
                             ("x.sh", managed))


class ForeignRowsTest(unittest.TestCase):
    def test_unclaimed_dest_file_and_declared_command_are_foreign(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".claude/hooks"
            root.mkdir(parents=True)
            other = root / "herdr.sh"
            other.write_text("x")
            # Ours: a linked hub script — must not count as foreign.
            src = hooks_dir(home) / "claude/guard.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            (root / "guard.py").symlink_to(src)
            # A declared command pointing outside ~/.quiver is foreign.
            tool = home / "tools/bridge"
            tool.parent.mkdir(parents=True)
            tool.write_text("x")
            (home / ".claude/settings.json").write_text(json.dumps({
                "hooks": {"Stop": [{"hooks": [
                    {"command": str(tool)},
                    {"command": "bash ~/.claude/hooks/herdr.sh"}]}]}}))
            statuses = plan_hooks(home)
            declared = {"claude": declarations(
                home / ".claude/settings.json", "claude", {}, home)}
            rows = foreign_rows(home, {}, statuses, declared)
            by_name = {r.script: r for r in rows}
            self.assertIn("herdr.sh", by_name)
            self.assertIn("bridge", by_name)
            self.assertNotIn("guard.py", by_name)
            row = by_name["herdr.sh"]
            self.assertEqual(row.tier, "foreign")
            self.assertEqual(row.source, other)
            self.assertTrue(row.declared)
            self.assertIs(row.enabled, True)
            self.assertIn("PreToolUse", row.events_supported)


class CrudCommandsTest(unittest.TestCase):
    def test_disable_then_enable_round_trips_the_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            dest = home / ".claude/hooks/gate.py"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(src)
            code, out = _run(home, "disable", "gate")
            self.assertEqual(code, 0, out)
            self.assertIn("gate.py",
                          _shared.disabled_names(src.parent))
            self.assertFalse(dest.is_symlink())
            self.assertTrue(src.is_file())   # source stays
            code, out = _run(home, "enable", "gate")
            self.assertEqual(code, 0, out)
            self.assertTrue(dest.is_symlink())
            self.assertFalse(
                _shared.disabled_names(src.parent))

    def test_disable_refuses_foreign(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".claude/hooks"
            root.mkdir(parents=True)
            (root / "foreign.sh").write_text("x")
            code, out = _run(home, "disable", "foreign.sh")
            self.assertEqual(code, 1)
            self.assertIn("foreign", out)

    def test_delete_needs_confirmation_and_keeps_the_stanza(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            settings = home / ".claude/settings.json"
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps({"hooks": {
                "Stop": [{"hooks": [{"command": "gate.py"}]}]}}))
            code, out = _run(home, "delete", "gate")
            self.assertEqual(code, 1)
            self.assertTrue(src.exists())   # nothing deleted without --yes
            code, out = _run(home, "delete", "gate", "--yes")
            self.assertEqual(code, 0, out)
            self.assertFalse(src.exists())
            # The declaration stanza is never ours to delete.
            self.assertIn("gate.py", settings.read_text())
            self.assertIn("stanza", out)

    def test_delete_foreign_requires_the_flag_and_takes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".claude/hooks"
            root.mkdir(parents=True)
            target = root / "other.sh"
            target.write_text("x")
            code, out = _run(home, "delete", "other.sh", "--yes")
            self.assertEqual(code, 1)
            self.assertIn("--foreign", out)
            self.assertTrue(target.exists())
            code, out = _run(
                home, "delete", "other.sh", "--foreign", "--yes")
            self.assertEqual(code, 0, out)
            self.assertFalse(target.exists())

    def test_sync_generates_links_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(home, "sync")
            self.assertEqual(code, 0, out)
            stub = _shared.generated_dir(home, "claude") / "gate.py"
            self.assertTrue(stub.is_file())
            link = home / ".claude/hooks/_generated"
            self.assertTrue(link.is_symlink())
            self.assertTrue((link / "gate.py").is_file())
            code, out = _run(home, "sync")
            self.assertEqual(code, 0, out)

    def test_list_shows_shared_and_foreign_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            root = home / ".claude/hooks"
            root.mkdir(parents=True, exist_ok=True)
            (root / "stray.sh").write_text("x")
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(home, "list")
            self.assertEqual(code, 0, out)
            self.assertIn("shared", out)
            self.assertIn("foreign", out)
            code, out = _run(home, "list", "--json")
            rows = {r["name"]: r for r in
                    json.loads(out)["data"]["hooks"]}
            self.assertEqual(rows["gate.py"]["tier"], "shared")
            self.assertEqual(rows["stray.sh"]["tier"], "foreign")
            self.assertEqual(rows["gate.py"]["events_supported"],
                             ["Stop"])

    def test_hook_rows_marks_disabled_and_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/off.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            _shared.set_disabled(
                hooks_dir(home) / "claude", "off.py", True)
            statuses = plan_hooks(home)
            [row] = hook_rows(statuses, {}, home, {})
            self.assertIs(row.enabled, False)
            self.assertIn("PreToolUse", row.events_supported)


if __name__ == "__main__":
    unittest.main()
