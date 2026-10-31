"""`swe hooks`: the management surface over ~/.quiver/hooks/<harness>/.

The link half comes from ``init.hooks.plan_hooks`` (tested in
test_init_hooks.py); these cover what the domain adds — settings-file
discovery and declaration parsing, the declared-column join, the three
drift checks, the skeleton ``new`` writes, and the command handlers.
"""

import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from quiver.harness import drift
from quiver.hooks import commands as hooks_commands
from quiver.hooks import create as mk
from quiver.hooks.inventory import find_script, hook_findings, script_summary
from quiver.hooks.models import HookRow
from quiver.hooks.settings import (
    basenames_in,
    declarations,
    expand_command_path,
    settings_files,
)
from quiver.init.layout import hooks_dir


def _home(tmp: str) -> Path:
    home = Path(tmp)
    (home / ".quiver").mkdir()
    (home / ".claude").mkdir()
    return home


def _hook(home: Path, harness: str, name: str, body: str = "print('hi')\n") -> Path:
    path = hooks_dir(home) / harness / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _registry(home: Path, data: dict) -> None:
    path = home / ".quiver" / "config" / "harness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _settings(home: Path, rel: str, hooks: dict) -> Path:
    """One settings file in the shared {Event: [...]} shape both harnesses use."""
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": hooks}))
    return path


def _link(home: Path, harness: str, name: str) -> Path:
    dest = home / ".claude/hooks" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(hooks_dir(home) / harness / name)
    return dest


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _run(home: Path, *args: str) -> tuple[int, str]:
    out = StringIO()
    with mock.patch.object(Path, "home", staticmethod(lambda: home)):
        with redirect_stdout(out):
            code = hooks_commands.cmd_hooks(list(args))
    return code, _plain(out.getvalue())


CLAUDE_HOOK = {"command": 'python3 "~/.claude/hooks/guard.py"', "timeout": 10}


class SettingsFilesTest(unittest.TestCase):
    def test_claude_settings_and_local_both_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _settings(home, ".claude/settings.json", {})
            _settings(home, ".claude/settings.local.json", {})
            files = settings_files({}, "claude", home)
            self.assertEqual(
                files,
                [home / ".claude/settings.json",
                 home / ".claude/settings.local.json"],
            )

    def test_codex_hooks_json_from_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".codex").mkdir()
            _settings(home, ".codex/hooks.json", {})
            self.assertEqual(
                settings_files({}, "codex", home), [home / ".codex/hooks.json"]
            )

    def test_probe_beside_a_declared_hooks_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".x").mkdir()
            _settings(home, ".x/settings.json", {})
            reg = {"x": {"capabilities": {"hooks": {"supported": True,
                                                    "root": "~/.x/hooks"}}}}
            self.assertEqual(
                settings_files(reg, "x", home), [home / ".x/settings.json"]
            )

    def test_capability_settings_wins_over_the_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _settings(home, ".claude/settings.json", {})
            _settings(home, "custom/hooks.json", {})
            reg = {"claude": {"capabilities": {"hooks": {
                "supported": True, "settings": "~/custom/hooks.json"}}}}
            self.assertEqual(
                settings_files(reg, "claude", home), [home / "custom/hooks.json"]
            )

    def test_settings_list_and_missing_files_are_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            reg = {"claude": {"capabilities": {"hooks": {
                "supported": True,
                "settings": ["~/gone.json", "~/.claude/settings.json"]}}}}
            self.assertEqual(settings_files(reg, "claude", home), [])

    def test_supported_false_turns_discovery_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _settings(home, ".claude/settings.json", {})
            reg = {"claude": {"capabilities": {"hooks": {"supported": False}}}}
            self.assertEqual(settings_files(reg, "claude", home), [])

    def test_no_root_no_table_no_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            self.assertEqual(settings_files({}, "droid", home), [])


class DeclarationsTest(unittest.TestCase):
    def test_event_matcher_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            path = _settings(home, ".claude/settings.json", {
                "PreToolUse": [{"matcher": "Bash", "hooks": [CLAUDE_HOOK]}],
            })
            [decl] = declarations(path, "claude")
            self.assertEqual((decl.event, decl.matcher, decl.timeout),
                             ("PreToolUse", "Bash", 10))
            self.assertEqual(decl.label(), "PreToolUse:Bash")

    def test_codex_shape_has_no_matcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            path = _settings(home, ".codex/hooks.json", {
                "Stop": [{"hooks": [{"type": "command",
                                      "command": "bash /x/stop.sh"}]}],
            })
            [decl] = declarations(path, "codex")
            self.assertEqual((decl.event, decl.matcher), ("Stop", ""))
            self.assertIsNone(decl.timeout)
            self.assertEqual(decl.label(), "Stop")

    def test_garbage_and_schema_drift_read_as_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            bad = home / ".claude/settings.json"
            bad.write_text("{not json")
            self.assertEqual(declarations(bad, "claude"), [])
            bad.write_text(json.dumps({"hooks": "nope"}))
            self.assertEqual(declarations(bad, "claude"), [])
            bad.write_text(json.dumps({"hooks": {
                "Stop": ["not a group", {"hooks": "nope"},
                         {"hooks": [{"command": 42}, {"command": "ok.py"}]}],
            }}))
            [decl] = declarations(bad, "claude")
            self.assertEqual(decl.command, "ok.py")


