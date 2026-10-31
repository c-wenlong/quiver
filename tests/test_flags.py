"""Tests for the shared ``--name=value`` argv normaliser."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from quiver.find.commands import cmd_find
from quiver.flags import expand_value_flags
from quiver.harness.commands import (
    _parse_edit_flags,
    cmd_add,
    cmd_archive,
    cmd_edit,
    cmd_install,
    cmd_list,
)
from quiver.providers.commands import cmd_add as provider_add
from quiver.providers.commands import cmd_info as provider_info
from quiver.providers.commands import cmd_list as provider_list
from quiver.reports.commands import _followup, _parse_generate_args, cmd_report


def _run(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(list(args))
    return code, buf.getvalue()


class ExpandValueFlagsTest(unittest.TestCase):
    def test_pairs_and_passthrough(self):
        args = expand_value_flags(
            ["amd", "--scope=all", "--full", "-r", "--harness=active"],
            {"--scope", "--harness"},
        )
        self.assertEqual(
            args, ["amd", "--scope", "all", "--full", "-r", "--harness", "active"]
        )

    def test_value_may_contain_equals(self):
        args = expand_value_flags(["--session-arg=--server=local"], {"--session-arg"})
        self.assertEqual(args, ["--session-arg", "--server=local"])

    def test_comma_list_is_one_value(self):
        args = expand_value_flags(["--tags=a,b,c"], {"--tags"})
        self.assertEqual(args, ["--tags", "a,b,c"])

    def test_empty_value_is_kept(self):
        args = expand_value_flags(["--scope="], {"--scope"})
        self.assertEqual(args, ["--scope", ""])

    def test_bare_flag_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            expand_value_flags(["--agent", "claude"], {"--agent"})
        self.assertIn("--agent=<value>", str(ctx.exception))

    def test_similar_prefix_does_not_match(self):
        args = expand_value_flags(["--agents=x"], {"--agent"})
        self.assertEqual(args, ["--agents=x"])


class BareFlagRejectionTest(unittest.TestCase):
    """Every domain prints ``--name=<value>`` and exits 1 on the space form."""

    def test_find_scope(self):
        code, out = _run(cmd_find, ["--scope"])
        self.assertEqual(code, 1)
        self.assertIn("--scope=<value>", out)

    def test_find_harness(self):
        code, out = _run(cmd_find, ["--harness"])
        self.assertEqual(code, 1)
        self.assertIn("--harness=<value>", out)

    def test_find_harness_bad_value(self):
        code, out = _run(cmd_find, ["--harness=bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown harness state", out)

    def test_list_scope(self):
        code, out = _run(cmd_list, ["--scope"])
        self.assertEqual(code, 1)
        self.assertIn("--scope=<value>", out)

    def test_archive_usage(self):
        with patch("quiver.harness.archive.load_archive", return_value={}):
            code, out = _run(cmd_archive, ["--usage"])
        self.assertEqual(code, 1)
        self.assertIn("--usage=<value>", out)

    def test_archive_bad_usage_level(self):
        with patch("quiver.harness.archive.load_archive", return_value={}):
            code, out = _run(cmd_archive, ["demo", "--usage=enormous"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown usage level", out)

    def test_archive_usage_without_name(self):
        with patch("quiver.harness.archive.load_archive", return_value={}):
            code, out = _run(cmd_archive, ["--usage=heavy"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe hs archive", out)

    def test_add_aliases(self):
        code, out = _run(cmd_add, ["demo", "demo-cmd", "--aliases"])
        self.assertEqual(code, 1)
        self.assertIn("--aliases=<value>", out)

    def test_add_too_few_args(self):
        code, out = _run(cmd_add, ["demo"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe add", out)

    def test_install_package(self):
        code, out = _run(cmd_install, ["demo", "--package"])
        self.assertEqual(code, 1)
        self.assertIn("--package=<value>", out)

    def test_install_no_args(self):
        code, out = _run(cmd_install, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe install", out)

    def test_edit_no_args(self):
        code, out = _run(cmd_edit, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe edit", out)

    def test_edit_flags_only(self):
        code, out = _run(cmd_edit, ["--description=x"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe edit", out)

    def test_edit_unknown_flag(self):
        code, out = _run(cmd_edit, ["demo", "--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag: --bogus", out)

    def test_edit_flags_bare_field(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_edit_flags(["demo", "--tags"])
        self.assertIn("--tags=<value>", str(ctx.exception))

    def test_edit_flags_equals_form(self):
        updates, rest = _parse_edit_flags(
            ["demo", "--description=new", "--set=version=2.0"]
        )
        self.assertEqual(rest, ["demo"])
        self.assertEqual(updates["description"], "new")
        self.assertEqual(updates["version"], "2.0")

    def test_providers_list_keys_dir(self):
        code, out = _run(provider_list, ["--api-keys-dir"])
        self.assertEqual(code, 1)
        self.assertIn("--api-keys-dir=<value>", out)

    def test_providers_info_keys_dir(self):
        code, out = _run(provider_info, ["demo", "--api-keys-dir"])
        self.assertEqual(code, 1)
        self.assertIn("--api-keys-dir=<value>", out)

    def test_providers_add_env(self):
        code, out = _run(provider_add, ["myprov", "--env"])
        self.assertEqual(code, 1)
        self.assertIn("--env=<value>", out)

    def test_report_no_args(self):
        # Bare `swe report` prints help and exits 0, same as swe mcp /
        # providers / skills / config.
        code, out = _run(cmd_report, [])
        self.assertEqual(code, 0)
        self.assertIn("swe report daily", out)

    def test_report_help_subcommand(self):
        code, out = _run(cmd_report, ["help"])
        self.assertEqual(code, 0)
        self.assertIn("swe report daily", out)

    def test_report_daily_help_flag(self):
        code, out = _run(cmd_report, ["daily", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("swe report daily", out)

    def test_report_daily_help_wins_over_bad_args(self):
        # `--days=0` fails validation, but a user passing --help alongside
        # it is asking for help, not a report run.
        code, out = _run(cmd_report, ["daily", "--help", "--days=0"])
        self.assertEqual(code, 0)
        self.assertIn("swe report daily", out)

    def test_report_followup_dispatch(self):
        ledger = Mock()
        ledger.get.return_value = None
        with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger):
            code, out = _run(cmd_report, ["followup", "work", "missing"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown follow-up", out)

    def test_report_followups_status(self):
        code, out = _run(cmd_report, ["followups", "--status"])
        self.assertEqual(code, 1)
        self.assertIn("--status=<value>", out)

    def test_report_followups_extra_arg(self):
        code, out = _run(cmd_report, ["followups", "--status=done", "junk"])
        self.assertEqual(code, 1)
        self.assertIn("--status=open|done|dismissed", out)

    def test_report_followups_status_done_lists(self):
        ledger = Mock()
        ledger.list.return_value = []
        with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger):
            code, _ = _run(cmd_report, ["followups", "--status=done"])
        self.assertEqual(code, 0)
        ledger.list.assert_called_once_with(status="done")

    def test_report_followups_bad_status(self):
        ledger = Mock()
        ledger.list.side_effect = ValueError("bad status")
        with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger):
            code, out = _run(cmd_report, ["followups", "--status=bogus"])
        self.assertEqual(code, 1)
        self.assertIn("bad status", out)

    def test_followup_bare_project(self):
        with patch("quiver.reports.commands.FollowUpLedger", return_value=Mock()):
            code, out = _run(_followup, ["add", "thing", "--project"])
        self.assertEqual(code, 1)
        self.assertIn("--project=<value>", out)

    def test_followup_work_harness_equals_form(self):
        item = Mock()
        ledger = Mock()
        ledger.get.return_value = item
        with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger), patch(
            "quiver.reports.commands.work_on_follow_up", return_value=0
        ) as work:
            code, _ = _run(_followup, ["work", "fu_1", "--new", "--harness=claude"])
        self.assertEqual(code, 0)
        work.assert_called_once_with(item, mode="new", harness="claude")

    def test_followup_edit_keeps_literal_flag_text(self):
        ledger = Mock()
        with patch("quiver.reports.commands.FollowUpLedger", return_value=ledger):
            code, _ = _run(
                _followup, ["edit", "fu_1", "note", "about", "--harness=x"]
            )
        self.assertEqual(code, 0)
        ledger.edit.assert_called_once_with("fu_1", text="note about --harness=x")

    def test_generate_args_bare_days(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_generate_args(["--days"])
        self.assertIn("--days=<value>", str(ctx.exception))

    def test_generate_args_short_flag_without_value(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_generate_args(["-d"])
        self.assertIn("positive integer", str(ctx.exception))

    def test_generate_args_bad_days_friendly(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_generate_args(["--days=x"])
        self.assertIn("positive integer", str(ctx.exception))

    def test_generate_args_bad_weeks_friendly(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_generate_args(["--weeks=x"])
        self.assertIn("positive integer", str(ctx.exception))

    def test_generate_args_equals_pairs(self):
        parsed = _parse_generate_args([
            "--start=2026-07-01", "--end=2026-07-30",
            "--agent=claude", "--search=login", "--writer-arg=--fast",
        ])
        self.assertEqual(parsed.start, "2026-07-01")
        self.assertEqual(parsed.end, "2026-07-30")
        self.assertEqual(parsed.agent, "claude")
        self.assertEqual(parsed.search, "login")
        self.assertEqual(parsed.writer_args, ["--fast"])

    def test_add_equals_form_consumes_flags(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            code, out = _run(
                cmd_add,
                ["demo", "demo-cmd", "--aliases=a,b", "--tags=t1,t2", "a desc"],
            )
        saved = save.call_args[0][0]["demo"]
        self.assertEqual(saved["aliases"], ["a", "b"])
        self.assertEqual(saved["tags"], ["t1", "t2"])
        self.assertEqual(saved["description"], "a desc")
        self.assertIn("Added 'demo'", out)

    def test_re_adding_a_harness_keeps_its_display_name(self):
        existing = {"demo": {"command": "old", "display_name": "Demo Code"}}
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _, out = _run(cmd_add, ["demo", "demo-cmd"])
        saved = save.call_args[0][0]["demo"]
        self.assertEqual(saved["command"], "demo-cmd")
        self.assertEqual(saved["display_name"], "Demo Code")
        self.assertIn("Updated 'demo'", out)

    def test_re_adding_a_harness_keeps_its_colour(self):
        existing = {"demo": {"command": "old", "primary_color": "#112233"}}
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "demo-cmd"])
        self.assertEqual(save.call_args[0][0]["demo"]["primary_color"], "#112233")

    def test_re_adding_keeps_state_pin_capabilities_and_unknown_fields(self):
        # A re-add must never silently drop registry vocabulary (state, pin,
        # capabilities) or user fields the command does not own.
        existing = {
            "demo": {
                "command": "demo-cmd",
                "description": "ma description",
                "state": "starred",
                "pin": 2,
                "capabilities": {"skills": {"supported": True, "root": "~/.demo/skills"}},
                "notes": "à garder",
                "discovered_via": "init",
                "custom_field": "x",
                "tags": ["custom-tag"],
                "aliases": ["dd"],
                "version": "9.9",
                "added": "2020-01-01T00:00:00",
            }
        }
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "demo-cmd"])
        saved = save.call_args[0][0]["demo"]
        self.assertEqual(saved["command"], "demo-cmd")
        self.assertEqual(saved["state"], "starred")
        self.assertEqual(saved["pin"], 2)
        self.assertEqual(saved["capabilities"]["skills"]["root"], "~/.demo/skills")
        self.assertEqual(saved["notes"], "à garder")
        self.assertEqual(saved["discovered_via"], "init")
        self.assertEqual(saved["custom_field"], "x")
        self.assertEqual(saved["description"], "ma description")
        self.assertEqual(saved["tags"], ["custom-tag"])
        self.assertEqual(saved["aliases"], ["dd"])
        self.assertEqual(saved["version"], "9.9")
        self.assertEqual(saved["added"], "2020-01-01T00:00:00")

    def test_re_adding_archived_entry_keeps_state_and_notes_it(self):
        existing = {"demo": {"command": "old", "state": "archived"}}
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _, out = _run(cmd_add, ["demo", "demo-cmd"])
        self.assertEqual(save.call_args[0][0]["demo"]["state"], "archived")
        self.assertIn("still archived", out)

    def test_re_adding_with_explicit_tags_overrides_only_tags(self):
        existing = {
            "demo": {"command": "old", "state": "starred", "tags": ["custom-tag"]}
        }
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "demo-cmd", "--tags=nouveau"])
        saved = save.call_args[0][0]["demo"]
        self.assertEqual(saved["tags"], ["nouveau"])
        self.assertEqual(saved["state"], "starred")

    def test_re_adding_a_different_command_drops_the_stale_version(self):
        # The stored version belongs to the probed binary: re-adding to
        # point at another command must not show the old binary's version.
        existing = {"demo": {"command": "old-bin", "version": "9.9"}}
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "new-bin"])
        self.assertIsNone(save.call_args[0][0]["demo"]["version"])

    def test_re_adding_with_empty_description_clears_it(self):
        # `--description=` is an explicit clear, not "not supplied".
        existing = {"demo": {"command": "demo-cmd", "description": "old text"}}
        with patch("quiver.harness.commands.load_registry", return_value=existing), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "demo-cmd", "--description="])
        self.assertEqual(save.call_args[0][0]["demo"]["description"], "")

    def test_install_onto_a_new_command_drops_the_old_version(self):
        # Same rule as cmd_add: when --command points the entry at a
        # different binary and no version probe returns a value, the old
        # binary's version must not survive on the saved entry.
        import subprocess as sp

        from quiver.harness.installers import Installer

        existing = {"demo": {"command": "old-bin", "version": "9.9"}}
        done = sp.CompletedProcess(args=[], returncode=0)

        def fake_which(tool):
            return "/usr/bin/brew" if tool == "brew" else None

        with patch(
            "quiver.harness.commands.load_registry_readonly",
            return_value=existing,
        ), patch(
            "quiver.harness.commands.load_registry",
            return_value=dict(existing),
        ), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.installers.installers_for",
            return_value=[Installer("brew", "demo-pkg")],
        ), patch(
            "quiver.harness.commands.shutil.which", side_effect=fake_which
        ), patch(
            "quiver.harness.commands.subprocess.run", return_value=done
        ):
            _run(cmd_install, ["demo", "--via=brew", "--command=new-bin"])
        saved = save.call_args[0][0]["demo"]
        self.assertEqual(saved["command"], "new-bin")
        self.assertIsNone(saved["version"])

    def test_adding_a_new_harness_sets_no_display_name(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "demo-cmd"])
        self.assertNotIn("display_name", save.call_args[0][0]["demo"])

    def test_add_description_flag_sets_description(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ):
            _run(cmd_add, ["demo", "demo-cmd", "--description=from flag"])
        self.assertEqual(save.call_args[0][0]["demo"]["description"], "from flag")

    def test_add_dash_i_value_does_not_go_interactive(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ), patch(
            "quiver.harness.commands._add_interactive"
        ) as interactive:
            _run(cmd_add, ["demo", "demo-cmd", "--description=-i"])
        interactive.assert_not_called()
        self.assertEqual(save.call_args[0][0]["demo"]["description"], "-i")

    def test_add_command_flag_conflicts_with_positional(self):
        code, out = _run(cmd_add, ["demo", "demo-cmd", "--command=baz"])
        self.assertEqual(code, 1)
        self.assertIn("--command only prefills", out)

    def test_add_flag_as_command_is_usage_error(self):
        code, out = _run(cmd_add, ["demo", "--aliases=a"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe add", out)

    def test_list_scope_value_spelling_a_boolean_flag_is_not_smuggled(self):
        # `--scope=-u` expands to ("--scope", "-u"); the -u must be consumed
        # as the scope value, not stripped as --usage, which used to leave a
        # bare --scope and crash on args[i+1].
        code, out = _run(cmd_list, ["--scope=-u"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown scope", out)

    def test_list_exclude_value_spelling_a_flag_does_not_refresh(self):
        # A refresh run would clear the real session cache and re-poll
        # provider rate limits, so the refresh side-effects are patched.
        with (
            patch("quiver.sessions.aggregator.invalidate_cache") as inv_sessions,
            patch(
                "quiver.sessions.usage.invalidate_counts_cache"
            ) as inv_counts,
            patch("quiver.harness.commands._fetch_rate_limits"),
        ):
            code, _ = _run(cmd_list, ["--exclude=-r"])
        self.assertEqual(code, 0)
        inv_sessions.assert_not_called()
        inv_counts.assert_not_called()

    def test_list_real_boolean_flags_still_work(self):
        with (
            patch("quiver.sessions.aggregator.invalidate_cache"),
            patch(
                "quiver.sessions.usage.invalidate_counts_cache"
            ) as invalidate,
            patch("quiver.harness.commands._fetch_rate_limits"),
        ):
            code, _ = _run(cmd_list, ["-r"])
        self.assertEqual(code, 0)
        invalidate.assert_called_once()

    def test_list_subcommand_dispatch_still_precedes_flag_parsing(self):
        # `edit`/`legend` dispatch on the untouched argv: `-u edit` keeps
        # treating "edit" as a tag filter, and a real `edit` call hands its
        # own argv to cmd_list_edit, which rejects list-only flags.
        with patch("quiver.harness.commands._fetch_rate_limits"):
            code, out = _run(cmd_list, ["-u", "edit"])
        self.assertEqual(code, 0)
        code, out = _run(cmd_list, ["edit", "--reset", "-r"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args", out)


if __name__ == "__main__":
    unittest.main()
