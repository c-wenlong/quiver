"""~/.swe/hooks/<harness>/: hook scripts linked one by one into each harness.

Hooks are per harness, not shared: a script under hooks/claude/ goes only to
Claude Code's hooks directory, and only the file is linked, never the settings
entry that declares it.
"""

import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from swe.harness.drift import check_code_vs_data
from swe.init import commands as init_commands
from swe.init.hooks import (
    HOOK_FALLBACK,
    INSIDE_SWE_DETAIL,
    NO_ROOT_DETAIL,
    UNKNOWN_HARNESS_DETAIL,
    UNSUPPORTED_DETAIL,
    hook_root,
    plan_hooks,
)
from swe.init.layout import hooks_dir, linkignore_file


def _home(tmp: str) -> Path:
    home = Path(tmp)
    (home / ".swe").mkdir()
    (home / ".claude").mkdir()
    return home


def _hook(home: Path, harness: str, name: str, body: str = "print('hi')\n") -> Path:
    path = hooks_dir(home) / harness / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def _registry(home: Path, data: dict) -> None:
    path = home / ".swe" / "config" / "harness.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _states(statuses) -> dict[str, str]:
    return {s.path.name: s.state for s in statuses}


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class HookRootTest(unittest.TestCase):
    def test_fallback_table_covers_an_undescribed_harness(self):
        home = Path("/h")
        self.assertEqual(hook_root({}, "claude", home), (home / ".claude/hooks", ""))

    def test_registry_root_beats_the_table(self):
        home = Path("/h")
        reg = {"claude": {"capabilities": {"hooks": {"supported": True, "root": "~/elsewhere"}}}}
        self.assertEqual(hook_root(reg, "claude", home), (home / "elsewhere", ""))

    def test_supported_false_turns_off_even_a_table_harness(self):
        reg = {"claude": {"capabilities": {"hooks": {"supported": False}}}}
        self.assertEqual(hook_root(reg, "claude", Path("/h")), (None, UNSUPPORTED_DETAIL))

    def test_unknown_harness_has_no_root(self):
        self.assertEqual(
            hook_root({}, "not-a-harness", Path("/h")), (None, NO_ROOT_DETAIL))


