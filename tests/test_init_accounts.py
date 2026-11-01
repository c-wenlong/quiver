"""accounts.sync.*: shared content linked into SWE's extra account homes.

Every test builds a throwaway home with fake account homes under
~/.swe/accounts/<vendor>/<id>/home and a profiles.json naming them.
"""

import json
import os
import unittest
from pathlib import Path

from swe.configuration import validate_config
from swe.init import accounts
from swe.init import commands as init_commands
from swe.init.layout import LinkStatus, agents_file, hooks_dir, skills_dir

from tests._tempdir import temp_dir


def _home(case) -> Path:
    home = temp_dir(case).resolve()
    q = home / ".swe"
    (q / "skills" / "alpha").mkdir(parents=True)
    (q / "skills" / "alpha" / "SKILL.md").write_text("# alpha\n")
    (q / "AGENTS.md").write_text("# shared\n")
    (home / ".claude").mkdir()
    return home


def _profiles(home: Path, *profiles: dict) -> None:
    root = home / ".swe" / "accounts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles.json").write_text(json.dumps({"schema": 1, "profiles": list(profiles)}))
    for p in profiles:
        if not p.get("isDefault"):
            (root / p["vendor"] / p["id"] / "home").mkdir(parents=True, exist_ok=True)


def _acct(home: Path, vendor: str = "claude", pid: str = "claude-work") -> Path:
    return home / ".swe" / "accounts" / vendor / pid / "home"


def _flags(home: Path, **flags) -> None:
    path = home / ".swe" / "config" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accounts": {"sync": flags}}))


def _sync(home: Path, check_only: bool = False):
    rows, notes = accounts.sync_accounts(home, [], {}, check_only=check_only)
    return {(r.primitive, str(r.status.path)): r.result for r in rows}, notes


WORK = {"id": "claude-work", "vendor": "claude", "name": "Work", "isDefault": False}
CODEX = {"id": "codex-alt", "vendor": "codex", "name": "Alt", "isDefault": False}


class SwitchedOffTest(unittest.TestCase):
    def test_default_off_reads_nothing_and_links_nothing(self):
        home = _home(self)
        _profiles(home, WORK)
        rows, notes = _sync(home)
        self.assertEqual((rows, notes), ({}, []))
        self.assertEqual(os.listdir(_acct(home)), [])

    def test_off_never_reads_a_broken_profiles_file(self):
        home = _home(self)
        (home / ".swe" / "accounts").mkdir(parents=True)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        self.assertEqual(_sync(home), ({}, []))


