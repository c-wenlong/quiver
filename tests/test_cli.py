import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import quiver
from quiver.cli import COMMANDS, cmd_providers


def _run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("sys.argv", ["swe", *argv]), \
            redirect_stdout(out), redirect_stderr(err):
        from quiver.cli import main
        status = main()
    return status, out.getvalue(), err.getvalue()


class VersionCommandTest(unittest.TestCase):
    def test_each_spelling_prints_the_version(self):
        for spelling in ("version", "-v", "-V", "--version"):
            with self.subTest(spelling=spelling):
                status, out, err = _run_main([spelling])
                self.assertEqual(status, 0)
                self.assertEqual(out, f"swe {quiver.__version__}\n")
                self.assertEqual(err, "")

    def test_version_is_dispatchable_not_an_alias_hack(self):
        for spelling in ("version", "-v", "-V", "--version"):
            self.assertIs(COMMANDS[spelling], COMMANDS["version"])

    def test_json_envelope(self):
        for argv in (["version", "--json"], ["--version", "--json"]):
            with self.subTest(argv=argv):
                status, out, err = _run_main(argv)
                self.assertEqual(status, 0)
                doc = json.loads(out)
                self.assertEqual(doc["schema"], 1)
                self.assertEqual(doc["data"]["name"], "swe")
                self.assertEqual(doc["data"]["version"], quiver.__version__)
                self.assertIn("python", doc["data"])
                self.assertEqual(err, "")

    def test_extra_args_are_rejected(self):
        status, out, err = _run_main(["-v", "extra"])
        self.assertEqual(status, 1)
        self.assertIn("Unexpected args: extra", out + err)

    def test_works_when_config_is_broken(self):
        # Patch the path registry.py actually reads, the way
        # test_registry_state.py isolates ~/.quiver: the module constants
        # are bound at import, so HOME alone proves nothing.
        from quiver import cli
        from quiver.configuration import ConfigurationError
        from quiver.harness import registry
        with tempfile.TemporaryDirectory() as home:
            harness_file = (pathlib.Path(home) / ".quiver" / "config"
                            / "harness.json")
            harness_file.parent.mkdir(parents=True)
            harness_file.write_text("{corrupt")
            with mock.patch.object(registry, "HARNESS_FILE", harness_file):
                with self.assertRaises(ConfigurationError):
                    registry.load_registry()
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    status = cli.cmd_version([])
        self.assertEqual(status, 0)
        self.assertEqual(out.getvalue(), f"swe {quiver.__version__}\n")

    def test_version_help_routes_through_cmd_help(self):
        for spelling in ("version", "-v", "-V", "--version"):
            with self.subTest(spelling=spelling):
                status, out, err = _run_main([spelling, "--help"])
                self.assertEqual(status, 0)
                self.assertIn("swe version", out)


class VersionCompletionTest(unittest.TestCase):
    def test_version_is_in_primary_completions(self):
        from quiver.completion import _PRIMARY_COMMANDS
        names = [name for name, _ in _PRIMARY_COMMANDS]
        self.assertIn("version", names)

    def test_version_flag_completion_offers_json(self):
        from quiver.completion import get_completions
        candidates = [name for name, _ in get_completions(["version", "--"])]
        self.assertIn("--json", candidates)


class ProvidersCliRoutingTest(unittest.TestCase):
    def test_cmd_providers_forwards_arguments_and_status(self):
        with mock.patch("quiver.cli.providers_cli.main", return_value=7) as provider_main:
            result = cmd_providers(["info", "anthropic"])

        self.assertEqual(result, 7)
        provider_main.assert_called_once_with(["info", "anthropic"])

    def test_provider_command_and_alias_share_the_same_router(self):
        self.assertIs(COMMANDS["providers"], cmd_providers)
        self.assertIs(COMMANDS["pv"], cmd_providers)


class ProvidersHelpRouteTest(unittest.TestCase):
    """`swe providers --help` reaches the group's own fuller help, not
    the summary that ends by pointing back at `swe providers --help`."""

    def test_providers_help_bypasses_top_level_summary(self):
        import sys
        from quiver.cli import main

        for argv in (["swe", "providers", "--help"], ["swe", "pv", "-h"]):
            with self.subTest(argv=argv):
                with mock.patch.object(sys, "argv", argv), mock.patch(
                    "quiver.cli.providers_cli.main", return_value=0
                ) as provider_main:
                    result = main()
                self.assertEqual(result, 0)
                provider_main.assert_called_once_with([argv[2]])


if __name__ == "__main__":
    unittest.main()