class PlanHooksTest(unittest.TestCase):
    def test_no_hooks_dir_plans_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(plan_hooks(_home(tmp)), [])

    def test_missing_destination_is_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = _hook(home, "claude", "guard.py")
            [status] = plan_hooks(home)
            self.assertEqual(status.state, "create")
            self.assertEqual(status.path, home / ".claude/hooks/guard.py")
            self.assertEqual(status.source, src)

    def test_harness_not_installed_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "droid", "guard.sh")
            # Binary evidence counts too (#68): stub PATH or a machine
            # with droid installed reads the same dirs as installed.
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                [status] = plan_hooks(home)
            self.assertEqual((status.state, status.detail), ("skipped", "harness not installed"))

    def test_binary_on_path_links_hooks_when_no_dir_exists(self):
        # #68: a Nix-profile droid has never run, so ~/.factory is absent —
        # dir evidence alone used to skip it as "harness not installed".
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "droid", "guard.sh")
            which = lambda name: "/fake/bin/droid" if name == "droid" else None
            with mock.patch("swe.init.layout.shutil.which", side_effect=which):
                [status] = plan_hooks(home)
            self.assertEqual(status.state, "create")
            self.assertEqual(status.path, home / ".factory/hooks/guard.sh")

    def test_identical_plain_copy_is_absorbed_not_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py", "same\n")
            (home / ".claude/hooks").mkdir()
            (home / ".claude/hooks/guard.py").write_text("same\n")
            self.assertEqual(_states(plan_hooks(home)), {"guard.py": "absorb"})

    def test_different_plain_file_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py", "new\n")
            (home / ".claude/hooks").mkdir()
            (home / ".claude/hooks/guard.py").write_text("old\n")
            self.assertEqual(_states(plan_hooks(home)), {"guard.py": "conflict"})

    def test_link_to_source_is_linked_and_elsewhere_is_relink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            good = _hook(home, "claude", "good.py")
            _hook(home, "claude", "bad.py")
            hooks = home / ".claude/hooks"
            hooks.mkdir()
            (hooks / "good.py").symlink_to(good)
            (hooks / "bad.py").symlink_to(home / "nowhere.py")
            self.assertEqual(
                _states(plan_hooks(home)), {"bad.py": "relink", "good.py": "linked"}
            )

    def test_other_scripts_in_the_harness_dir_are_not_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            (home / ".claude/hooks").mkdir()
            (home / ".claude/hooks/installer-owned.sh").write_text("x")
            self.assertEqual(_states(plan_hooks(home)), {"guard.py": "create"})

    def test_dotfiles_and_build_litter_are_not_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _hook(home, "claude", ".DS_Store")
            (hooks_dir(home) / "claude/__pycache__").mkdir()
            (hooks_dir(home) / "README.md").write_text("top-level files are not harnesses")
            self.assertEqual(_states(plan_hooks(home)), {"guard.py": "create"})

    def test_linkignore_matches_the_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _hook(home, "claude", "other.py")
            linkignore_file(home).write_text(".claude/hooks/guard.py\n")
            self.assertEqual(
                _states(plan_hooks(home)), {"guard.py": "ignored", "other.py": "create"}
            )

    def test_harness_with_no_root_is_skipped_with_the_fix(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = _hook(home, "rootless", "notify.sh")
            _registry(home, {"rootless": {"command": "rootless"}})
            [status] = plan_hooks(home)
            self.assertEqual((status.state, status.detail), ("skipped", NO_ROOT_DETAIL))
            self.assertEqual(status.path, src)

    def test_unknown_hub_dir_is_one_skipped_entry_not_one_per_file(self):
        # A bundle a plugin runs straight from the hub: no registry entry,
        # no contract, so no harness.json fix applies to each file in it.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            for name in ("command_guard.py", "README.md", "rules.json"):
                _hook(home, "command-guard", name)
            [status] = plan_hooks(home)
            bundle = hooks_dir(home) / "command-guard"
            self.assertEqual(
                (status.label, status.state, status.detail),
                ("command-guard", "skipped", UNKNOWN_HARNESS_DETAIL))
            self.assertEqual((status.path, status.source), (bundle, bundle))

    def test_contract_harness_without_registry_entry_still_plans_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "a.py")
            _hook(home, "claude", "b.py")
            self.assertEqual(_states(plan_hooks(home)), {"a.py": "create", "b.py": "create"})

    def test_registry_root_is_read_from_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".codex").mkdir()
            _hook(home, "codex", "notify.sh")
            _registry(home, {"codex": {"capabilities": {"hooks": {"supported": True, "root": "~/.codex/hooks"}}}})
            [status] = plan_hooks(home)
            self.assertEqual((status.state, status.path), ("create", home / ".codex/hooks/notify.sh"))


class HookRootSafetyTest(unittest.TestCase):
    def test_root_inside_quiver_is_skipped_and_source_survives(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = _hook(home, "claude", "guard.py", "keep me\n")
            _registry(home, {"claude": {"capabilities": {"hooks": {"supported": True, "root": "~/.swe/hooks/claude"}}}})
            [status] = plan_hooks(home)
            self.assertEqual((status.state, status.detail), ("skipped", INSIDE_SWE_DETAIL))
            out = StringIO()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), redirect_stdout(out):
                init_commands.cmd_init([])
            self.assertFalse(src.is_symlink())
            self.assertEqual(src.read_text(), "keep me\n")

    def test_hard_link_to_the_source_is_never_absorbed(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = _hook(home, "claude", "guard.py")
            (home / ".claude/hooks").mkdir()
            os.link(src, home / ".claude/hooks/guard.py")
            [status] = plan_hooks(home)
            self.assertEqual(status.state, "skipped")

    def test_root_directly_under_home_must_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            _registry(home, {"claude": {"capabilities": {"hooks": {"supported": True, "root": "~/elsewhere"}}}})
            # This pins the dir rule, not the PATH rule: stub which or a
            # machine with claude installed answers "installed" via binary.
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                [status] = plan_hooks(home)
                self.assertEqual((status.state, status.detail), ("skipped", "harness not installed"))
                (home / "elsewhere").mkdir()
                [status] = plan_hooks(home)
                self.assertEqual(status.state, "create")

    def test_deep_root_counts_its_own_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".config/opencode").mkdir(parents=True)
            _hook(home, "opencode", "guard.ts")
            _registry(home, {"opencode": {"capabilities": {"hooks": {"supported": True, "root": "~/.config/opencode/hooks"}}}})
            [status] = plan_hooks(home)
            self.assertEqual(status.state, "create")


