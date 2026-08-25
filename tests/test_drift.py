import tempfile
import unittest
from pathlib import Path

from quiver.harness.drift import (
    Finding,
    check_code_vs_data,
    check_dangling_symlinks,
    check_help_vs_dispatch,
    check_prose_mentions,
    check_registry_schema,
    check_subcommand_help,
    _real_commands,
    _real_help_topics,
    _real_mcp_help_and_commands,
)


class HelpVsDispatchTest(unittest.TestCase):
    def test_flags_orphan_help_topic(self):
        help_topics = {"list", "orphan-topic"}
        commands = {"list": lambda args: 0}
        findings = check_help_vs_dispatch(help_topics, commands)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].area, "help")
        self.assertIn("orphan-topic", findings[0].message)

    def test_flags_command_with_no_topic(self):
        help_topics = {"list"}
        commands = {"list": lambda args: 0, "ghost": lambda args: 0}
        findings = check_help_vs_dispatch(help_topics, commands)
        self.assertEqual(len(findings), 1)
        self.assertIn("ghost", findings[0].message)

    def test_consistent_pair_is_clean(self):
        help_topics = {"list", "doctor"}
        commands = {"list": lambda args: 0, "doctor": lambda args: 0}
        self.assertEqual(check_help_vs_dispatch(help_topics, commands), [])

    def test_whitelisted_aliases_are_not_flagged(self):
        help_topics = {"list", "harness", "providers", "skills", "help"}
        commands = {
            "list": None, "ls": None,
            "harness": None, "hs": None,
            "providers": None, "pv": None,
            "skills": None, "sk": None,
            "use": None, "run": None,
            "remove": None, "rm": None,
            "-h": None, "--help": None, "help": None,
            "__complete": None,
        }
        # "use" has no help topic and is NOT in the whitelist, so it alone
        # should be flagged; every alias-style entry should be silent.
        findings = check_help_vs_dispatch(help_topics, commands)
        messages = [f.message for f in findings]
        self.assertTrue(any("'use'" in m for m in messages))
        for alias in ("ls", "hs", "pv", "sk", "run", "rm", "-h", "--help", "__complete", "help"):
            self.assertFalse(any(f"'{alias}'" in m for m in messages), msg=alias)

    def test_real_help_and_dispatch_agree(self):
        # help_text.py's HELP topics and cli.py's COMMANDS dispatch are a
        # pure-code invariant (no machine state involved), so this can
        # assert zero findings rather than merely "does not crash". The
        # dead "star" topic (a swe hs star subcommand with no matching
        # top-level command) was the last known drift here; it is gone now.
        findings = check_help_vs_dispatch(_real_help_topics(), _real_commands())
        self.assertEqual(findings, [])


class SubcommandHelpTest(unittest.TestCase):
    def test_phantom_help_entry_warns(self):
        findings = check_subcommand_help({"sync", "export"}, {"sync"}, "mcp")
        self.assertEqual(len(findings), 1)
        self.assertIn("'export'", findings[0].message)

    def test_undocumented_subcommand_warns(self):
        findings = check_subcommand_help({"sync"}, {"sync", "doctor"}, "mcp")
        self.assertEqual(len(findings), 1)
        self.assertIn("'doctor'", findings[0].message)

    def test_whitelist_and_agreement_are_silent(self):
        findings = check_subcommand_help(
            {"sync"}, {"sync", "help"}, "mcp", whitelist=frozenset({"help"})
        )
        self.assertEqual(findings, [])

    def test_real_mcp_help_and_dispatch_agree(self):
        help_keys, command_keys = _real_mcp_help_and_commands()
        self.assertEqual(
            check_subcommand_help(
                help_keys, command_keys, "mcp", whitelist=frozenset({"help"})
            ),
            [],
        )


class ProseMentionsTest(unittest.TestCase):
    def test_phantom_prose_mention_warns(self):
        text = "run swe mcp export to save, or swe mcp sync to apply"
        findings = check_prose_mentions(text, "mcp", {"sync"})
        self.assertEqual(len(findings), 1)
        self.assertIn("'export'", findings[0].message)

    def test_placeholders_and_real_commands_are_silent(self):
        # "<command>" doesn't match the two-word pattern; real subs are fine.
        text = "swe mcp <command> help, swe mcp sync, swe mcp help"
        self.assertEqual(check_prose_mentions(text, "mcp", {"sync"}), [])


