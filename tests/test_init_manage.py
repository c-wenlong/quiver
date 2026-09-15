"""`swe init` offers to manage skills roots harness.json does not know.

A discovered ``~/.*/skills`` whose label is no registry key or alias is a
new harness: tick it and init registers it (active, with skills root and
instruction file in capabilities) and links both; leave it unticked and it
is archived, reported ``ignored``, and never asked about again.
"""

import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from quiver.init import commands as init_commands
from quiver.init import manage
from quiver.init.hooks import plan_hooks
from quiver.init.layout import link_states, plan


def _home(tmp: str, registry: dict | None = None) -> Path:
    home = Path(tmp)
    (home / ".quiver").mkdir()
    if registry is not None:
        cfg = home / ".quiver" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "harness.json").write_text(json.dumps(registry))
    return home


def _init(home: Path, args: list[str], *, supported=False,
          chosen=None, lines=None) -> str:
    """Run cmd_init against ``home`` with the interactive seams patched."""
    answers = iter(lines or [])
    picker = mock.Mock(return_value=chosen)
    out = StringIO()
    with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
        mock.patch.object(init_commands, "_supported", lambda: supported), \
        mock.patch.object(init_commands, "multiselect", picker), \
        mock.patch.object(init_commands, "read_line",
                          lambda *a, **k: next(answers)), \
        redirect_stdout(out):
        init_commands.cmd_init(args)
    return re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue()), picker


def _registry(home: Path) -> dict:
    path = home / ".quiver" / "config" / "harness.json"
    return json.loads(path.read_text())


class NewHarnessCheckTest(unittest.TestCase):
    def test_check_only_reports_new_harness_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".config" / "foo" / "skills"
            (root / "x").mkdir(parents=True)
            (root / "x" / "SKILL.md").write_text("# x")
            out, _ = _init(home, ["--check", "--full"])
            self.assertIn("1 new harness", out)
            self.assertIn("foo", out)
            self.assertFalse((home / ".quiver" / "config" / "harness.json").exists())
            self.assertFalse(root.is_symlink())


class YesFlagTest(unittest.TestCase):
    def test_yes_registers_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".config" / "foo" / "skills"
            root.mkdir(parents=True)  # empty: classifies absorb, still offered
            out, picker = _init(home, ["--yes", "--full"])
            self.assertFalse(picker.called)
            entry = _registry(home)["foo"]
            self.assertNotIn("state", entry)
            self.assertEqual(entry["discovered_via"], "init")
            self.assertEqual(
                entry["capabilities"]["skills"]["root"], "~/.config/foo/skills"
            )
            self.assertEqual(
                entry["capabilities"]["instructions"]["file"],
                "~/.config/foo/AGENTS.md",
            )
            agents = home / ".config" / "foo" / "AGENTS.md"
            self.assertTrue(agents.is_symlink())
            self.assertEqual(agents.resolve(), (home / ".quiver" / "AGENTS.md").resolve())
            self.assertTrue(root.is_symlink())
            self.assertEqual(root.resolve(), (home / ".quiver" / "skills").resolve())

    def test_already_linked_root_is_still_offered(self):
        """A machine that ran init before this feature has linked roots."""
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            shared = home / ".quiver" / "skills"
            shared.mkdir(parents=True)
            (home / ".config" / "foo").mkdir(parents=True)
            root = home / ".config" / "foo" / "skills"
            root.symlink_to(shared)
            out, picker = _init(home, ["--full"], supported=True,
                                chosen=["foo"], lines=[""])
            self.assertTrue(picker.called)
            self.assertIn("foo", [c.key for c in picker.call_args[0][0]])
            entry = _registry(home)["foo"]
            self.assertNotIn("state", entry)
            self.assertEqual(
                entry["capabilities"]["skills"]["root"], "~/.config/foo/skills"
            )
            self.assertTrue(root.is_symlink())

    def test_non_terminal_registers_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".config" / "foo" / "skills"
            root.mkdir(parents=True)
            out, picker = _init(home, ["--full"], supported=False)
            self.assertFalse(picker.called)
            self.assertIn("not a terminal, registering every new harness", out)
            entry = _registry(home)["foo"]
            self.assertNotIn("state", entry)
            self.assertEqual(
                entry["capabilities"]["instructions"]["file"],
                "~/.config/foo/AGENTS.md",
            )
            self.assertTrue((home / ".config" / "foo" / "AGENTS.md").is_symlink())