class BasenamesTest(unittest.TestCase):
    def test_quoted_paths_and_args(self):
        names = basenames_in('python3 "/Users/x/.claude/hooks/guard.py" '
                             "&& bash './lib.sh' run")
        self.assertIn("guard.py", names)
        self.assertIn("lib.sh", names)

    def test_env_forms_expand_or_stay_unverifiable(self):
        home = Path("/h")
        self.assertEqual(
            expand_command_path("$HOME/.claude/hooks/x.py", home),
            home / ".claude/hooks/x.py",
        )
        self.assertEqual(
            expand_command_path("${HOME-}/.claude/hooks/x.py", home),
            home / ".claude/hooks/x.py",
        )
        self.assertIsNone(
            expand_command_path("$SUPERSET_HOME_DIR/hooks/x.sh", home))
        self.assertIsNone(expand_command_path("hooks/x.sh", home))


class ListCommandTest(unittest.TestCase):
    def test_declared_column_matches_by_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _hook(home, "claude", "other.py")
            _settings(home, ".claude/settings.json", {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{
                    "type": "command",
                    "command": f'python3 "{home}/.claude/hooks/guard.py"',
                    "timeout": 10}]}],
            })
            code, out = _run(home)
            self.assertEqual(code, 0)
            self.assertRegex(out, r"guard\.py\s+claude\s+harness\s+create\s+PreToolUse:Bash")
            self.assertRegex(out, r"other\.py\s+claude\s+harness\s+create\s+-")

    def test_harness_filter_and_alias_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            (home / ".factory").mkdir()
            _hook(home, "droid", "guard.sh")
            _registry(home, {"claude": {"aliases": ["cc"]}})
            code, out = _run(home, "list", "--harness=cc")
            self.assertEqual(code, 0)
            self.assertIn("guard.py", out)
            self.assertNotIn("guard.sh", out)

    def test_bare_flag_means_default_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            (home / ".factory").mkdir()
            _hook(home, "droid", "other.sh")
            code, out = _run(home, "--harness=claude")
            self.assertEqual(code, 0)
            self.assertIn("guard.py", out)
            self.assertNotIn("other.sh", out)

    def test_empty_hub_says_how_to_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(_home(tmp))
            self.assertEqual(code, 0)
            self.assertIn("swe hooks new", out)

    def test_json_rows_carry_declarations(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py",
                  '"""Stop hook: block replies that break the rules."""\n')
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "guard.py"}]}],
            })
            code, out = _run(home, "list", "--json")
            self.assertEqual(code, 0)
            data = json.loads(out)["data"]["hooks"]
            row = data[0]
            # The stable schema the app page consumes.
            for key in ("name", "harness", "tier", "source_path",
                        "dest_path", "link_status", "enabled",
                        "declared", "events_supported", "summary",
                        "detail"):
                self.assertIn(key, row, key)
            self.assertEqual(row["name"], "guard.py")
            self.assertEqual(row["tier"], "harness")
            self.assertIs(row["enabled"], True)
            self.assertEqual(row["declared"][0]["event"], "Stop")
            self.assertTrue(
                row["declared"][0]["file"].endswith("settings.json"))
            self.assertIn("PreToolUse", row["events_supported"])
            self.assertEqual(row["summary"],
                             "Stop hook: block replies that break the rules.")

    def test_unknown_hub_dir_lists_as_one_row_and_check_stays_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for name in ("command_guard.py", "README.md", "rules.json",
                         "test_command_guard.py"):
                _hook(home, "command-guard", name)
            code, out = _run(home, "list", "--json")
            self.assertEqual(code, 0)
            rows = [r for r in json.loads(out)["data"]["hooks"]
                    if r["harness"] == "command-guard"]
            self.assertEqual(
                [(r["name"], r["link_status"]) for r in rows],
                [("command-guard", "skipped")])
            # The app says why it never runs, from the row alone.
            self.assertEqual(rows[0]["detail"], "not a known harness")
            self.assertEqual(
                [f for f in hook_findings(home)
                 if f.harness == "command-guard"], [])

    def test_unknown_hub_dir_does_not_satisfy_another_harness_declaration(self):
        # claude declares a bare command_guard.py it does not have; the
        # bundle's same-named file must not make that warning vanish.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "command-guard", "command_guard.py")
            _settings(home, ".claude/settings.json", {
                "PreToolUse": [{"hooks": [{"command": "command_guard.py"}]}],
            })
            missing = [f for f in hook_findings(home)
                       if f.kind == "declared-missing"]
            self.assertEqual([f.harness for f in missing], ["claude"])
            self.assertIn("command_guard.py", missing[0].message)


