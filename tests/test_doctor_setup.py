"""`swe doctor`'s setup checks: hub, AGENTS.md, registry, unlinked."""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from quiver.harness.doctor import issue_findings, setup_findings


class SetupFindingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def hub(self):
        quiver = self.home / ".quiver"
        quiver.mkdir(exist_ok=True)
        return quiver

    def findings(self, registry, states=None):
        (self.home / "harness.json").write_text(json.dumps(registry))
        with mock.patch("quiver.harness.registry.HARNESS_FILE",
                        self.home / "harness.json"), \
             mock.patch("quiver.init.layout.link_states",
                        return_value=states or {}):
            return setup_findings(self.home)

    def test_no_hub_reports_only_hub(self):
        got = self.findings({})
        self.assertEqual([f.check for f in got], ["setup.hub"])
        self.assertEqual(got[0].action, "setup")

    def test_hub_without_agents_md(self):
        self.hub()
        got = self.findings({})
        self.assertEqual([f.check for f in got], ["setup.agents-md"])
        self.assertEqual(got[0].fix, "swe setup --apply")

    def test_empty_registry_reports_registry_not_unlinked(self):
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        got = self.findings({})
        self.assertEqual([f.check for f in got], ["setup.registry"])
        self.assertEqual(got[0].action, "discover")

    def test_one_unlinked_one_linked(self):
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        registry = {"claude": {"command": "claude", "state": "active"},
                    "codex": {"command": "codex", "state": "active"}}
        states = {"claude": {"agents": "create", "skills": "linked"},
                  "codex": {"agents": "linked", "skills": "linked"}}
        got = self.findings(registry, states)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].check, "setup.unlinked")
        self.assertEqual(got[0].harness, "claude")
        self.assertEqual(got[0].action, "init")
        self.assertIn("the shared AGENTS.md", got[0].message)

    def test_archived_and_keep_are_ignored(self):
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        registry = {"old": {"command": "old", "state": "archived"},
                    "keep": {"command": "keep"}}
        states = {"old": {"agents": "create"}, "keep": {"agents": "keep"}}
        self.assertEqual(self.findings(registry, states), [])

    def _declare(self, names, present=()):
        """A claude settings.json naming hub folder marketplaces."""
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        claude = self.home / ".claude"
        claude.mkdir(exist_ok=True)
        markets = {name: {"source": {"source": "directory",
                                     "path": str(self.home / ".quiver" / "plugins" / name)}}
                   for name in names}
        markets["remote"] = {"source": {"source": "github", "repo": "o/r"}}
        settings = claude / "settings.json"
        settings.write_text(json.dumps({"extraKnownMarketplaces": markets}))
        for name in present:
            (self.home / ".quiver" / "plugins" / name).mkdir(parents=True)
        return settings

    def test_missing_marketplace_folders_fold_into_one_warning(self):
        # #696: a synced settings.json named hub folders this machine lacks.
        self._declare(["nus", "design", "here"], present=["here"])
        registry = {"claude": {"command": "claude", "display_name": "Claude Code"}}
        got = [f for f in self.findings(registry)
               if f.check == "setup.marketplace-missing"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].harness, "claude")
        self.assertEqual(got[0].severity, "warning")
        # The github marketplace is fetched by claude itself, so not listed.
        self.assertIn("Claude Code registers 2 marketplace folders missing "
                      "on this machine: design, nus", got[0].message)
        self.assertIn("~/.claude/settings.json", got[0].fix)

    def test_the_drop_action_carries_its_targets(self):
        self._declare(["nus"])
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        (plugins / "known_marketplaces.json").write_text(json.dumps({
            "added": {"source": {"source": "directory",
                                 "path": str(self.home / "gone" / "added")}}}))
        got = [f for f in self.findings({"claude": {"command": "claude"}})
               if f.check == "setup.marketplace-missing"]
        drop = next(r for r in got[0].remedies
                    if r.get("action") == "marketplace-drop")
        self.assertEqual(drop["settings_entries"], ["nus"])
        self.assertEqual(drop["cli_entries"], ["added"])

    def test_a_nix_settings_file_leaves_the_cli_entries_droppable(self):
        settings = self._declare(["nus"])
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        (plugins / "known_marketplaces.json").write_text(json.dumps({
            "added": {"source": {"source": "directory",
                                 "path": str(self.home / "gone" / "added")}}}))
        real_realpath = os.path.realpath

        def realpath(path, *a, **k):
            if pathlib.Path(path) == settings:
                return "/nix/store/abc-settings.json"
            return real_realpath(path, *a, **k)

        with mock.patch("os.path.realpath", side_effect=realpath):
            got = [f for f in self.findings({"claude": {"command": "claude"}})
                   if f.check == "setup.marketplace-missing"]
        drop = next(r for r in got[0].remedies
                    if r.get("action") == "marketplace-drop")
        self.assertEqual((drop["settings_entries"], drop["cli_entries"]),
                         ([], ["added"]))

    def test_an_entry_in_both_files_is_dropped_from_both(self):
        # A marketplace registered in settings AND through the CLI needs
        # two deletes: removing only the settings copy leaves the known
        # copy, and the warning survives the Run.
        self._declare(["nus"])
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        (plugins / "known_marketplaces.json").write_text(json.dumps({
            "nus": {"source": {"source": "directory",
                               "path": str(self.home / "gone" / "nus")}}}))
        got = [f for f in self.findings({"claude": {"command": "claude"}})
               if f.check == "setup.marketplace-missing"]
        drop = next(r for r in got[0].remedies
                    if r.get("action") == "marketplace-drop")
        self.assertEqual((drop["settings_entries"], drop["cli_entries"]),
                         (["nus"], ["nus"]))

    def test_a_managed_settings_file_leaves_only_the_cli_targets(self):
        settings = self._declare(["nus"])
        real = self.home / "real-settings.json"
        settings.rename(real)
        settings.symlink_to(real)
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        (plugins / "known_marketplaces.json").write_text(json.dumps({
            "added": {"source": {"source": "directory",
                                 "path": str(self.home / "gone" / "added")}}}))
        got = [f for f in self.findings({"claude": {"command": "claude"}})
               if f.check == "setup.marketplace-missing"]
        drop = next(r for r in got[0].remedies
                    if r.get("action") == "marketplace-drop")
        self.assertEqual((drop["settings_entries"], drop["cli_entries"]),
                         ([], ["added"]))
        self.assertIn("managed elsewhere", got[0].fix)

    def test_another_harness_gets_no_drop_action(self):
        from quiver.harness.doctor import _marketplace_remedies
        remedies = _marketplace_remedies("codex", ["x"], ["x"], [], False)
        self.assertNotIn("marketplace-drop",
                         [r.get("action") for r in remedies])

    def test_drop_marketplace_entries_edits_both_files(self):
        from quiver.harness.doctor import drop_marketplace_entries
        settings = self._declare(["nus", "design", "here"], present=["here"])
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        known = plugins / "known_marketplaces.json"
        known.write_text(json.dumps({
            "added": {"source": {"source": "directory",
                                 "path": str(self.home / "gone" / "added")}},
            "kept": {"source": {"source": "directory",
                                "path": str(self.home / "x" / "kept")}}}))
        error = drop_marketplace_entries(self.home, ["nus", "design"], ["added"])
        self.assertIsNone(error)
        extra = json.loads(settings.read_text())["extraKnownMarketplaces"]
        # "remote" is the github-sourced entry _declare adds: not missing.
        self.assertEqual(sorted(extra), ["here", "remote"])
        self.assertEqual(list(json.loads(known.read_text())), ["kept"])
        self.assertEqual(len(list((self.hub() / "backups").iterdir())), 2)

    def test_a_managed_settings_file_is_skipped_not_failed(self):
        from quiver.harness.doctor import drop_marketplace_entries
        settings = self._declare(["nus"])
        real = self.home / "real-settings.json"
        settings.rename(real)
        settings.symlink_to(real)
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        known = plugins / "known_marketplaces.json"
        known.write_text(json.dumps({
            "added": {"source": {"source": "directory",
                                 "path": str(self.home / "gone" / "added")}}}))
        error = drop_marketplace_entries(self.home, ["nus"], ["added"])
        self.assertIn("managed elsewhere", error)
        self.assertIn("nus", json.loads(real.read_text())["extraKnownMarketplaces"])
        self.assertEqual(json.loads(known.read_text()), {})

    def test_a_nix_managed_settings_file_says_so(self):
        settings = self._declare(["nus"])
        registry = {"claude": {"command": "claude"}}
        real_realpath = os.path.realpath

        # Only the settings file resolves into the store, so the note has to
        # come from reading that file's own link, not any other path.
        def realpath(path, *a, **k):
            if pathlib.Path(path) == settings:
                return "/nix/store/abc-settings.json"
            return real_realpath(path, *a, **k)

        with mock.patch("os.path.realpath", side_effect=realpath):
            got = [f for f in self.findings(registry)
                   if f.check == "setup.marketplace-missing"]
        self.assertEqual(len(got), 1)
        self.assertIn("managed by Nix", got[0].fix)

    def test_a_cli_registered_marketplace_gets_the_remove_command(self):
        self._declare(["nus"])
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        (plugins / "known_marketplaces.json").write_text(json.dumps({
            "added": {"source": {"source": "directory",
                                 "path": str(self.home / "gone" / "added")}}}))
        got = [f for f in self.findings({"claude": {"command": "claude"}})
               if f.check == "setup.marketplace-missing"]
        self.assertEqual(len(got), 1)
        self.assertIn("drop nus from ~/.claude/settings.json", got[0].fix)
        self.assertIn("claude plugin marketplace remove added", got[0].fix)
        self.assertNotIn("drop added", got[0].fix)

    def test_an_unreadable_harness_says_the_check_did_not_finish(self):
        self._declare(["nus"])
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir()
        (plugins / "installed_plugins.json").write_text("not json")
        got = self.findings({"claude": {"command": "claude", "display_name": "Claude Code"}})
        checks = {f.check: f for f in got}
        self.assertNotIn("setup.marketplace-missing", checks)
        self.assertIn("Couldn't check Claude Code's marketplaces",
                      checks["setup.marketplace-unchecked"].message)

    def test_present_marketplace_folders_are_quiet(self):
        self._declare(["here"], present=["here"])
        got = self.findings({"claude": {"command": "claude"}})
        self.assertNotIn("setup.marketplace-missing", [f.check for f in got])

    def test_json_carries_action(self):
        self.assertEqual(self.findings({})[0].to_json()["action"], "setup")

    def test_warnings_keep_exit_zero(self):
        from quiver.harness.commands import cmd_doctor

        env = mock.Mock(node="/usr/bin/node", npm="/usr/bin/npm",
                        node_version="22", npm_version="10",
                        global_prefix="/usr/local",
                        global_bin="/usr/local/bin", global_bin_on_path=True)
        (self.hub() / "AGENTS.md").write_text("# shared\n")
        (self.home / "harness.json").write_text("{}")
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"HOME": str(self.home)}), \
             mock.patch("quiver.harness.commands.load_registry", return_value={}), \
             mock.patch("quiver.harness.path_health.probe_node_env", return_value=env), \
             mock.patch("quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"), \
             mock.patch("quiver.harness.path_health.nvm_bin_dirs", return_value=[]), \
             mock.patch("quiver.harness.path_health.find_off_path_tools", return_value=[]), \
             mock.patch("quiver.harness.commands.shutil.which", return_value="/usr/bin/npm"), \
             mock.patch("quiver.harness.registry.HARNESS_FILE", self.home / "harness.json"), \
             contextlib.redirect_stdout(out):
            self.assertEqual(cmd_doctor(["--json"]), 0)
        doc = json.loads(out.getvalue())
        checks = {f["check"] for f in doc["data"]["findings"]}
        self.assertIn("setup.registry", checks)

    def test_plain_output_shows_the_setup_section(self):
        from quiver.harness.commands import cmd_doctor

        env = mock.Mock(node="/usr/bin/node", npm="/usr/bin/npm",
                        node_version="22", npm_version="10",
                        global_prefix="/usr/local",
                        global_bin="/usr/local/bin", global_bin_on_path=True)
        self.hub()  # a hub with no AGENTS.md
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"HOME": str(self.home)}), \
             mock.patch("quiver.harness.commands.load_registry", return_value={}), \
             mock.patch("quiver.harness.path_health.probe_node_env", return_value=env), \
             mock.patch("quiver.harness.path_health.preferred_npm_bin", return_value="/usr/bin/npm"), \
             mock.patch("quiver.harness.path_health.nvm_bin_dirs", return_value=[]), \
             mock.patch("quiver.harness.path_health.find_off_path_tools", return_value=[]), \
             mock.patch("quiver.harness.commands.shutil.which", return_value="/usr/bin/npm"), \
             contextlib.redirect_stdout(out):
            cmd_doctor([])
        text = out.getvalue()
        self.assertIn("Setup", text)
        self.assertIn("No shared AGENTS.md", text)
        self.assertIn("swe setup --apply", text)