class InteractivePickerTest(unittest.TestCase):
    def _two_roots(self, home: Path) -> None:
        (home / ".foo" / "skills").mkdir(parents=True)
        (home / ".bar" / "skills").mkdir(parents=True)

    def test_tick_registers_untick_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            self._two_roots(home)
            out, _ = _init(home, ["--full"], supported=True,
                           chosen=["foo"], lines=["CLAUDE.md"])
            registry = _registry(home)
            self.assertNotIn("state", registry["foo"])
            self.assertEqual(
                registry["foo"]["capabilities"]["instructions"]["file"],
                "~/.foo/CLAUDE.md",
            )
            self.assertTrue((home / ".foo" / "CLAUDE.md").is_symlink())
            bar = registry["bar"]
            self.assertEqual(bar["state"], "archived")
            self.assertEqual(bar["archived"]["reason"], "declined in swe init")
            self.assertFalse((home / ".bar" / "skills").is_symlink())
            self.assertRegex(out, r"ignored\s+~/\.bar/skills\s+archived in harness\.json")

    def test_skip_leaves_no_instruction_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".foo" / "skills").mkdir(parents=True)
            _init(home, ["--full"], supported=True, chosen=["foo"], lines=["skip"])
            entry = _registry(home)["foo"]
            self.assertNotIn("instructions", entry["capabilities"])
            self.assertEqual(
                [p.name for p in (home / ".foo").iterdir()], ["skills"]
            )

    def test_bad_filename_reasks_once_then_defaults(self):
        for bad in (".", "..", "a/b"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                home = _home(tmp)
                (home / ".foo" / "skills").mkdir(parents=True)
                out, _ = _init(home, ["--full"], supported=True,
                               chosen=["foo"], lines=[bad, ""])
                self.assertIn("just a filename such as AGENTS.md", out)
                entry = _registry(home)["foo"]
                self.assertEqual(
                    entry["capabilities"]["instructions"]["file"],
                    "~/.foo/AGENTS.md",
                )

    def test_register_refuses_bad_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".foo" / "skills"
            root.mkdir(parents=True)
            with self.assertRaises(ValueError):
                manage.register(home, {}, {"foo": (root, "..")}, {})
            self.assertFalse(
                (home / ".quiver" / "config" / "harness.json").exists()
            )

    def test_register_writes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".foo" / "skills"
            root.mkdir(parents=True)
            registry = manage.register(home, {}, {"foo": (root, None)}, {})
            cfg = home / ".quiver" / "config"
            self.assertFalse((cfg / "harness.json.tmp").exists())
            self.assertEqual(
                json.loads((cfg / "harness.json").read_text()), registry
            )

    def test_eof_defaults_to_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".foo" / "skills").mkdir(parents=True)

            def eof(*a, **k):
                raise EOFError

            out = StringIO()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)), \
                mock.patch.object(init_commands, "_supported", lambda: True), \
                mock.patch.object(init_commands, "multiselect",
                                  lambda *a, **k: ["foo"]), \
                mock.patch.object(init_commands, "read_line", eof), \
                redirect_stdout(out):
                init_commands.cmd_init(["--full"])
            entry = _registry(home)["foo"]
            self.assertEqual(
                entry["capabilities"]["instructions"]["file"],
                "~/.foo/AGENTS.md",
            )

    def test_cancel_registers_nothing_but_still_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".foo" / "skills"
            root.mkdir(parents=True)
            out, _ = _init(home, ["--full"], supported=True, chosen=None)
            self.assertIn("cancelled, nothing registered", out)
            self.assertFalse((home / ".quiver" / "config" / "harness.json").exists())
            self.assertTrue(root.is_symlink())


