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


if __name__ == "__main__":
    unittest.main()