class IssueFindingsTest(unittest.TestCase):
    """``swe doctor``'s Issues section: the app's IssueStore as findings."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, issues):
        store = self.home / ".quiver" / "data"
        store.mkdir(parents=True)
        (store / "issues.json").write_text(json.dumps(
            {"version": 1, "issues": issues}))

    def test_no_file_is_no_findings(self):
        self.assertEqual(issue_findings(self.home), [])

    def test_a_stale_login_is_a_warning_with_its_fix(self):
        self._write({
            "cursor/auth-required": {
                "harness": "cursor", "status": "auth-required",
                "first_seen": 1.0, "last_notified": 1.0, "occurrences": 2,
            },
        })
        got = issue_findings(self.home)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].check, "usage.cursor/auth-required")
        self.assertEqual(got[0].severity, "warning")
        self.assertEqual(got[0].harness, "cursor")
        # The fix text comes from the REMEDIES table.
        self.assertEqual(got[0].fix, "cursor-agent status")

    def test_an_outage_is_info(self):
        self._write({
            "devin/unavailable": {
                "harness": "devin", "status": "unavailable",
                "occurrences": 4,
            },
        })
        got = issue_findings(self.home)
        self.assertEqual(got[0].severity, "info")

    def test_an_outage_waits_until_it_persists(self):
        # One or two failed polls are a blip the card shows; Doctor lists
        # an outage once it has failed PERSISTENT_POLLS polls in a row.
        for count, listed in ((1, False), (2, False), (3, True)):
            with self.subTest(occurrences=count):
                self.tearDown()
                self.setUp()
                self._write({
                    "devin/timeout": {
                        "harness": "devin", "status": "timeout",
                        "occurrences": count,
                    },
                })
                self.assertEqual(bool(issue_findings(self.home)), listed)

    def test_an_extra_accounts_own_fix_beats_the_table(self):
        # The app stored a fix aimed at the account's own data dir; the
        # table's would sign in the default account instead.
        command = 'XDG_DATA_HOME="/tmp/acct" devin auth login'
        self._write({
            "devin/devin-work/auth-required": {
                "harness": "devin", "status": "auth-required",
                "account": "Work", "accountKey": "devin-work",
                "remedy": {"summary": "This account's Devin API key was rejected",
                           "steps": ["Log in."], "command": command},
            },
        })
        got = issue_findings(self.home)
        self.assertEqual(got[0].fix, command)
        self.assertIn("(Work)", got[0].message)

    def test_an_extra_accounts_outage_takes_the_tables_fix(self):
        # A file from before remedy_for_account stopped retargeting
        # outages may hold a login command for one; the table's retry
        # fix replaces it.
        self._write({
            "devin/devin-work/unavailable": {
                "harness": "devin", "status": "unavailable",
                "account": "Work", "accountKey": "devin-work",
                "occurrences": 3,
                "remedy": {"summary": "This account isn't signed in to Devin",
                           "steps": ["Log in."],
                           "command": 'XDG_DATA_HOME="/tmp/acct" devin auth login'},
            },
        })
        got = issue_findings(self.home)
        self.assertNotIn("auth login", got[0].fix)
        self.assertNotIn("signed in", got[0].message)

    def test_a_permission_fix_is_the_settings_link(self):
        self._write({
            "devin/permission-required": {
                "harness": "devin", "status": "permission-required",
            },
        })
        got = issue_findings(self.home)
        self.assertTrue(got[0].fix.startswith("x-apple.systempreferences:"))

    def test_resolved_and_malformed_rows_are_skipped(self):
        self._write({
            "cursor/auth-required": {"resolved": True, "harness": "cursor",
                                     "status": "auth-required"},
            "junk": "not a dict",
            "broken": {"harness": 7},
        })
        self.assertEqual(issue_findings(self.home), [])

    def test_a_stored_remedy_fills_in_when_the_table_has_none(self):
        self._write({
            "oddball/custom": {
                "harness": "oddball", "status": "custom",
                "remedy": {"summary": "Oddball needs a nudge",
                           "steps": ["Nudge it."]},
            },
        })
        got = issue_findings(self.home)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].message, "Oddball needs a nudge")
        self.assertEqual(got[0].fix, "Nudge it.")

    def test_an_account_names_which_login_is_stale(self):
        self._write({
            "cursor/work/auth-required": {
                "harness": "cursor", "status": "auth-required",
                "account": "work",
            },
        })
        got = issue_findings(self.home)
        self.assertIn("work", got[0].message)


if __name__ == "__main__":
    unittest.main()
