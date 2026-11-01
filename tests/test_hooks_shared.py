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
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from swe.hooks import commands as hooks_commands
from swe.hooks import shared as _shared
from swe.hooks.contracts import CONTRACTS, contract_for
from swe.hooks.inventory import foreign_rows, hook_rows
from swe.hooks.settings import declarations, harness_declarations
from swe.init.hooks import plan_hooks
from swe.init.layout import hooks_dir


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
        from swe.hooks import shim_runtime

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

    def _cursor_rows(self, home: Path, *commands: str) -> list[str]:
        settings = home / ".cursor/hooks.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"version": 1, "hooks": {
            "stop": [{"command": c} for c in commands]}}))
        declared = {"cursor": harness_declarations({}, "cursor", home)}
        rows = foreign_rows(home, {}, plan_hooks(home), declared)
        return sorted(r.script for r in rows if r.harness == "cursor")

    def _orca(self, home: Path) -> Path:
        hook = home / ".orca/agent-hooks/cursor-hook.sh"
        hook.parent.mkdir(parents=True)
        hook.write_text("#!/bin/sh\n")
        return hook

    def test_a_shell_wrapper_rows_the_script_not_the_shell(self):
        # orca's form: a test, then /bin/sh on the script. /bin/sh is a
        # real file, so it used to row as a foreign hook named "sh".
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            self._orca(home)
            got = self._cursor_rows(
                home,
                'if [ -f "${HOME-}/.orca/agent-hooks/cursor-hook.sh" ]; '
                'then /bin/sh "${HOME-}/.orca/agent-hooks/cursor-hook.sh" '
                '|| :; fi')
            self.assertEqual(got, ["cursor-hook.sh"])

    def test_sh_dash_c_rows_the_script_its_body_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            hook = self._orca(home)
            inner = (f"if [ -f '{hook}' ]; then X='{{}}' /bin/sh '{hook}'; "
                     "fi")
            got = self._cursor_rows(home, f"/bin/sh -c {shlex.quote(inner)}")
            self.assertEqual(got, ["cursor-hook.sh"])

    def test_a_tested_file_is_not_an_executed_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            hook = self._orca(home)
            conf = home / "conf.json"
            conf.write_text("{}")
            got = self._cursor_rows(home, f"[ -f {conf} ] && {hook}")
            self.assertEqual(got, ["cursor-hook.sh"])

    def test_a_quoted_executable_with_spaces_keeps_its_whole_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            helper = home / "Apps/Agent Relay.app/Contents/Agent Relay Helper"
            helper.parent.mkdir(parents=True)
            helper.write_text("x")
            got = self._cursor_rows(
                home, f"{shlex.quote(str(helper))} relay stop")
            self.assertEqual(got, ["Agent Relay Helper"])

    def test_a_hooks_root_dir_rows_only_when_something_runs_from_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".cursor/hooks"
            (root / "state").mkdir(parents=True)
            (root / "state/continual-learning.json").write_text("{}")
            (root / "bundle").mkdir()
            (root / "bundle/gate.py").write_text("x")
            got = self._cursor_rows(home, f"python3 {root}/bundle/gate.py")
            self.assertEqual(got, ["bundle"])

    def test_a_dir_hook_run_through_sh_dash_c_still_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".cursor/hooks"
            (root / "bundle").mkdir(parents=True)
            (root / "bundle/gate.py").write_text("x")
            got = self._cursor_rows(
                home, f"sh -c 'python3 {root}/bundle/gate.py'")
            self.assertEqual(got, ["bundle"])

    def test_a_state_file_passed_as_an_argument_does_not_row_its_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            hook = self._orca(home)
            state = home / ".cursor/hooks/state"
            state.mkdir(parents=True)
            (state / "x.json").write_text("{}")
            got = self._cursor_rows(home, f"{hook} {state}/x.json")
            self.assertEqual(got, ["cursor-hook.sh"])

    def test_a_quoted_path_with_spaces_inside_sh_dash_c(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            gate = home / "My Hooks/gate.py"
            gate.parent.mkdir(parents=True)
            gate.write_text("x")
            got = self._cursor_rows(home, f"sh -c \"python3 '{gate}'\"")
            self.assertEqual(got, ["gate.py"])

    def test_a_test_behind_an_assignment_or_wrapper_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            hook = self._orca(home)
            conf = home / "conf.json"
            conf.write_text("{}")
            for prefix in ("MODE=check test", "env -i MODE=x test"):
                got = self._cursor_rows(
                    home, f"{prefix} -f {conf} && {hook}")
                self.assertEqual(got, ["cursor-hook.sh"], prefix)

    def test_a_test_line_does_not_swallow_the_hook_on_the_next_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            hook = self._orca(home)
            got = self._cursor_rows(home, f"test -f {hook}\n{hook}")
            self.assertEqual(got, ["cursor-hook.sh"])


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

    def test_disable_a_shared_hook_prunes_its_wrappers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
                code, out = _run(home, "disable", "gate")
            stub = _shared.generated_dir(home, "claude") / "gate.py"
            self.assertEqual(code, 0, out)
            self.assertFalse(stub.exists())
            self.assertFalse(
                _shared.generated_dir(home, "claude").exists())

    def test_disable_scopes_by_harness_and_reports_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for name in ("claude", "droid"):
                src = hooks_dir(home) / f"{name}/gate.py"
                src.parent.mkdir(parents=True)
                src.write_text("x")
            code, out = _run(home, "disable", "gate", "--harness=claude")
            self.assertEqual(code, 0, out)
            self.assertEqual(
                _shared.disabled_names(hooks_dir(home) / "claude"),
                {"gate.py"})
            self.assertEqual(
                _shared.disabled_names(hooks_dir(home) / "droid"), set())
            code, out = _run(home, "disable", "nope")
            self.assertEqual(code, 1)
            self.assertIn("No hook named nope", out)
            code, out = _run(home, "enable", "--harness=claude")
            self.assertEqual(code, 1)
            self.assertIn("Usage", out)

    def test_delete_harness_scope_and_tty_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for name in ("claude", "droid"):
                src = hooks_dir(home) / f"{name}/gate.py"
                src.parent.mkdir(parents=True)
                src.write_text("x")
            code, out = _run(
                home, "delete", "gate", "--harness=droid", "--yes")
            self.assertEqual(code, 0, out)
            self.assertFalse(
                (hooks_dir(home) / "droid/gate.py").exists())
            self.assertTrue(
                (hooks_dir(home) / "claude/gate.py").exists())
            # An interactive y works when stdin is a tty.
            with mock.patch.object(
                    sys.stdin, "isatty", return_value=True), \
                    mock.patch("swe.prompt.read_line",
                               return_value="y") as rl:
                code, out = _run(home, "delete", "gate")
            self.assertEqual(code, 0, out)
            self.assertFalse(
                (hooks_dir(home) / "claude/gate.py").exists())
            rl.assert_called_once()

    def test_sync_flag_errors_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            code, out = _run(home, "sync", "extra")
            self.assertEqual(code, 1)
            self.assertIn("Unexpected", out)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(home, "sync")
            self.assertEqual(code, 0, out)
            self.assertIn("wrote", out)

    def test_delete_shared_prunes_wrapper_and_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/hooks").mkdir(parents=True)
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
                link = home / ".claude/hooks/_generated"
                link.symlink_to(
                    _shared.generated_dir(home, "claude"),
                    target_is_directory=True)
                code, out = _run(home, "delete", "gate", "--yes")
            self.assertEqual(code, 0, out)
            self.assertFalse(
                _shared.generated_dir(home, "claude").exists())
            self.assertFalse(link.is_symlink())


class NewSharedCommandTest(unittest.TestCase):
    def test_new_shared_writes_template_and_names_targets_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(home, "new", "shared", "watcher")
            self.assertEqual(code, 0, out)
            script = _shared.shared_dir(home) / "watcher.py"
            self.assertTrue(script.is_file())
            self.assertTrue(os.access(script, os.X_OK))
            text = script.read_text()
            self.assertIn("# events: [PreToolUse]", text)
            self.assertIn("watcher:", text)
            # One declare line per covered harness, not per event.
            self.assertEqual(out.count(
                "claude: ~/.claude/hooks/_generated/watcher.py"), 1)
            self.assertIn("claude", out)
            code, out = _run(home, "new", "shared", "watcher")
            self.assertEqual(code, 1)
            self.assertIn("already exists", out)

    def test_new_shared_json_and_dormant(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(home, "new", "shared", "solo", "--json")
            self.assertEqual(code, 0, out)
            data = json.loads(out)["data"]
            self.assertEqual(data["tier"], "shared")
            self.assertEqual(data["deploys_to"], [])
            self.assertTrue(data["path"].endswith("solo.py"))

    def test_project_dotdirs_in_cwd_are_not_an_install(self):
        # A repo with .claude/ and .cursor/ is not a harness install; the
        # contract roots are home-relative and must not resolve on cwd.
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as project:
            home = _home(tmp)
            for dotdir in (".claude", ".cursor"):
                (Path(project) / dotdir / "hooks").mkdir(parents=True)
            cwd = os.getcwd()
            os.chdir(project)
            try:
                with mock.patch("shutil.which", return_value=None):
                    code, out = _run(
                        home, "new", "shared", "solo", "--json")
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["data"]["deploys_to"], [])


class ShimRuntimeUnitTest(unittest.TestCase):
    """main()/_split/edges the subprocess e2e can't reach for coverage."""

    def _main(self, argv, stdin_text="{}"):
        from swe.hooks import shim_runtime
        out, err = StringIO(), StringIO()
        with mock.patch("sys.stdin", StringIO(stdin_text)), \
                mock.patch("sys.stdout", out), \
                mock.patch("sys.stderr", err):
            rc = shim_runtime.main(argv)
        return rc, out.getvalue(), err.getvalue()

    @staticmethod
    def _flat():
        return json.dumps({
            "harness": "cursor", "schema": "flat",
            "event_map": {"preToolUse": "PreToolUse", "stop": "Stop"},
            "must_print_json": True})

    @staticmethod
    def _grouped():
        return json.dumps({"harness": "claude", "schema": "grouped"})

    _script_n = 0

    def _script(self, tmp, body):
        ShimRuntimeUnitTest._script_n += 1
        p = Path(tmp) / f"s{ShimRuntimeUnitTest._script_n}.py"
        p.write_text(body)
        return str(p)

    def test_split_needs_separator_and_a_script(self):
        from swe.hooks import shim_runtime
        for argv in (["--contract", "{}", "s.py"],
                     ["--contract", "{}", "--"],
                     ["--"]):
            with self.assertRaises(SystemExit):
                shim_runtime._split(argv)
        contract, rest = shim_runtime._split(
            ["--contract", '{"a": 1}', "--", "s.py", "preToolUse"])
        self.assertEqual((contract, rest), ({"a": 1}, ["s.py", "preToolUse"]))

    def test_grouped_success_passes_script_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._script(
                tmp, "import json, sys\n"
                     "d = json.load(sys.stdin)\n"
                     "print(json.dumps({'seen': d['event']}))\n")
            rc, out, err = self._main(
                ["--contract", self._grouped(), "--", script],
                '{"hook_event_name": "Stop", "session_id": "s"}')
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(out), {"seen": "Stop"})

    def test_must_print_failures_always_answer_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            crash = self._script(tmp, "raise RuntimeError('x')\n")
            junk = self._script(
                tmp, "import sys\nsys.stdout.write('not json')\n")
            deny = self._script(tmp, "import sys\nsys.exit(2)\n")
            argv = ["--contract", self._flat(), "--"]
            rc, out, _ = self._main(argv + [crash],
                                    '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "allow"})
            rc, out, _ = self._main(argv + [junk],
                                    '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "allow"})
            rc, out, _ = self._main(argv + [deny],
                                    '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out),
                             {"permission": "deny", "continue": False})
            # A deliberate JSON answer survives a non-zero exit.
            late = self._script(
                tmp, "import sys\n"
                     "sys.stdout.write('{\\\"permission\\\": \\\"ask\\\"}')\n"
                     "sys.exit(1)\n")
            rc, out, _ = self._main(argv + [late],
                                    '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "ask"})

    def test_exec_error_and_bad_stdin_fail_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._script(tmp, "import sys\nsys.exit(0)\n")
            with mock.patch("subprocess.run", side_effect=OSError("no")):
                rc, out, _ = self._main(
                    ["--contract", self._flat(), "--", script],
                    "this is not json")
            self.assertEqual(rc, 0)
            # Unknown event fails open with continue, still valid JSON.
            self.assertEqual(json.loads(out), {"continue": True})
            # On a permission event the default is an explicit allow.
            with mock.patch("subprocess.run", side_effect=OSError("no")):
                rc, out, _ = self._main(
                    ["--contract", self._flat(), "--", script],
                    '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "allow"})

    def test_argv_event_names_a_payload_without_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._script(
                tmp, "import json, sys\n"
                     "d = json.load(sys.stdin)\n"
                     "print(d['event'])\n")
            rc, out, _ = self._main(
                ["--contract", self._grouped(), "--", script, "Stop"],
                '{"session_id": "s"}')
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "Stop")

    def test_argv_never_overrides_the_payload_event(self):
        # A declaration passing an option must not rewrite a real event.
        with tempfile.TemporaryDirectory() as tmp:
            script = self._script(
                tmp, "import json, sys\n"
                     "d = json.load(sys.stdin)\n"
                     "print(d['event'])\n")
            rc, out, _ = self._main(
                ["--contract", self._grouped(), "--",
                 script, "--verbose"],
                '{"hook_event_name": "PreToolUse", "session_id": "s"}')
            self.assertEqual(rc, 0)
            self.assertEqual(out.strip(), "PreToolUse")

    def test_continue_false_denies_a_permission_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = self._script(
                tmp, "import sys\nsys.stdout.write('{\\\"continue\\\": false}')\n")
            rc, out, _ = self._main(
                ["--contract", self._flat(), "--", script],
                '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "deny"})

    def test_exit2_beats_stdout_and_stdout_is_revalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            # {} + exit 2 still blocks — stdout cannot weaken the signal.
            empty = self._script(
                tmp, "import sys\n"
                     "sys.stdout.write('{}')\nsys.exit(2)\n")
            rc, out, _ = self._main(
                ["--contract", self._flat(), "--", empty],
                '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out),
                             {"permission": "deny", "continue": False})
            # A mid-write crash leaves a JSON prefix: not forwarded raw.
            torn = self._script(
                tmp, "import sys\n"
                     "sys.stdout.write('{\\\"permission')\nsys.exit(1)\n")
            rc, out, _ = self._main(
                ["--contract", self._flat(), "--", torn],
                '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "allow"})
            # A deliberate JSON answer still survives a nonzero exit.
            deny = self._script(
                tmp, "import sys\n"
                     "sys.stdout.write('{\\\"permission\\\": \\\"deny\\\"}')\n"
                     "sys.exit(1)\n")
            rc, out, _ = self._main(
                ["--contract", self._flat(), "--", deny],
                '{"hook_event_name": "preToolUse"}')
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out), {"permission": "deny"})

    def test_blocked_keeps_script_json_but_forces_the_decision(self):
        from swe.hooks import shim_runtime
        grouped = {"schema": "grouped"}
        self.assertEqual(
            json.loads(shim_runtime._blocked(
                grouped, "x", '{"reason": "no", "continue": false}')),
            {"reason": "no", "continue": False, "decision": "block"})
        self.assertEqual(
            shim_runtime._blocked(grouped, "x", "{}"),
            '{"decision": "block"}')
        self.assertEqual(
            shim_runtime._blocked(grouped, "x", "junk"),
            '{"decision": "block"}')
        self.assertEqual(
            json.loads(shim_runtime._blocked(
                {"schema": "flat"}, "x", '{"permission": "allow"}')),
            {"permission": "deny", "continue": False})

    def test_normalize_aliases_and_defaults(self):
        from swe.hooks import shim_runtime
        norm, native = shim_runtime.normalize(
            {"hook_event_name": "X", "error_message": "boom",
             "source": "resume", "agent_transcript": "/t"},
            {"harness": "h", "fields": {"tool_name": ("tool",)}}, "")
        self.assertEqual((norm["error"], norm["session_source"],
                          norm["agent_transcript_path"]),
                         ("boom", "resume", "/t"))
        norm2, _ = shim_runtime.normalize(
            {"hook_event_name": "X", "tool": "Shell"},
            {"harness": "h", "fields": {"tool_name": ("tool",)}}, "")
        self.assertEqual(norm2["tool_name"], "Shell")

    def test_default_and_block_output_shapes(self):
        from swe.hooks import shim_runtime
        flat = {"schema": "flat"}
        self.assertEqual(
            shim_runtime.default_output(flat, "preToolUse"),
            '{"permission": "allow"}')
        self.assertEqual(
            shim_runtime.default_output(flat, "sessionEnd"),
            '{"continue": true}')
        self.assertEqual(
            shim_runtime.default_output({"schema": "grouped"}, "x"), "{}")
        self.assertEqual(
            shim_runtime.block_output(flat, "x"),
            '{"permission": "deny", "continue": false}')
        self.assertEqual(
            shim_runtime.block_output({"schema": "grouped"}, "x"),
            '{"decision": "block"}')

    def test_permission_of_each_answer_shape(self):
        from swe.hooks import shim_runtime
        cases = [
            ({"hookSpecificOutput": {"permissionDecision": "ask"}}, "ask"),
            ({"decision": "approve"}, "allow"),
            ({"decision": "block"}, "deny"),
            ({"decision": "ask"}, "ask"),
            ({"permission": "deny"}, "deny"),
            ({"unrelated": 1}, None),
            ({}, None),
        ]
        for payload, want in cases:
            self.assertEqual(
                shim_runtime._permission_of(payload), want, payload)

    def test_translate_flat_non_permission_events(self):
        from swe.hooks import shim_runtime
        flat = {"schema": "flat"}
        self.assertEqual(
            shim_runtime.translate(flat, "stop", '{"continue": false}'),
            '{"continue": false}')
        self.assertEqual(
            shim_runtime.translate(flat, "stop", '{"decision": "block"}'),
            '{"continue": false}')
        self.assertEqual(
            shim_runtime.translate(flat, "stop", "junk"),
            '{"continue": true}')


class ContractOverrideTest(unittest.TestCase):
    def test_archetype_cursor_and_cap_merges(self):
        home = Path("/h")
        reg = {"x": {"capabilities": {"hooks": {
            "supported": True, "contract": "cursor",
            "events": ["alpha"], "timeout_unit": "ms"}}}}
        contract = contract_for(reg, "x", home)
        self.assertEqual(contract.schema, "flat")
        self.assertTrue(contract.must_print_json)
        self.assertEqual(contract.events, ("alpha",))
        self.assertEqual(contract.timeout_unit, "ms")
        # The archetype's map still maps leftover names.
        self.assertIn("preToolUse", contract.event_map)

    def test_shim_and_supported_switches(self):
        reg = {"claude": {"capabilities": {"hooks": {
            "supported": True, "shim": False}}}}
        contract = contract_for(reg, "claude", Path("/h"))
        self.assertFalse(contract.shim_able)
        reg = {"claude": {"capabilities": {"hooks": {
            "supported": False}}}}
        contract = contract_for(reg, "claude", Path("/h"))
        self.assertFalse(contract.supported)
        self.assertFalse(contract.shim_able)

    def test_settings_and_root_and_plugin_files(self):
        home = Path("/h")
        reg = {"claude": {"capabilities": {"hooks": {
            "supported": True, "root": "~/.cl/hooks",
            "settings": ["~/.cl/hooks.json"]}}}}
        contract = contract_for(reg, "claude", home)
        self.assertEqual(contract.root, home / ".cl/hooks")
        self.assertEqual(contract.settings, (home / ".cl/hooks.json",))

    def test_hooks_caps_and_normalized_event_edges(self):
        from swe.hooks.contracts import hooks_caps, normalized_event
        self.assertEqual(hooks_caps({"x": "nope"}, "x"), {})
        self.assertEqual(hooks_caps({"x": {}}, "x"), {})
        self.assertEqual(hooks_caps({}, "x"), {})
        contract = contract_for({}, "claude", Path("/h"))
        self.assertEqual(normalized_event(contract, "PreToolUse"),
                         "PreToolUse")
        self.assertEqual(normalized_event(contract, "Weird"), "Weird")


class PendingDeployTest(unittest.TestCase):
    def test_pending_counts_missing_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                # Runtime + one stub missing.
                self.assertEqual(_shared.pending_deploy(home, {}), 2)
                _shared.apply_deploy(home, {})
                self.assertEqual(_shared.pending_deploy(home, {}), 0)
                stub = _shared.generated_dir(home, "claude") / "gate.py"
                stub.write_text("corrupted")
                self.assertEqual(_shared.pending_deploy(home, {}), 1)

    def test_dormant_shared_script_rows_as_skipped(self):
        from swe.hooks.inventory import shared_rows
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _shared_hook(home, "quiet.py", "import sys\n")
            statuses = plan_hooks(home)
            [row] = shared_rows(home, {}, statuses, {})
            self.assertEqual(row.harness, "shared")
            self.assertEqual(row.state, "skipped")
            self.assertIn("no events", row.detail)


class ApplyHookStatusBranchTest(unittest.TestCase):
    def test_conflict_with_force_backs_up_then_links(self):
        from swe.init.hooks import apply_hook_status
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("hub")
            dest = home / ".claude/hooks/gate.py"
            dest.parent.mkdir(parents=True)
            dest.write_text("mine")
            [status] = plan_hooks(home)
            self.assertEqual(status.state, "conflict")
            self.assertEqual(apply_hook_status(status, home), "blocked")
            self.assertTrue(dest.is_file() and not dest.is_symlink())
            self.assertEqual(
                apply_hook_status(status, home, force=True), "linked")
            self.assertTrue(dest.is_symlink())
            # The pre-replacement copy lands under backups_dir_for with a
            # flattened name.
            backups = list(
                (home / ".quiver/backups").glob("*gate*"))
            self.assertTrue(backups)

    def test_absorb_replaces_an_identical_copy_with_backup(self):
        from swe.init.hooks import apply_hook_status
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("same bytes")
            dest = home / ".claude/hooks/gate.py"
            dest.parent.mkdir(parents=True)
            dest.write_text("same bytes")
            [status] = plan_hooks(home)
            self.assertEqual(status.state, "absorb")
            self.assertEqual(apply_hook_status(status, home), "linked")
            self.assertTrue(dest.is_symlink())

    def test_disabled_unlink_only_takes_our_symlink(self):
        from swe.init.hooks import apply_hook_status
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            other = home / "other.py"
            other.write_text("x")
            dest = home / ".claude/hooks/gate.py"
            dest.parent.mkdir(parents=True)
            # A link that does not resolve to our source is left alone.
            dest.symlink_to(other)
            _shared.set_disabled(src.parent, "gate.py", True)
            [status] = plan_hooks(home)
            self.assertEqual(
                apply_hook_status(status, home), "ignored")
            self.assertTrue(dest.is_symlink())
            # Pointing at ours: the link comes off.
            dest.unlink()
            dest.symlink_to(src)
            self.assertEqual(
                apply_hook_status(status, home), "unlinked")
            self.assertFalse(dest.is_symlink())


class ManagedSettingsTest(unittest.TestCase):
    def test_a_symlinked_settings_file_is_a_note_not_drift(self):
        from swe.hooks.inventory import hook_findings
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            real = home / "nix/settings.json"
            real.parent.mkdir(parents=True)
            real.write_text(json.dumps({"hooks": {}}))
            link = home / ".claude/settings.json"
            link.parent.mkdir(parents=True)
            link.symlink_to(real)
            findings = hook_findings(home)
            kinds = {f.kind for f in findings}
            self.assertIn("managed", kinds)
            # Doctor keeps the note out of its actionable warnings.
            from swe.harness.drift import check_hook_drift
            self.assertFalse(
                [f for f in check_hook_drift(home)
                 if "managed" in f.message])


class InitSharedDeployTest(unittest.TestCase):
    def test_check_reports_pending_and_a_run_writes(self):
        from swe.init.commands import run_init
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                run = run_init(
                    home, check_only=True, quiet=True, ask=False)
            self.assertTrue(
                any("generated hook file(s)" in s
                    for s in run.scaffold), run.scaffold)
            self.assertFalse(_shared.runtime_file(home).exists())
            with mock.patch("shutil.which", return_value=None):
                run = run_init(home, quiet=True, ask=False, yes=True)
            self.assertTrue(_shared.runtime_file(home).exists())
            self.assertTrue(
                any("shared hooks" in s for s in run.scaffold))


class ReviewFixesTest(unittest.TestCase):
    """Regression coverage for the review fixes on the shared tier."""

    # --- _write_if_changed ownership (shared.py #3) -------------------

    def test_unowned_file_at_wrapper_path_is_backed_up_then_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            gdir = _shared.generated_dir(home, "claude")
            gdir.mkdir(parents=True)
            mine = gdir / "gate.py"
            mine.write_text("# my own file\n")
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                written, _p = _shared.apply_deploy(home, {})
            self.assertIn(mine, written)
            self.assertIn("GENERATED by quiver", mine.read_text())
            backups = list((home / ".quiver/backups").glob("*gate*"))
            self.assertTrue(backups)
            self.assertEqual(backups[0].read_text(), "# my own file\n")

    def test_symlink_at_wrapper_path_never_gets_written_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            gdir = _shared.generated_dir(home, "claude")
            gdir.mkdir(parents=True)
            real = home / "real.py"
            real.write_text("# somebody else's file\n")
            link = gdir / "gate.py"
            link.symlink_to(real)
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
            self.assertFalse(link.is_symlink())
            self.assertIn("GENERATED by quiver", link.read_text())
            self.assertEqual(real.read_text(), "# somebody else's file\n")
            self.assertTrue(
                list((home / ".quiver/backups").glob("*gate*")))

    # --- archived harnesses keep their wrappers (shared.py #5) ---------

    def test_archived_harness_generated_dir_is_not_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            gdir = _shared.generated_dir(home, "claude")
            gdir.mkdir(parents=True)
            stale = gdir / "old.py"
            stale.write_text("# GENERATED by quiver — do not edit.\nx\n")
            registry = {"claude": {"state": "archived"}}
            with mock.patch("shutil.which", return_value=None):
                written, pruned = _shared.apply_deploy(home, registry)
            self.assertEqual(pruned, [])
            self.assertTrue(stale.is_file())
            self.assertEqual(_shared.pending_deploy(home, registry), 0)

    # --- --harness cannot scope shared ops (commands.py #6) ------------

    def test_scoped_toggle_and_delete_refuse_shared_only_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            for verb in ("disable", "delete"):
                code, out = _run(
                    home, verb, "gate", "--harness=claude", "--yes")
                self.assertEqual(code, 1, (verb, out))
                self.assertIn("--harness", out)
            self.assertTrue(
                (_shared.shared_dir(home) / "gate.py").is_file())

    def test_scoped_toggle_still_hits_the_harness_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            # claude has its OWN gate.py beside the shared one.
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            code, out = _run(home, "disable", "gate", "--harness=claude")
            self.assertEqual(code, 0, out)
            self.assertEqual(
                _shared.disabled_names(hooks_dir(home) / "claude"),
                {"gate.py"})
            self.assertEqual(
                _shared.disabled_names(_shared.shared_dir(home)), set())

    # --- sync stops when .linkignore cannot be read (commands.py #7) ---

    def test_sync_refuses_an_unreadable_linkignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            bad = home / ".quiver/.linkignore"
            bad.mkdir()  # a directory cannot be read as a file
            code, out = _run(home, "sync")
            self.assertEqual(code, 1, out)
            self.assertIn("linkignore", out.lower())
            self.assertFalse(_shared.runtime_file(home).exists())
            code, out = _run(home, "sync", "--json")
            self.assertEqual(code, 1)
            self.assertFalse(_shared.runtime_file(home).exists())

    def test_enable_and_disable_refuse_an_unreadable_linkignore(self):
        """The re-plan after the marker reloaded .linkignore unguarded, so
        an unreadable one escaped as a LinkIgnoreError traceback (after the
        marker was already written)."""
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            (home / ".quiver/.linkignore").mkdir()
            for verb in ("disable", "enable"):
                code, out = _run(home, verb, "gate")
                self.assertEqual(code, 1, out)
                self.assertIn("linkignore", out.lower())
                self.assertEqual(_shared.disabled_names(src.parent), set())

    # --- failed deletes report themselves (commands.py #8) -------------

    def test_a_failed_delete_is_reported_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            source = _shared.shared_dir(home)
            source.mkdir(parents=True)
            gate = source / "gate.py"
            gate.write_text("# events: [Stop]\n")
            source.chmod(0o500)  # unlink fails on the read-only dir
            try:
                code, out = _run(home, "delete", "gate", "--yes")
            finally:
                source.chmod(0o700)
            self.assertEqual(code, 1, out)
            self.assertIn("failed", out)
            self.assertTrue(gate.is_file())

    def test_shared_delete_counts_one_source_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/hooks").mkdir(parents=True)
            (home / ".cursor/hooks").mkdir(parents=True)
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(home, "delete", "gate", "--yes")
            self.assertEqual(code, 0, out)
            self.assertEqual(out.count("deleted"), 1, out)

    # --- _generated is machinery, not a hook row (inventory.py #9) -----

    def test_generated_dir_is_not_a_row_or_a_delete_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
                code, out = _run(home, "list")
            self.assertNotIn("_generated", out)
            code, out = _run(home, "delete", "_generated", "--yes")
            self.assertEqual(code, 1)
            self.assertIn("No hook named", out)
            self.assertTrue(
                _shared.generated_dir(home, "claude").is_dir())

    # --- edit refuses foreign rows (commands.py #10) -------------------

    def test_edit_refuses_a_foreign_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            stray = home / ".claude/hooks/stray.sh"
            stray.parent.mkdir(parents=True)
            stray.write_text("#!/bin/sh\n")
            code, out = _run(home, "edit", "stray.sh")
            self.assertEqual(code, 1, out)
            self.assertIn("foreign", out)

    # --- only the executed file is a foreign hook (inventory.py #11) ---

    def test_file_arguments_do_not_row_as_foreign_hooks(self):
        from swe.hooks.inventory import foreign_rows
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            other = home / "other"
            other.mkdir()
            (other / "gate.py").write_text("x")
            (other / "policy.json").write_text("{}")
            settings = home / ".claude/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"hooks": {"PreToolUse": [{
                "matcher": "*", "hooks": [{"type": "command",
                    "command": f"python3 {other}/gate.py "
                               f"{other}/policy.json"}]}]}}))
            statuses = plan_hooks(home)
            declared = {"claude": harness_declarations(
                {}, "claude", home)}
            rows = foreign_rows(home, {}, statuses, declared)
            self.assertEqual([r.script for r in rows], ["gate.py"])

    # --- same foreign file rows per harness (inventory.py #12) ---------

    def test_one_foreign_script_rows_under_each_declaring_harness(self):
        from swe.hooks.inventory import foreign_rows
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            outside = home / "elsewhere/gate.py"
            outside.parent.mkdir(parents=True)
            outside.write_text("x")
            for name, cfg in (("claude", ".claude/settings.json"),
                              ("cursor", ".cursor/hooks.json")):
                settings = home / cfg
                settings.parent.mkdir(parents=True, exist_ok=True)
                if name == "claude":
                    settings.write_text(json.dumps({"hooks": {"Stop": [{
                        "matcher": "*", "hooks": [{"type": "command",
                            "command": str(outside)}]}]}}))
                else:
                    settings.write_text(json.dumps({
                        "version": 1,
                        "hooks": {"stop": [{"command": str(outside)}]}}))
            statuses = plan_hooks(home)
            declared = {
                "claude": harness_declarations({}, "claude", home),
                "cursor": harness_declarations({}, "cursor", home),
            }
            rows = foreign_rows(home, {}, statuses, declared)
            got = sorted((r.harness, r.script) for r in rows)
            self.assertEqual(
                got, [("claude", "gate.py"), ("cursor", "gate.py")])

    # --- paths beat basenames across tiers (inventory.py #13) ----------

    def test_a_declared_path_claims_only_its_own_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/hooks").mkdir(parents=True)
            src = hooks_dir(home) / "claude/gate.py"
            src.parent.mkdir(parents=True)
            src.write_text("x")
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            settings = home / ".claude/settings.json"
            settings.write_text(json.dumps({"hooks": {"Stop": [{
                "matcher": "*", "hooks": [{"type": "command",
                    "command": "~/.claude/hooks/gate.py"}]}]}}))
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
                # The linked state is what "undeclared" fires on.
                (home / ".claude/hooks/_generated").symlink_to(
                    _shared.generated_dir(home, "claude"),
                    target_is_directory=True)
                code, out = _run(home, "check")
            self.assertEqual(code, 1, out)
            self.assertIn("_generated", out)
            self.assertIn("no settings file declares it", out)
            # The claude row IS declared; the shared wrapper is not.
            rows, _registry = hooks_commands._rows(home)
            by_tier = {r.tier: r for r in rows if r.script == "gate.py"}
            self.assertTrue(by_tier["harness"].declared)
            self.assertFalse(by_tier["shared"].declared)

    # --- pending_deploy counts writes and prunes (shared.py #14) -------

    def test_pending_counts_stale_runtime_and_prunes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude").mkdir()
            _shared_hook(home, "gate.py",
                         "# events: [Stop]\nimport sys\nsys.exit(0)\n")
            with mock.patch("shutil.which", return_value=None):
                _shared.apply_deploy(home, {})
                # A stale runtime rewrites.
                _shared.runtime_file(home).write_text("# old\n")
                # And a stale marked wrapper prunes.
                gdir = _shared.generated_dir(home, "claude")
                (gdir / "gone.py").write_text(
                    "# GENERATED by quiver — do not edit.\nx\n")
                self.assertEqual(_shared.pending_deploy(home, {}), 2)

    # --- the stanza speaks the target's contract (commands.py #15) -----

    def test_new_gemini_gets_beforetool_and_ms(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".gemini").mkdir()
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(
                    home, "new", "gemini", "watcher", "--json")
            self.assertEqual(code, 0, out)
            body = json.loads(json.loads(out)["data"]["stanza"])
            hook = body["hooks"]["BeforeTool"][0]["hooks"][0]
            self.assertEqual(hook["timeout"], 10000)
            self.assertNotIn("PreToolUse", body["hooks"])

    def test_new_shared_cursor_gets_the_flat_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cursor").mkdir()
            (home / ".cursor/hooks").mkdir()
            with mock.patch("shutil.which", return_value=None):
                code, out = _run(
                    home, "new", "shared", "watcher", "--json")
            self.assertEqual(code, 0, out)
            stanza = json.loads(out)["data"]["stanza"]
            body = json.loads(stanza)
            self.assertEqual(body["version"], 1)
            entry = body["hooks"]["preToolUse"][0]
            self.assertEqual(entry["command"].split("/")[-1],
                             "watcher.py")
            self.assertNotIn("matcher", entry)


if __name__ == "__main__":
    unittest.main()
