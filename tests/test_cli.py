import unittest
from unittest import mock

from quiver.cli import COMMANDS, cmd_providers


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