class SwitchedOnTest(unittest.TestCase):
    def test_skills_link_into_every_account_home(self):
        home = _home(self)
        _profiles(home, WORK, CODEX,
                  {"id": "claude-default", "vendor": "claude", "isDefault": True},
                  {"id": "cursor-x", "vendor": "cursor", "isDefault": False},
                  {"id": "../evil", "vendor": "claude", "isDefault": False})
        _flags(home, skills=True)
        rows, _ = _sync(home)
        work, alt = _acct(home) / "skills", _acct(home, "codex", "codex-alt") / "skills"
        self.assertEqual(rows, {("skills", str(work)): "linked", ("skills", str(alt)): "linked"})
        self.assertEqual(os.readlink(work), str(skills_dir(home)))
        self.assertEqual(os.readlink(alt), str(skills_dir(home)))
        self.assertFalse((home / ".claude" / "skills").exists())
        recorded = accounts.load_manifest(home)
        self.assertEqual({e["path"] for e in recorded}, {str(work), str(alt)})
        # A second run is settled and rewrites nothing.
        self.assertEqual(set(_sync(home)[0].values()), {"linked"})

    def test_instructions_use_each_vendors_filename(self):
        home = _home(self)
        _profiles(home, WORK, CODEX)
        _flags(home, instructions=True)
        _sync(home)
        self.assertEqual(os.readlink(_acct(home) / "CLAUDE.md"), str(agents_file(home)))
        codex = _acct(home, "codex", "codex-alt") / "AGENTS.md"
        self.assertEqual(os.readlink(codex), str(agents_file(home)))

    def test_hooks_link_per_script_and_skip_disabled_ones(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (hub / "off.py").write_text("print(2)\n")
        (hub / ".disabled").write_text("off.py\n")
        _sync(home)
        self.assertEqual(os.readlink(_acct(home) / "hooks" / "guard.py"), str(hub / "guard.py"))
        self.assertFalse(os.path.lexists(_acct(home) / "hooks" / "off.py"))

    def test_check_mode_writes_nothing(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True, instructions=True)
        rows, _ = _sync(home, check_only=True)
        self.assertEqual(set(rows.values()), {"would-create"})
        self.assertEqual(os.listdir(_acct(home)), [])
        self.assertFalse(accounts.manifest_file(home).exists())

    def test_missing_account_home_is_skipped(self):
        home = _home(self)
        _profiles(home, WORK)
        _acct(home).rmdir()
        _flags(home, skills=True)
        rows, _ = _sync(home)
        self.assertEqual(set(rows.values()), {"skipped"})

    def test_linkignore_leaves_an_account_target_alone(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        rows, _ = accounts.sync_accounts(
            home, [".swe/accounts/claude/*/home/skills"], {}, check_only=False)
        self.assertEqual([r.result for r in rows], ["ignored"])
        self.assertFalse(os.path.lexists(_acct(home) / "skills"))


class ConflictTest(unittest.TestCase):
    def test_a_real_directory_is_never_replaced_even_with_force(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        own = _acct(home) / "skills" / "mine"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("# mine\n")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        init_commands.run_init(home, force=True, quiet=True, ask=False)
        self.assertTrue((own / "SKILL.md").is_file())
        self.assertFalse((_acct(home) / "skills").is_symlink())

    def test_an_identical_file_is_a_conflict_not_an_absorb(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        (_acct(home) / "CLAUDE.md").write_text("# shared\n")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertFalse((_acct(home) / "CLAUDE.md").is_symlink())

    def test_the_accounts_own_symlink_is_left_alone(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        elsewhere = home / "my-skills"
        elsewhere.mkdir()
        (_acct(home) / "skills").symlink_to(elsewhere)
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(os.readlink(_acct(home) / "skills"), str(elsewhere))

    def test_a_dangling_link_is_repointed(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        (_acct(home) / "skills").symlink_to(home / "gone")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["linked"])
        self.assertEqual(os.readlink(_acct(home) / "skills"), str(skills_dir(home)))

    def test_a_hooks_folder_linked_out_of_the_account_is_not_written_through(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (home / ".claude" / "hooks").mkdir()
        (_acct(home) / "hooks").symlink_to(home / ".claude" / "hooks")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(os.listdir(home / ".claude" / "hooks"), [])


class ReviewFixesTest(unittest.TestCase):
    def test_a_hooks_file_is_a_conflict_not_a_crash(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True, skills=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (_acct(home) / "hooks").write_text("not a folder\n")
        rows, _ = _sync(home)
        self.assertEqual(rows[("hooks", str(_acct(home) / "hooks" / "guard.py"))], "conflict")
        # Work before and after it still lands and is recorded.
        self.assertEqual(rows[("skills", str(_acct(home) / "skills"))], "linked")
        self.assertEqual(len(accounts.load_manifest(home)), 1)

    def test_a_link_failure_becomes_a_conflict_row(self):
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        with mock.patch.object(Path, "symlink_to", side_effect=PermissionError(13, "denied")):
            rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(accounts.load_manifest(home), [])

    def test_a_dotdot_link_into_the_shared_tree_is_the_accounts_own(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        private = home / ".swe" / "private-skills"
        private.mkdir()
        (_acct(home) / "skills").symlink_to(f"{skills_dir(home)}/../private-skills")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])

    def test_unlink_never_follows_a_hooks_folder_swapped_for_a_link(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        _sync(home)
        # The account's hooks/ becomes a link to the default home's, which
        # holds a link with the very same target.
        default_hooks = home / ".claude" / "hooks"
        default_hooks.mkdir()
        (default_hooks / "guard.py").symlink_to(hub / "guard.py")
        acct_hooks = _acct(home) / "hooks"
        (acct_hooks / "guard.py").unlink()
        acct_hooks.rmdir()
        acct_hooks.symlink_to(default_hooks)
        _flags(home, hooks=False)
        rows, _ = _sync(home)
        self.assertEqual(rows, {})
        self.assertTrue((default_hooks / "guard.py").is_symlink())
        self.assertEqual(accounts.load_manifest(home), [])

    def test_a_hooks_folder_linked_within_the_account_is_not_linked_into(self):
        # Cleanup refuses a parent that is a link, so linking must refuse it
        # too, or switching off would leave the links behind.
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        (_acct(home) / "my-hooks").mkdir()
        (_acct(home) / "hooks").symlink_to(_acct(home) / "my-hooks")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["conflict"])
        self.assertEqual(os.listdir(_acct(home) / "my-hooks"), [])

    def test_without_flock_the_pass_still_runs(self):
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        with mock.patch.object(accounts, "fcntl", None):
            rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["linked"])

    def test_a_damaged_record_stops_the_pass(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        record = json.loads(accounts.manifest_file(home).read_text())
        record["links"].append({"path": 3})
        accounts.manifest_file(home).write_text(json.dumps(record))
        _flags(home, skills=False)
        rows, notes = _sync(home)
        self.assertEqual((rows, len(notes)), ({}, 1))
        self.assertTrue((_acct(home) / "skills").is_symlink())
        self.assertIn('"path": 3', accounts.manifest_file(home).read_text())

    def test_check_json_says_why_the_pass_was_skipped(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        run = init_commands.run_init(home, check_only=True, quiet=True)
        doc = init_commands.check_document(home, run)
        self.assertEqual(doc["accounts"], [])
        self.assertEqual(len(doc["account_notes"]), 1)

    def test_defaults_show_every_switch_off(self):
        from swe.configuration import get_value, resolve_config

        self.assertIs(get_value(resolve_config({}), "accounts.sync.skills"), False)


class SwitchOffTest(unittest.TestCase):
    def test_off_removes_only_the_links_swe_made(self):
        home = _home(self)
        _profiles(home, WORK, CODEX)
        _flags(home, skills=True)
        _sync(home)
        # The user relinks the codex account by hand; swe never made that one.
        alt = _acct(home, "codex", "codex-alt") / "skills"
        alt.unlink()
        alt.symlink_to(os.path.relpath(skills_dir(home), alt.parent))
        _flags(home, skills=False)
        rows, _ = _sync(home)
        self.assertEqual(rows, {("skills", str(_acct(home) / "skills")): "unlinked"})
        self.assertFalse(os.path.lexists(_acct(home) / "skills"))
        self.assertTrue(alt.is_symlink())
        self.assertEqual(accounts.load_manifest(home), [])
        # The shared tree itself is untouched.
        self.assertTrue((skills_dir(home) / "alpha" / "SKILL.md").is_file())

    def test_off_in_check_mode_previews_the_unlink(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        _sync(home)
        _flags(home, instructions=False)
        rows, _ = _sync(home, check_only=True)
        self.assertEqual(list(rows.values()), ["would-unlink"])
        self.assertTrue((_acct(home) / "CLAUDE.md").is_symlink())

    def test_a_recorded_link_the_user_replaced_is_forgotten_not_removed(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, instructions=True)
        _sync(home)
        link = _acct(home) / "CLAUDE.md"
        link.unlink()
        link.write_text("# my own\n")
        _flags(home, instructions=False)
        self.assertEqual(_sync(home)[0], {})
        self.assertEqual(link.read_text(), "# my own\n")

    def test_a_disabled_hook_is_unlinked_from_accounts(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, hooks=True)
        hub = hooks_dir(home) / "claude"
        hub.mkdir(parents=True)
        (hub / "guard.py").write_text("print(1)\n")
        _sync(home)
        (hub / ".disabled").write_text("guard.py\n")
        rows, _ = _sync(home)
        self.assertEqual(list(rows.values()), ["unlinked"])
        self.assertTrue((hub / "guard.py").is_file())

    def test_unreadable_profiles_stop_the_pass_instead_of_unlinking(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        _flags(home, skills=False)
        rows, notes = _sync(home)
        self.assertEqual(rows, {})
        self.assertEqual(len(notes), 1)
        self.assertTrue((_acct(home) / "skills").is_symlink())

    def test_unreadable_config_stops_the_pass(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        _sync(home)
        (home / ".swe" / "config" / "config.json").write_text("{nope")
        rows, notes = _sync(home)
        self.assertEqual((rows, len(notes)), ({}, 1))
        self.assertTrue((_acct(home) / "skills").is_symlink())


class SweGuardTest(unittest.TestCase):
    """The ~/.swe guard still refuses absorb into an account home."""

    def test_init_apply_refuses_to_absorb_inside_quiver(self):
        home = _home(self)
        _profiles(home, WORK)
        target = _acct(home) / "CLAUDE.md"
        target.write_text("# shared\n")
        status = LinkStatus("claude", target, "absorb", "same text")
        result = init_commands._apply(status, agents_file(home), home, force=True)
        self.assertEqual(result, "blocked")
        self.assertFalse(target.is_symlink())

    def test_hook_apply_refuses_to_absorb_inside_quiver(self):
        from swe.init.hooks import apply_hook_status

        home = _home(self)
        _profiles(home, WORK)
        target = _acct(home) / "hooks" / "guard.py"
        target.parent.mkdir()
        target.write_text("print(1)\n")
        status = LinkStatus("claude", target, "absorb", "identical copy",
                            home / ".swe" / "hooks" / "claude" / "guard.py")
        self.assertEqual(apply_hook_status(status, home, force=True), "blocked")
        self.assertFalse(target.is_symlink())


class InitIntegrationTest(unittest.TestCase):
    def test_check_json_reports_accounts_and_switches(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        run = init_commands.run_init(home, check_only=True, quiet=True)
        doc = init_commands.check_document(home, run)
        self.assertEqual(doc["account_sync"],
                         {"instructions": False, "skills": True, "hooks": False, "mcp": False})
        self.assertEqual(doc["accounts"], [{
            "account": "claude/claude-work", "primitive": "skills",
            "path": str(_acct(home) / "skills"), "state": "would-create", "detail": "",
        }])
        # Account rows never join the paths --force would replace.
        from swe.webapp.pages.doctor import blocked_paths
        self.assertNotIn(str(_acct(home) / "skills"), blocked_paths(run, home))

    def test_accounts_flag_runs_only_the_account_pass(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=home), redirect_stdout(out):
            code = init_commands.cmd_init(["--accounts", "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(out.getvalue())
        doc = doc.get("data", doc)
        self.assertEqual([r["state"] for r in doc["accounts"]], ["linked"])
        self.assertTrue((_acct(home) / "skills").is_symlink())
        # The default harness homes were not linked by this run.
        self.assertFalse(os.path.lexists(home / ".claude" / "skills"))
        self.assertEqual(init_commands.cmd_init(["--accounts", "--force"]), 1)

    def _cmd(self, home: Path, *args: str) -> tuple[int, str]:
        import io
        import re
        from contextlib import redirect_stdout
        from unittest import mock

        out = io.StringIO()
        with mock.patch.object(Path, "home", return_value=home), redirect_stdout(out):
            code = init_commands.cmd_init(list(args))
        return code, re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue())

    def test_init_prints_accounts_and_names_conflicts(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True, instructions=True)
        (_acct(home) / "CLAUDE.md").write_text("# mine\n")
        code, text = self._cmd(home, "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Accounts", text)
        self.assertIn("Not synced, the account already has its own:", text)
        _flags(home, skills=False)
        code, text = self._cmd(home, "--full")
        self.assertIn("unlinked", text)

    def test_accounts_text_view(self):
        home = _home(self)
        code, text = self._cmd(home, "--accounts")
        self.assertEqual(code, 0)
        self.assertIn("No account sync switched on", text)
        _profiles(home, WORK)
        _flags(home, skills=True)
        code, text = self._cmd(home, "--accounts", "--check")
        self.assertIn("would-create", text)
        (home / ".swe" / "accounts" / "profiles.json").write_text("{nope")
        code, text = self._cmd(home, "--accounts")
        self.assertEqual(code, 1)
        self.assertIn("account sync skipped", text)
        (home / ".swe" / "config" / "config.json").write_text("{nope")
        code, text = self._cmd(home, "--accounts")
        self.assertEqual(code, 1)

    def test_config_set_hints_at_init(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock

        from swe import config_commands

        home = _home(self)
        path = home / ".swe" / "config" / "config.json"
        out = io.StringIO()
        with mock.patch("swe.configuration.CONFIG_FILE", path), redirect_stdout(out):
            config_commands.cmd_config(["set", "accounts.sync.skills", "true"])
            config_commands.cmd_config(["unset", "accounts.sync.skills"])
        self.assertEqual(out.getvalue().count("Run swe init"), 2)

    def test_archived_vendor_is_left_alone(self):
        home = _home(self)
        _profiles(home, WORK)
        _flags(home, skills=True)
        rows, _ = accounts.sync_accounts(
            home, [], {"claude": {"state": "archived"}}, check_only=False)
        self.assertEqual([r.result for r in rows], ["ignored"])


class ConfigValidationTest(unittest.TestCase):
    def test_switches_must_be_booleans_with_known_names(self):
        issues = {i.path for i in validate_config(
            {"accounts": {"sync": {"skills": "yes", "plugins": True, "hooks": False}}})}
        self.assertEqual(issues, {"accounts.sync.skills", "accounts.sync.plugins"})

    def test_sync_must_be_an_object(self):
        issues = validate_config({"accounts": {"sync": True}})
        self.assertEqual([i.path for i in issues], ["accounts.sync"])


if __name__ == "__main__":
    unittest.main()
