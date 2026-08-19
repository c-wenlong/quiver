"""Tests for the general configuration CLI."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from quiver.config_commands import cmd_config
from quiver.configuration import ConfigurationError


class ConfigCommandsTest(unittest.TestCase):
    def run_command(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cmd_config(args)
        return result, output.getvalue()

    @patch("quiver.config_commands.load_resolved_config", return_value={"report": {"max_workers": 3}})
    def test_show_resolved_config(self, _load):
        result, output = self.run_command([])
        self.assertEqual(result, 0)
        self.assertIn('"max_workers": 3', output)

    @patch("quiver.config_commands.save_config")
    @patch("quiver.config_commands.load_config", return_value={})
    def test_set_parses_json_values(self, _load, save):
        result, _ = self.run_command(["set", "report.session.args", '["--flag"]'])
        self.assertEqual(result, 0)
        self.assertEqual(save.call_args.args[0]["report"]["session"]["args"], ["--flag"])

    @patch("quiver.config_commands.load_resolved_config", return_value={"report": {"max_workers": 3}})
    def test_get_dotted_value(self, _load):
        result, output = self.run_command(["get", "report.max_workers"])
        self.assertEqual(result, 0)
        self.assertEqual(output.strip(), "3")

    @patch("quiver.config_commands.check_config", return_value=[])
    @patch("quiver.config_commands.load_resolved_config", return_value={})
    def test_check_reports_incomplete_setup(self, _load, _check):
        result, output = self.run_command(["check"])
        self.assertEqual(result, 1)
        self.assertIn("setup is incomplete", output)

    @patch("quiver.config_commands.interactive_report_setup", side_effect=ConfigurationError("bad model"))
    @patch("quiver.config_commands.load_config", return_value={})
    def test_setup_failure_is_actionable(self, _load, _setup):
        result, output = self.run_command(["setup", "report"])
        self.assertEqual(result, 1)
        self.assertIn("bad model", output)


if __name__ == "__main__":
    unittest.main()
