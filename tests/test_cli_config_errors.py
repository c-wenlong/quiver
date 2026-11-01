"""A broken config file under ``--json`` must leave stdout empty.

The macOS app decodes a ``--json`` command's stdout whole, so the
dispatcher's ConfigurationError message belongs on stderr there. Without
``--json`` the message stays on stdout, as before.
"""

import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from swe import cli
from swe.configuration import ConfigurationError


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


PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


class CorruptRegistryThroughRealCommandsTest(unittest.TestCase):
    """End to end: a malformed harness.json, the real ``swe`` entry point."""

    def _swe(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            config = pathlib.Path(tmp) / ".quiver" / "config"
            config.mkdir(parents=True)
            (config / "harness.json").write_text("{corrupt")
            env = os.environ.copy()
            env["HOME"] = tmp
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PROJECT_SRC), env.get("PYTHONPATH")]))
            return subprocess.run([sys.executable, "-m", "swe.cli", *args],
                                  env=env, capture_output=True, text=True)

    def test_list_json_keeps_stdout_empty(self):
        r = self._swe("list", "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("Cannot parse JSON", r.stderr)

    def test_mcp_discover_json_keeps_stdout_empty(self):
        r = self._swe("mcp", "discover", "--json")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("Cannot parse JSON", r.stderr)


if __name__ == "__main__":
    unittest.main()