class RegistrySchemaTest(unittest.TestCase):
    def test_bad_state_is_an_error(self):
        registry = {"foo": {"state": "pinned"}}
        findings = check_registry_schema(registry)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("pinned", findings[0].message)

    def test_stray_pin_without_starred_state_warns(self):
        registry = {"foo": {"state": "active", "pin": 1}}
        findings = check_registry_schema(registry)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        self.assertIn("pin", findings[0].message)

    def test_archived_object_without_archived_state_warns(self):
        registry = {"foo": {"state": "active", "archived": {"reason": "x"}}}
        findings = check_registry_schema(registry)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        self.assertIn("archived", findings[0].message)

    def test_missing_capability_root_warns_not_errors(self):
        registry = {
            "foo": {
                "state": "active",
                "capabilities": {"skills": {"supported": True, "root": "~/.nope-does-not-exist/skills"}},
            }
        }
        findings = check_registry_schema(registry)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warn")
        self.assertIn("does not exist", findings[0].message)

    def test_clean_entry_has_no_findings(self):
        registry = {
            "foo": {"state": "starred", "pin": 1},
            "bar": {"state": "archived", "archived": {"reason": "x"}},
            "baz": {},
        }
        self.assertEqual(check_registry_schema(registry), [])


class CodeVsDataTest(unittest.TestCase):
    def test_registry_extending_the_table_is_healthy(self):
        # Capabilities-first: the registry knowing a root the fallback table
        # lacks is the design working, not drift.
        registry = {
            "widget": {
                "capabilities": {"plugins": {"supported": True, "root": "~/.widget/plugins"}},
            }
        }
        self.assertEqual(check_code_vs_data(registry, plugin_roots=()), [])

    def test_registry_overriding_the_table_is_healthy(self):
        # The fallback claiming support the registry denies is the override
        # working: at runtime capabilities win, so nothing has drifted.
        registry = {"gadget": {"capabilities": {"plugins": {"supported": False}}}}
        findings = check_code_vs_data(
            registry, plugin_roots=(("gadget", Path(".gadget/plugins")),)
        )
        self.assertEqual(findings, [])

    def test_table_naming_unknown_harness_warns(self):
        # A fallback entry for a harness the registry has never heard of is
        # a stale row nothing can override — that one is real drift.
        findings = check_code_vs_data(
            {}, plugin_roots=(("ghost", Path(".ghost/plugins")),)
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("ghost", findings[0].message)
        self.assertIn("no such harness", findings[0].message)

    def test_supported_without_root_warns(self):
        registry = {"gizmo": {"capabilities": {"plugins": {"supported": True}}}}
        findings = check_code_vs_data(registry, plugin_roots=())
        self.assertEqual(len(findings), 1)
        self.assertIn("records no root path", findings[0].message)

    def test_name_mismatch_on_shared_root_path(self):
        registry = {
            "oldname": {
                "capabilities": {"plugins": {"supported": True, "root": "~/.thing/plugins"}},
            }
        }
        findings = check_code_vs_data(
            registry, plugin_roots=(("newname", Path(".thing/plugins")),)
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("name mismatch", findings[0].message)
        self.assertIn("oldname", findings[0].message)
        self.assertIn("newname", findings[0].message)

    def test_consistent_table_and_registry_is_clean(self):
        registry = {
            "widget": {
                "capabilities": {"plugins": {"supported": True, "root": "~/.widget/plugins"}},
            }
        }
        findings = check_code_vs_data(
            registry, plugin_roots=(("widget", Path(".widget/plugins")),)
        )
        self.assertEqual(findings, [])


class DanglingSymlinksTest(unittest.TestCase):
    def test_flags_broken_symlink_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target-dir"
            target.mkdir()
            (root / "good-link").symlink_to(target)
            (root / "bad-link").symlink_to(root / "missing-target")
            (root / "plain-file").write_text("hi")

            findings = check_dangling_symlinks([root])

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "warn")
            self.assertIn("bad-link", findings[0].message)

    def test_missing_directory_is_skipped_not_errored(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist"
            self.assertEqual(check_dangling_symlinks([missing]), [])

    def test_clean_directory_has_no_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_text("hi")
            (root / "subdir").mkdir()
            self.assertEqual(check_dangling_symlinks([root]), [])


if __name__ == "__main__":
    unittest.main()