class InitHooksTest(unittest.TestCase):
    def _run(self, home: Path, *args: str) -> tuple[int, str]:
        out = StringIO()
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            with redirect_stdout(out):
                code = init_commands.cmd_init(list(args))
        return code, _plain(out.getvalue())

    def test_check_reports_a_hooks_line_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py")
            code, out = self._run(home, "--check")
            self.assertEqual(code, 0)
            self.assertRegex(out, r"Hooks\s+1 would-create")
            self.assertFalse((home / ".claude/hooks/guard.py").exists())

    def test_no_hooks_says_nothing_to_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, out = self._run(_home(tmp), "--check")
            self.assertRegex(out, r"Hooks\s+nothing to link")

    def test_init_links_the_script_and_leaves_siblings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            src = _hook(home, "claude", "guard.py")
            sibling = home / ".claude/hooks/herdr.sh"
            sibling.parent.mkdir()
            sibling.write_text("installer-owned")
            code, out = self._run(home)
            dest = home / ".claude/hooks/guard.py"
            self.assertEqual(code, 0)
            self.assertTrue(dest.is_symlink())
            self.assertEqual(dest.resolve(), src.resolve())
            self.assertFalse(sibling.is_symlink())
            self.assertRegex(out, r"Hooks\s+1 linked")

    def test_identical_copy_is_backed_up_and_replaced_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py", "same\n")
            dest = home / ".claude/hooks/guard.py"
            dest.parent.mkdir()
            dest.write_text("same\n")
            code, _ = self._run(home)
            self.assertEqual(code, 0)
            self.assertTrue(dest.is_symlink())
            backups = list((home / ".swe/backups").glob(".claude_hooks_guard.py.*"))
            self.assertEqual(len(backups), 1)

    def test_different_file_blocks_until_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _hook(home, "claude", "guard.py", "new\n")
            dest = home / ".claude/hooks/guard.py"
            dest.parent.mkdir()
            dest.write_text("old\n")
            code, out = self._run(home)
            self.assertEqual(code, 1)
            self.assertFalse(dest.is_symlink())
            self.assertIn("~/.claude/hooks/guard.py", out)
            code, _ = self._run(home, "--force")
            self.assertEqual(code, 0)
            self.assertTrue(dest.is_symlink())


class DriftHooksTest(unittest.TestCase):
    def test_fallback_table_joins_with_registry_names(self):
        reg = {
            "claude": {"capabilities": {"hooks": {"supported": True, "root": "~/.claude/hooks"}}},
            "cline": {},
            "droid": {},
            # Every other contract-table row is "registered": the drift
            # check only warns on a table row the registry has never
            # heard of, and HOOK_FALLBACK covers many harnesses now.
            **{name: {} for name in HOOK_FALLBACK
               if name not in ("claude", "cline", "droid")},
        }
        findings = check_code_vs_data(reg, plugin_roots=(), hook_roots=list(HOOK_FALLBACK.items()))
        self.assertEqual(findings, [])

    def test_same_root_under_another_name_warns(self):
        reg = {"cc": {"capabilities": {"hooks": {"supported": True, "root": "~/.claude/hooks"}}}}
        findings = check_code_vs_data(
            reg, plugin_roots=(), hook_roots=[("claude", Path(".claude/hooks"))]
        )
        self.assertTrue(any("name mismatch" in f.message for f in findings))


if __name__ == "__main__":
    unittest.main()