class CheckCommandTest(unittest.TestCase):
    def test_declared_missing_script_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "gone.py"}]}],
            })
            kinds = {f.kind for f in hook_findings(home)}
            self.assertIn("declared-missing", kinds)

    def test_declared_basename_living_at_destination_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/hooks").mkdir()
            (home / ".claude/hooks/herdr.sh").write_text("x")
            _settings(home, ".claude/settings.json", {
                "SessionStart": [{"hooks": [{"command": "bash herdr.sh"}]}],
            })
            self.assertEqual(
                [f for f in hook_findings(home)
                 if f.kind == "declared-missing"], [])

    def test_absolute_path_and_unverifiable_vars_are_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            real = home / "tools/keeper.sh"
            real.parent.mkdir(parents=True)
            real.write_text("x")
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [
                    {"command": f"bash {real}"},
                    {"command": "[ -x $SUPERSET_HOME_DIR/hooks/n.sh ] && "
                               "$SUPERSET_HOME_DIR/hooks/n.sh || true"},
                ]}],
            })
            self.assertEqual(
                [f for f in hook_findings(home)
                 if f.kind == "declared-missing"], [])

    def test_declared_dest_path_with_only_a_hub_copy_is_not_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "gate.py")           # hub only, not linked
            _settings(home, ".claude/settings.json", {
                "PreToolUse": [{"hooks": [{
                    "command": "~/.claude/hooks/gate.py"}]}],
            })
            kinds = {f.kind for f in hook_findings(home)}
            # Not linked yet -> unlinked finding, never declared-missing.
            self.assertIn("unlinked", kinds)
            self.assertNotIn("declared-missing", kinds)

    def test_a_dead_literal_path_elsewhere_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "gate.py")           # same basename, wrong place
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "/opt/old/gate.py"}]}],
            })
            kinds = {f.kind for f in hook_findings(home)}
            self.assertIn("declared-missing", kinds)

    def test_one_resolved_does_not_mask_another_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            real = home / "tools/first.sh"
            real.parent.mkdir(parents=True)
            real.write_text("x")
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{
                    "command": f"bash {real} && bash {home}/tools/gone.sh"}]}],
            })
            missing = [f.message for f in hook_findings(home)
                       if f.kind == "declared-missing"]
            self.assertTrue(any("gone.sh" in m for m in missing))
            self.assertFalse(any("first.sh" in m for m in missing))

    def test_a_tested_path_is_not_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{
                    "command": "[ -f opt.sh ] && opt.sh || true"}]}],
            })
            self.assertEqual(
                [f for f in hook_findings(home)
                 if f.kind == "declared-missing"], [])

    def test_quoted_path_with_spaces_is_one_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            real = home / "tools/my guard.sh"
            real.parent.mkdir(parents=True)
            real.write_text("x")
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": f'bash "{real}"'}]}],
            })
            self.assertEqual(
                [f for f in hook_findings(home)
                 if f.kind == "declared-missing"], [])
            self.assertIn("my guard.sh", basenames_in(f'bash "{real}"'))

    def test_broken_link_counts_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            dest = home / ".claude/hooks/guard.py"
            dest.parent.mkdir(parents=True)
            dest.symlink_to(home / ".quiver/hooks/claude/guard.py")  # dangling
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": str(dest)}]}],
            })
            kinds = {f.kind for f in hook_findings(home)}
            self.assertIn("declared-missing", kinds)

    def test_directory_hook_declaration_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            bundle = hooks_dir(home) / "claude/bundle"
            bundle.mkdir(parents=True)
            (bundle / "guard.py").write_text("x")
            dest = home / ".claude/hooks/bundle"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(bundle)
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{
                    "command": 'python3 "$HOME/.claude/hooks/bundle/guard.py"'}]}],
            })
            kinds = {f.kind for f in hook_findings(home)}
            self.assertNotIn("undeclared", kinds)
            self.assertNotIn("declared-missing", kinds)

    def test_linked_but_never_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _link(home, "claude", "guard.py")
            kinds = {f.kind for f in hook_findings(home)}
            self.assertIn("undeclared", kinds)
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "guard.py"}]}],
            })
            kinds = {f.kind for f in hook_findings(home)}
            self.assertNotIn("undeclared", kinds)

    def test_drift_names_the_script_and_the_declaring_file(self):
        """Doctor's source line and remedies read HookDrift's metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            settings = _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "gone.py"}]}],
            })
            [found] = [f for f in hook_findings(home)
                       if f.kind == "declared-missing"]
            self.assertEqual(found.script, "gone.py")
            self.assertEqual(found.settings, (settings,))

            [f] = drift.check_hook_drift(home)
            self.assertEqual(f.area, "hooks")
            self.assertEqual(f.source, "declared in ~/.claude/settings.json")
            self.assertIn("gone.py", f.fix)
            self.assertEqual([r["kind"] for r in f.remedies],
                             ["agent", "steps"])
            self.assertIn("swe doctor", f.remedies[0]["prompt"])

    def test_an_undeclared_link_names_where_it_would_be_declared(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _link(home, "claude", "guard.py")
            settings = _settings(home, ".claude/settings.json", {})
            [found] = [f for f in hook_findings(home)
                       if f.kind == "undeclared"]
            self.assertEqual(found.script, "guard.py")
            self.assertEqual(found.settings, (settings,))

            [f] = [f for f in drift.check_hook_drift(home)
                   if f.message.startswith("guard.py")]
            self.assertEqual(f.source,
                             "would be declared in ~/.claude/settings.json")
            self.assertEqual([r["kind"] for r in f.remedies],
                             ["agent", "steps"])

    def test_an_unlinked_hook_runs_init_as_its_remedy(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")          # create -> unlinked
            [f] = [f for f in drift.check_hook_drift(home)
                   if f.message.startswith("guard.py")]
            self.assertEqual(f.action, "init")
            self.assertEqual(f.remedies[0]["kind"], "action")
            self.assertEqual(f.remedies[0]["action"], "init")

    def test_unlinked_and_skipped_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")          # create -> unlinked
            _hook(home, "droid", "idle.sh")            # no dir -> skipped
            # droid must not prove itself through a real binary on PATH.
            with mock.patch("shutil.which", return_value=None):
                findings = hook_findings(home)
            kinds = {(f.kind, f.harness) for f in findings}
            self.assertIn(("unlinked", "claude"), kinds)
            self.assertNotIn(("unlinked", "droid"), kinds)

    def test_check_prints_and_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            code, out = _run(home, "check")
            self.assertEqual(code, 1)
            self.assertIn("guard.py", out)
            code, out = _run(home)
            self.assertEqual(code, 0)


class DoctorWiringTest(unittest.TestCase):
    def test_findings_arrive_as_doctor_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            findings = drift.check_hook_drift(home)
            self.assertTrue(findings)
            self.assertTrue(all(f.area == "hooks" for f in findings))
            self.assertTrue(all(f.severity == "warn" for f in findings))

    def test_a_broken_inventory_is_one_warning_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _registry(home, {"claude": {}})
            (home / ".quiver/config/harness.json").write_text("{bad json")
            findings = drift.check_hook_drift(home)
            self.assertEqual(len(findings), 1)
            self.assertIn("could not run", findings[0].message)


class NewCommandTest(unittest.TestCase):
    def test_writes_an_executable_skeleton_and_stanza(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude/hooks").mkdir()
            code, out = _run(home, "new", "claude", "gate")
            self.assertEqual(code, 0)
            target = hooks_dir(home) / "claude/gate.py"
            self.assertTrue(target.is_file())
            self.assertTrue(os.access(target, os.X_OK))
            text = target.read_text()
            self.assertIn("json.load(sys.stdin)", text)
            self.assertIn("sys.exit(0)", text)
            # main() is wrapped so an error in user checks fails open —
            # catching Exception leaves a deliberate SystemExit(2) intact.
            self.assertIn("try:\n        main()\n    except Exception:", text)
            self.assertIn("PreToolUse", out)
            self.assertIn("~/.claude/hooks/gate.py", out)

    def test_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "gate.py", "mine\n")
            code, out = _run(home, "new", "claude", "gate")
            self.assertEqual(code, 1)
            self.assertEqual((hooks_dir(home) / "claude/gate.py").read_text(),
                             "mine\n")

    def test_unknown_harness_still_writes_and_notes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            code, out = _run(home, "new", "qwen", "gate")
            self.assertEqual(code, 0)
            self.assertTrue((hooks_dir(home) / "qwen/gate.py").is_file())
            self.assertIn("~/.quiver/hooks/qwen/gate.py", out)

    def test_alias_resolves_to_the_registry_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _registry(home, {"claude": {"aliases": ["cc"]}})
            code, _ = _run(home, "new", "cc", "gate")
            self.assertEqual(code, 0)
            self.assertTrue((hooks_dir(home) / "claude/gate.py").is_file())
            self.assertFalse((hooks_dir(home) / "cc").exists())

    def test_bad_names_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for bad in ("a/b", "..", ".x", "x y"):
                code, _ = _run(home, "new", "claude", bad)
                self.assertEqual(code, 1, bad)
            # Validation runs before the harness dir is made.
            self.assertFalse((hooks_dir(home) / "claude").exists())


class InfoCommandTest(unittest.TestCase):
    def test_shows_source_docstring_state_and_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py",
                  '"""Stop hook: blocks on bad style."""\nimport sys\n')
            _link(home, "claude", "guard.py")
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "guard.py", "timeout": 5}]}],
            })
            code, out = _run(home, "info", "guard")
            self.assertEqual(code, 0)
            self.assertIn("Stop hook: blocks on bad style.", out)
            self.assertIn("linked", out)
            self.assertIn("Stop", out)
            self.assertIn("timeout 5", out)

    def test_unknown_name_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            code, out = _run(home, "info", "nope")
            self.assertEqual(code, 1)
            self.assertIn("nope", out)


class EditCommandTest(unittest.TestCase):
    def _capture_argv(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return type("Done", (), {"returncode": 0})()

        return seen, fake_run

    def test_opens_the_hub_file_with_editor(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = _hook(home, "claude", "guard.py")
            _link(home, "claude", "guard.py")
            seen, fake = self._capture_argv()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch.dict(os.environ,
                                 {"VISUAL": "", "EDITOR": "myeditor --flag"}), \
                 mock.patch("subprocess.run", fake):
                code = hooks_commands.cmd_hooks(["edit", "guard"])
            self.assertEqual(code, 0)
            self.assertEqual(seen["argv"][:2], ["myeditor", "--flag"])
            self.assertEqual(Path(seen["argv"][-1]), src)

    def test_ambiguous_without_tty_requires_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            (home / ".factory").mkdir()
            _hook(home, "droid", "guard.py")
            stdin = mock.Mock()
            stdin.isatty.return_value = False
            out = StringIO()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("sys.stdin", stdin), \
                 redirect_stdout(out):
                code = hooks_commands.cmd_hooks(["edit", "guard"])
            self.assertEqual(code, 1)
            self.assertIn("--harness", _plain(out.getvalue()))
            seen, fake = self._capture_argv()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("subprocess.run", fake):
                code = hooks_commands.cmd_hooks(
                    ["edit", "guard", "--harness=droid"])
            self.assertEqual(code, 0)
            self.assertIn("droid", seen["argv"][-1])

    def test_directory_hook_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (hooks_dir(home) / "claude/bundle").mkdir(parents=True)
            code, out = _run(home, "edit", "bundle")
            self.assertEqual(code, 1)
            self.assertIn("directory", out)


class FindScriptTest(unittest.TestCase):
    def test_exact_and_stem_matches(self):
        def row(script, harness="claude"):
            return HookRow(script, harness, Path(f"/h/{script}"),
                           Path(f"/d/{script}"), "linked", "")

        rows = [row("guard.py"), row("guard.sh", "droid"), row("other.py")]
        self.assertEqual([r.script for r in find_script("guard.py", rows)],
                         ["guard.py"])
        self.assertEqual([r.script for r in find_script("other", rows)],
                         ["other.py"])
        self.assertEqual(
            sorted(r.script for r in find_script("guard", rows)),
            ["guard.py", "guard.sh"])


class SummaryTest(unittest.TestCase):
    def test_python_docstring_and_shell_comment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            py = _hook(home, "claude", "a.py",
                       '"""First line.\n\nRest."""\nx = 1\n')
            sh = _hook(home, "claude", "b.sh",
                       "#!/bin/sh\n# Does a thing.\nset -e\n")
            self.assertEqual(script_summary(py), "First line.")
            self.assertEqual(script_summary(sh), "Does a thing.")

    def test_no_docstring_unreadable_and_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            bare = _hook(home, "claude", "bare.py", "import sys\n")
            txt = _hook(home, "claude", "notes.txt", "just text\n")
            long_sh = _hook(home, "claude", "c.sh",
                            "# one\n# two\n# three\nset -e\n")
            self.assertEqual(script_summary(bare), "")
            self.assertEqual(script_summary(txt), "")
            self.assertEqual(script_summary(home / "gone.py"), "")
            self.assertEqual(script_summary(long_sh), "one two")

    def test_reads_only_the_head(self):
        # The list reads a summary per row on every refresh: a long script
        # is read from its head, and a binary is skipped.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            filler = "x = 1\n" * 10_000  # past the 16 KiB head
            long_py = _hook(home, "claude", "long.py",
                            '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n'
                            '"""Stop hook: guard the gate.\n\nMore."""\n'
                            + filler)
            cut_py = _hook(home, "claude", "cut.py",
                           '"""' + "a" * 20_000 + '"""\n')
            long_sh = _hook(home, "claude", "long.sh",
                            "#!/bin/sh\n# Guards things.\n" + "echo hi\n" * 5_000)
            binary = home / "helper"
            binary.write_bytes(b"\xcf\xfa\xed\xfe\0\0" + b"\0" * 64)
            self.assertEqual(script_summary(long_py), "Stop hook: guard the gate.")
            # A docstring that runs past the head has no closing quotes in
            # it, so there is nothing safe to show.
            self.assertEqual(script_summary(cut_py), "")
            self.assertEqual(script_summary(long_sh), "Guards things.")
            self.assertEqual(script_summary(binary), "")

    def test_list_reads_a_shared_script_once(self):
        # A shared script is a row per harness: one listing reads it once.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            source = _hook(home, "shared", "guard.py", '"""Guards."""\n')
            rows = [HookRow(script="guard.py", harness=h, source=source,
                            dest=home / f".{h}/hooks/_generated/guard.py",
                            state="linked", detail="", declared=[],
                            tier="shared")
                    for h in ("claude", "codex")]
            summaries: dict[Path, str] = {}
            with mock.patch.object(hooks_commands, "script_summary",
                                   wraps=script_summary) as read:
                out = [hooks_commands._row_json(r, summaries) for r in rows]
            self.assertEqual(read.call_count, 1)
            self.assertEqual([o["summary"] for o in out], ["Guards."] * 2)


class DispatchTest(unittest.TestCase):
    def test_help_and_unknown_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            code, out = _run(home, "--help")
            self.assertEqual(code, 0)
            self.assertIn("swe hooks new", out)
            code, out = _run(home, "bogus")
            self.assertEqual(code, 1)
            self.assertIn("Unknown subcommand", out)

    def test_arg_errors_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            for args in (["list", "--harness"],      # value flag needs =x
                         ["list", "extra"],
                         ["list", "--harness=nothere"],
                         ["info"],                   # usage
                         ["info", "a", "b"],
                         ["edit"],                   # usage
                         ["new", "claude"],          # usage
                         ["check", "extra"]):
                code, _ = _run(home, *args)
                self.assertEqual(code, 1, args)

    def test_json_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _link(home, "claude", "guard.py")
            code, out = _run(home, "info", "guard", "--json")
            self.assertEqual(code, 0)
            data = json.loads(out)["data"]
            self.assertEqual(data["harnesses"][0]["link_status"], "linked")
            code, out = _run(home, "check", "--json")
            self.assertEqual(code, 1)
            kinds = {f["kind"] for f in json.loads(out)["data"]["findings"]}
            self.assertIn("undeclared", kinds)
            code, out = _run(home, "new", "claude", "gate2", "--json")
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["data"]["path"].endswith("gate2.py"))

    def test_clean_check_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _link(home, "claude", "guard.py")
            _settings(home, ".claude/settings.json", {
                "Stop": [{"hooks": [{"command": "guard.py"}]}],
            })
            code, out = _run(home, "check")
            self.assertEqual(code, 0)
            self.assertIn("all agree", out)

    def test_corrupt_linkignore_warns_but_shows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            # An unreadable .linkignore raises LinkIgnoreError on load.
            (home / ".quiver/.linkignore").mkdir()
            err = StringIO()
            out = StringIO()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("sys.stderr", err), redirect_stdout(out):
                code = hooks_commands.cmd_hooks(["list"])
            self.assertEqual(code, 0)
            self.assertIn("guard.py", _plain(out.getvalue()))
            self.assertIn("warning:", err.getvalue())


class EditEdgeTest(unittest.TestCase):
    def test_visual_beats_editor_and_vi_is_the_floor(self):
        with mock.patch.dict(os.environ,
                             {"VISUAL": "code -w", "EDITOR": "nano"}):
            self.assertEqual(hooks_commands._editor_argv(), ["code", "-w"])
        with mock.patch.dict(os.environ, {"VISUAL": "", "EDITOR": ""},
                             clear=False):
            # unset both entirely for the vi floor
            env = {k: v for k, v in os.environ.items()
                   if k not in ("VISUAL", "EDITOR")}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(hooks_commands._editor_argv(), ["vi"])

    def test_symlinked_source_resolves_and_dangling_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            real = home / "elsewhere/guard.py"
            real.parent.mkdir(parents=True)
            real.write_text("x\n")
            link = hooks_dir(home) / "claude/guard.py"
            link.parent.mkdir(parents=True)
            link.symlink_to(real)
            seen = {}

            def fake_run(argv, **kw):
                seen["argv"] = argv
                return type("Done", (), {"returncode": 0})()

            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("subprocess.run", fake_run):
                code = hooks_commands.cmd_hooks(["edit", "guard"])
            self.assertEqual(code, 0)
            # resolve() lands on /private/var on macOS; compare resolved.
            self.assertEqual(Path(seen["argv"][-1]), real.resolve())

            link.unlink()
            link.symlink_to(home / "elsewhere/gone.py")
            code, out = _run(home, "edit", "guard")
            self.assertEqual(code, 1)
            self.assertIn("no real file", out)

    def test_editor_spawn_failure_and_tty_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            (home / ".factory").mkdir()
            _hook(home, "droid", "guard.py")
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("subprocess.run",
                            side_effect=OSError("no such editor")):
                stdin = mock.Mock()
                stdin.isatty.return_value = False
                out = StringIO()
                with mock.patch("sys.stdin", stdin), redirect_stdout(out):
                    code = hooks_commands.cmd_hooks(
                        ["edit", "guard", "--harness=claude"])
            self.assertEqual(code, 1)
            self.assertIn("Cannot run", _plain(out.getvalue()))

            # A tty gets the pick list; "2" selects the second row (droid).
            seen = {}

            def fake_run(argv, **kw):
                seen["argv"] = argv
                return type("Done", (), {"returncode": 0})()

            stdin = mock.Mock()
            stdin.isatty.return_value = True
            out = StringIO()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("sys.stdin", stdin), \
                 mock.patch("quiver.prompt.read_line", return_value="2"), \
                 mock.patch("subprocess.run", fake_run), \
                 redirect_stdout(out):
                code = hooks_commands.cmd_hooks(["edit", "guard"])
            self.assertEqual(code, 0)
            self.assertIn("droid", seen["argv"][-1])
            self.assertIn("several harnesses", _plain(out.getvalue()))

            for bad in ("9", "0", "-1"):
                out = StringIO()
                with mock.patch.object(Path, "home",
                                       staticmethod(lambda: home)), \
                     mock.patch("sys.stdin", stdin), \
                     mock.patch("quiver.prompt.read_line",
                                return_value=bad), \
                     redirect_stdout(out):
                    code = hooks_commands.cmd_hooks(["edit", "guard"])
                self.assertEqual(code, 1, bad)
                self.assertIn("No such choice", _plain(out.getvalue()))

            out = StringIO()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                 mock.patch("sys.stdin", stdin), \
                 mock.patch("quiver.prompt.read_line",
                            side_effect=EOFError), \
                 redirect_stdout(out):
                code = hooks_commands.cmd_hooks(["edit", "guard"])
            self.assertEqual(code, 1)


class CreateEdgeTest(unittest.TestCase):
    def test_bad_harness_name_and_missing_quiver_dir(self):
        self.assertIsNotNone(mk.validate_args("a/b", "ok"))
        self.assertIsNotNone(mk.validate_args("ok", ".."))
        self.assertIsNone(mk.validate_args("claude", "gate-2_x"))
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)  # no .quiver at all
            with self.assertRaises(ValueError) as ctx:
                mk.create("claude", "gate", home)
            self.assertIn("swe init", str(ctx.exception))

    def test_publish_failure_leaves_no_litter_and_never_clobbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            with mock.patch("os.link", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    mk.create("claude", "gate", home)
            harness_dir = hooks_dir(home) / "claude"
            self.assertEqual(list(harness_dir.iterdir()), [])
            # A lost race surfaces as the same refusal the pre-check gives.
            with mock.patch("os.link",
                            side_effect=FileExistsError("exists")):
                with self.assertRaises(ValueError):
                    mk.create("claude", "gate", home)
            self.assertEqual(list(harness_dir.iterdir()), [])

    def test_command_for_root_outside_home_is_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            reg = {"x": {"capabilities": {"hooks":
                                          {"supported": True,
                                           "root": "/opt/hooks"}}}}
            command, note = mk.command_for("x", "g.py", reg, home)
            self.assertEqual(command, "/opt/hooks/g.py")
            self.assertIsNone(note)

    def test_command_for_quotes_paths_that_need_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            reg = {"x": {"capabilities": {"hooks":
                                          {"supported": True,
                                           "root": "~/my hooks"}}}}
            command, _ = mk.command_for("x", "g.py", reg, home)
            # "~" never expands inside quotes, so $HOME carries the space.
            self.assertEqual(command, '"${HOME}"/\'my hooks/g.py\'')
            # Pasted inside double quotes, a $(...) or backtick in a
            # directory name ran when the hook did; now it stays a name.
            reg = {"x": {"capabilities": {"hooks":
                                          {"supported": True,
                                           "root": '~/it"s $(touch x) `id`'}}}}
            command, _ = mk.command_for("x", "g.py", reg, home)
            import shlex
            self.assertEqual(shlex.split(command),
                             ['${HOME}/it"s $(touch x) `id`/g.py'])
            reg = {"x": {"capabilities": {"hooks":
                                          {"supported": True,
                                           "root": "/opt/my hooks"}}}}
            command, _ = mk.command_for("x", "g.py", reg, home)
            self.assertEqual(command, "'/opt/my hooks/g.py'")

    def test_configured_settings_wins_before_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            reg = {"claude": {"capabilities": {"hooks": {
                "supported": True, "settings": "~/custom/hooks.json"}}}}
            self.assertEqual(
                mk.settings_targets(reg, "claude", home),
                [home / "custom/hooks.json"],
            )

    def test_settings_targets_table_and_root_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            # codex names hooks.json even when nothing exists yet.
            self.assertEqual(
                mk.settings_targets({}, "codex", home),
                [home / ".codex/hooks.json"],
            )
            reg = {"x": {"capabilities": {"hooks":
                                          {"supported": True,
                                           "root": "~/.x/hooks"}}}}
            self.assertEqual(
                mk.settings_targets(reg, "x", home),
                [home / ".x/settings.json"],
            )
            # A known file beats the convention.
            _settings(home, ".claude/settings.json", {})
            self.assertEqual(
                mk.settings_targets({}, "claude", home),
                [home / ".claude/settings.json"],
            )


class UnlinkedStatesTest(unittest.TestCase):
    def test_absorb_conflict_and_relink_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for name in ("same.py", "other.py", "moved.py"):
                _hook(home, "claude", name)
            dest_dir = home / ".claude/hooks"
            dest_dir.mkdir(parents=True)
            (dest_dir / "same.py").write_text("print('hi')\n")   # identical
            (dest_dir / "other.py").write_text("different\n")   # conflict
            (dest_dir / "moved.py").symlink_to(home / "elsewhere.py")
            msgs = [f.message for f in hook_findings(home)
                    if f.kind == "unlinked"]
            for name in ("same.py", "other.py", "moved.py"):
                self.assertTrue(any(m.startswith(name) for m in msgs), name)

    def test_list_shows_state_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "moved.py")
            dest_dir = home / ".claude/hooks"
            dest_dir.mkdir(parents=True)
            (dest_dir / "moved.py").symlink_to(home / "elsewhere.py")
            code, out = _run(home)
            self.assertEqual(code, 0)
            self.assertIn("relink", out)
            msgs = [f.message for f in hook_findings(home)]
            self.assertTrue(any("elsewhere.py" in m for m in msgs))


class PlumbingTest(unittest.TestCase):
    def test_harness_arg_pair_and_compact_forms(self):
        rest, h = hooks_commands._harness_arg(["x", "--harness", "cc"])
        self.assertEqual((rest, h), (["x"], "cc"))
        rest, h = hooks_commands._harness_arg(["--harness=cc", "x"])
        self.assertEqual((rest, h), (["x"], "cc"))

    def test_resolve_harness_needs_one_alias_match(self):
        self.assertEqual(
            hooks_commands._resolve_harness(
                "cc", {"a": {"aliases": ["cc"]}, "b": {"aliases": ["cc"]}},
                {"a", "b"}),
            "cc")   # ambiguous alias -> left alone, not guessed
        self.assertEqual(
            hooks_commands._resolve_harness(
                "cc", {"claude": {"aliases": "cc"}}, {"claude"}),
            "claude")

    def test_short_handles_outside_home(self):
        home = Path("/tmp/h")
        self.assertEqual(hooks_commands._short(Path("/etc/x"), home),
                         "/etc/x")
        self.assertEqual(hooks_commands._short(home / "a/b", home), "~/a/b")

    def test_basenames_and_expand_edges(self):
        self.assertEqual(basenames_in(""), set())
        self.assertNotIn("x", basenames_in("run --flag -x"))
        self.assertEqual(expand_command_path("~", Path("/h")), Path("/h"))


if __name__ == "__main__":
    unittest.main()
