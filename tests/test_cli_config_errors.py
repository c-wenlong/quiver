"""A broken config file under ``--json`` must leave stdout empty.

The macOS app decodes a ``--json`` command's stdout whole, so the
dispatcher's ConfigurationError message belongs on stderr there. Without
``--json`` the message stays on stdout, as before.
"""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from quiver import cli
from quiver.configuration import ConfigurationError


def _run(argv):
    def broken(_args):
        raise ConfigurationError("harness.json is not valid JSON")

    out, err = io.StringIO(), io.StringIO()
    with patch.dict(cli.COMMANDS, {argv[0]: broken}), patch("sys.argv", ["swe", *argv]), \
            redirect_stdout(out), redirect_stderr(err):
        status = cli.main()
    return status, out.getvalue(), err.getvalue()


class DispatcherConfigErrorTest(unittest.TestCase):
    def test_json_command_reports_on_stderr_with_empty_stdout(self):
        status, out, err = _run(["list", "--json"])
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("harness.json is not valid JSON", err)

    def test_json_flag_anywhere_in_the_arguments_counts(self):
        status, out, err = _run(["skills", "list", "--json", "--all"])
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertIn("not valid JSON", err)

    def test_plain_command_keeps_the_message_on_stdout(self):
        status, out, err = _run(["list"])
        self.assertEqual(status, 1)
        self.assertIn("harness.json is not valid JSON", out)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()