class ArchivedMeansUnmanagedTest(unittest.TestCase):
    def test_archived_root_is_ignored_not_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"droid": {"state": "archived", "aliases": []}})
            (home / ".factory" / "skills").mkdir(parents=True)
            out, _ = _init(home, ["--full"])
            self.assertFalse((home / ".factory" / "skills").is_symlink())
            self.assertFalse((home / ".factory" / "AGENTS.md").exists())
            self.assertRegex(
                out, r"ignored\s+~/\.factory/skills\s+archived in harness\.json"
            )
            self.assertRegex(
                out, r"ignored\s+~/\.factory/AGENTS\.md\s+archived in harness\.json"
            )

    def test_archived_but_linked_stays_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"droid": {"state": "archived", "aliases": []}})
            shared = home / ".quiver" / "skills"
            shared.mkdir(parents=True)
            (home / ".factory").mkdir()
            (home / ".factory" / "skills").symlink_to(shared)
            out, _ = _init(home, ["--check", "--full"])
            self.assertRegex(out, r"linked\s+~/\.factory/skills")

    def test_archived_hook_is_ignored_not_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"droid": {"state": "archived", "aliases": []}})
            hooks = home / ".quiver" / "hooks" / "droid"
            hooks.mkdir(parents=True)
            (hooks / "x.sh").write_text("#!/bin/sh\n")
            (home / ".factory").mkdir()
            statuses = plan_hooks(home)
            self.assertEqual(len(statuses), 1)
            self.assertEqual(statuses[0].state, "ignored")
            self.assertEqual(statuses[0].detail, "archived in harness.json")
            out, _ = _init(home, ["--full"])
            self.assertFalse((home / ".factory" / "hooks" / "x.sh").exists())

    def test_archived_hook_already_linked_stays_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"droid": {"state": "archived", "aliases": []}})
            hooks = home / ".quiver" / "hooks" / "droid"
            hooks.mkdir(parents=True)
            (hooks / "x.sh").write_text("#!/bin/sh\n")
            dest = home / ".factory" / "hooks"
            dest.mkdir(parents=True)
            (dest / "x.sh").symlink_to(hooks / "x.sh")
            statuses = plan_hooks(home)
            self.assertEqual(statuses[0].state, "linked")

    def test_archived_entry_with_string_alias_marks_root_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"gone": {"state": "archived", "aliases": "foo"}})
            (home / ".foo" / "skills").mkdir(parents=True)
            out, _ = _init(home, ["--check", "--full"])
            self.assertRegex(out, r"ignored\s+~/\.foo/skills\s+archived in harness\.json")


class StringAliasTest(unittest.TestCase):
    def test_string_alias_counts_as_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"bar": {"aliases": "foo"}})
            (home / ".foo" / "skills").mkdir(parents=True)
            _, picker = _init(home, ["--full"], supported=True, chosen=[])
            self.assertFalse(picker.called)


class RegistryDrivenTargetsTest(unittest.TestCase):
    def test_registry_file_adds_instruction_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {
                "foo": {"capabilities": {
                    "instructions": {"file": "~/.config/foo/AGENTS.md"}}},
            })
            (home / ".config" / "foo").mkdir(parents=True)
            with mock.patch.object(Path, "home", staticmethod(lambda: home)):
                instructions, _ = plan(home)
                foo = [s for s in instructions if s.label == "foo"]
                self.assertEqual(len(foo), 1)
                self.assertEqual(foo[0].state, "create")
                self.assertEqual(link_states(home)["foo"]["agents"], "create")

    def test_registry_file_overrides_tuple_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {
                "claude": {"capabilities": {
                    "instructions": {"file": "~/.claude/AGENTS.md"}}},
            })
            (home / ".claude").mkdir()
            with mock.patch.object(Path, "home", staticmethod(lambda: home)):
                instructions, _ = plan(home)
                claude = [s for s in instructions if s.label == "claude"]
                self.assertEqual(len(claude), 1)
                self.assertTrue(str(claude[0].path).endswith("AGENTS.md"))


class AlreadyKnownTest(unittest.TestCase):
    def test_registered_label_is_not_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"foo": {}})
            (home / ".config" / "foo" / "skills").mkdir(parents=True)
            _, picker = _init(home, ["--full"], supported=True, chosen=[])
            self.assertFalse(picker.called)

    def test_alias_of_registered_name_is_not_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp, {"foobar": {"aliases": ["foo"]}})
            (home / ".config" / "foo" / "skills").mkdir(parents=True)
            _, picker = _init(home, ["--full"], supported=True, chosen=[])
            self.assertFalse(picker.called)

    def test_agents_shared_dir_is_never_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".config" / "agents" / "skills").mkdir(parents=True)
            _, picker = _init(home, ["--full"], supported=True, chosen=[])
            self.assertFalse(picker.called)


class LegacyGuardTest(unittest.TestCase):
    def test_unmigrated_tools_json_blocks_the_picker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            cfg = home / ".quiver" / "config"
            cfg.mkdir(parents=True)
            (cfg / "tools.json").write_text(json.dumps({"claude": {}}))
            (home / ".config" / "foo" / "skills").mkdir(parents=True)
            out, picker = _init(home, ["--full"], supported=True, chosen=[])
            self.assertIn("harness.json not migrated yet", out)
            self.assertFalse((cfg / "harness.json").exists())
            self.assertFalse(picker.called)


if __name__ == "__main__":
    unittest.main()
