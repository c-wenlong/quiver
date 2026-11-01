"""Tests for the general configuration CLI."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from swe.config_commands import cmd_config
from swe.configuration import ConfigurationError


class ConfigCommandsTest(unittest.TestCase):
    def run_command(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_config(args)
        return result, output.getvalue()

    @patch("swe.config_commands.load_resolved_config", return_value={"report": {"max_workers": 3}})
    def test_show_resolved_config(self, _load):
        result, output = self.run_command([])
        self.assertEqual(result, 0)
        self.assertIn('"max_workers": 3', output)

    @patch("swe.config_commands.save_config")
    @patch("swe.config_commands.load_config", return_value={})
    def test_set_parses_json_values(self, _load, save):
        result, _ = self.run_command(["set", "report.session.args", '["--flag"]'])
        self.assertEqual(result, 0)
        self.assertEqual(save.call_args.args[0]["report"]["session"]["args"], ["--flag"])

    @patch("swe.config_commands.load_resolved_config", return_value={"report": {"max_workers": 3}})
    def test_get_dotted_value(self, _load):
        result, output = self.run_command(["get", "report.max_workers"])
        self.assertEqual(result, 0)
        self.assertEqual(output.strip(), "3")

    @patch("swe.config_commands.check_config", return_value=[])
    @patch("swe.config_commands.load_resolved_config", return_value={})
    def test_check_reports_incomplete_setup(self, _load, _check):
        result, output = self.run_command(["check"])
        self.assertEqual(result, 1)
        self.assertIn("setup is incomplete", output)

    @patch("swe.config_commands.interactive_report_setup", side_effect=ConfigurationError("bad model"))
    @patch("swe.config_commands.load_config", return_value={})
    def test_setup_failure_is_actionable(self, _load, _setup):
        result, output = self.run_command(["setup", "report"])
        self.assertEqual(result, 1)
        self.assertIn("bad model", output)


    @patch("swe.config_commands.load_resolved_config", return_value={"report": {"max_workers": 3}})
    def test_get_unknown_key_fails(self, _load):
        result, output = self.run_command(["get", "nope.key"])
        self.assertEqual(result, 1)
        self.assertIn("Unknown configuration key", output)

    def test_get_without_key_is_usage_error(self):
        result, output = self.run_command(["get"])
        self.assertEqual(result, 1)
        self.assertIn("Usage", output)

    @patch("swe.config_commands.load_resolved_config", return_value={"report": {"session": {"args": ["--a"]}}})
    def test_get_prints_dict_values_as_json(self, _load):
        result, output = self.run_command(["get", "report.session"])
        self.assertEqual(result, 0)
        self.assertIn('"args"', output)

    @patch("swe.config_commands.save_config")
    @patch("swe.config_commands.load_config", return_value={"a": {"b": 1}})
    def test_unset_removes_key(self, _load, save):
        result, output = self.run_command(["unset", "a.b"])
        self.assertEqual(result, 0)
        self.assertIn("Unset a.b", output)
        self.assertNotIn("b", save.call_args.args[0].get("a", {}))

    @patch("swe.config_commands.report_setup_complete", return_value=True)
    @patch("swe.config_commands.check_config", return_value=[])
    @patch("swe.config_commands.load_resolved_config", return_value={})
    def test_check_reports_valid_and_complete(self, _load, _check, _complete):
        result, output = self.run_command(["check"])
        self.assertEqual(result, 0)
        self.assertIn("Configuration is valid.", output)

    @patch("swe.config_commands.check_config", return_value=["broken thing"])
    def test_check_reports_issues(self, _check):
        result, output = self.run_command(["check"])
        self.assertEqual(result, 1)
        self.assertIn("broken thing", output)

    @patch("swe.config_commands.edit_config", return_value=0)
    @patch("swe.config_commands.check_config", return_value=[])
    def test_edit_then_check_passes(self, _check, _edit):
        result, _ = self.run_command(["edit"])
        self.assertEqual(result, 0)

    @patch("swe.config_commands.edit_config", return_value=0)
    @patch("swe.config_commands.check_config", return_value=["post-edit issue"])
    def test_edit_then_check_fails(self, _check, _edit):
        result, output = self.run_command(["edit"])
        self.assertEqual(result, 1)
        self.assertIn("post-edit issue", output)

    @patch("swe.config_commands.save_config")
    @patch("swe.config_commands.interactive_report_setup", return_value={"report": {}})
    @patch("swe.config_commands.load_config", return_value={})
    def test_setup_report_saves(self, _load, _setup, save):
        result, output = self.run_command(["setup", "report"])
        self.assertEqual(result, 0)
        save.assert_called_once()
        self.assertIn("Report configuration saved", output)

    def test_unknown_subcommand_fails(self):
        result, output = self.run_command(["bogus"])
        self.assertEqual(result, 1)
        self.assertIn("Unknown config command", output)


if __name__ == "__main__":
    unittest.main()
