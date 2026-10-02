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
        code, out = _run(cmd_report, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe report", out)

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


if __name__ == "__main__":
    unittest.main()
