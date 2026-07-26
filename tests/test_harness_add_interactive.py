import unittest
from unittest.mock import patch

from quiver.harness.commands import cmd_add


def _rl_side_effect(inputs):
    return lambda *_a, **_k: next(inputs)


class HarnessAddInteractiveTest(unittest.TestCase):
    def test_cmd_add_interactive_happy_path(self):
        tools = {}
        inputs = iter(["mytool", "mytool-bin", "My tool", "", "", ""])
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=True
        ), patch(
            "quiver.harness.commands.live_version", return_value="1.2.3"
        ), patch(
            "quiver.harness.commands.read_line", side_effect=_rl_side_effect(inputs)
        ):
            rc = cmd_add(["-i"])
        self.assertEqual(rc, 0)
        save.assert_called_once()
        saved = save.call_args[0][0]
        entry = saved["mytool"]
        self.assertEqual(entry["command"], "mytool-bin")
        self.assertEqual(entry["description"], "My tool")
        self.assertEqual(entry["aliases"], [])
        self.assertEqual(entry["tags"], ["agentic", "coding"])
        self.assertEqual(entry["version"], "1.2.3")

    def test_cmd_add_interactive_cancel(self):
        tools = {}
        inputs = iter(["mytool", "mytool-bin", "", "", "", "c"])
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=False
        ), patch(
            "quiver.harness.commands.live_version", return_value=None
        ), patch(
            "quiver.harness.commands.read_line", side_effect=_rl_side_effect(inputs)
        ):
            rc = cmd_add(["-i"])
        self.assertEqual(rc, 1)
        save.assert_not_called()

    def test_cmd_add_interactive_alias_collision_reprompts(self):
        tools = {
            "augment": {
                "command": "augment",
                "description": "augment",
                "aliases": ["au"],
                "tags": [],
                "version": None,
            }
        }
        # walk: name, command, desc, aliases="au" (collides), tags,
        # re-prompt aliases="my", confirm="" (yes)
        inputs = iter(["mytool", "mytool-bin", "", "au", "", "my", ""])
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=False
        ), patch(
            "quiver.harness.commands.live_version", return_value=None
        ), patch(
            "quiver.harness.commands.read_line", side_effect=_rl_side_effect(inputs)
        ):
            rc = cmd_add(["-i"])
        self.assertEqual(rc, 0)
        save.assert_called_once()
        saved = save.call_args[0][0]
        self.assertEqual(saved["mytool"]["aliases"], ["my"])

    def test_cmd_add_interactive_prefill_name_keeps_default(self):
        tools = {}
        # name pre-filled from "swe add mytool -i"; Enter keeps it.
        inputs = iter(["", "mytool-bin", "", "", "", ""])
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=False
        ), patch(
            "quiver.harness.commands.live_version", return_value=None
        ), patch(
            "quiver.harness.commands.read_line", side_effect=_rl_side_effect(inputs)
        ):
            rc = cmd_add(["mytool", "-i"])
        self.assertEqual(rc, 0)
        save.assert_called_once()
        saved = save.call_args[0][0]
        self.assertIn("mytool", saved)
        self.assertEqual(saved["mytool"]["command"], "mytool-bin")

    def test_cmd_add_interactive_edit_loop_then_save(self):
        tools = {}
        # first pass: confirm "e" → re-walk (Enter keeps defaults) → confirm "y"
        inputs = iter(
            ["mytool", "mytool-bin", "", "", "", "e", "", "", "", "", "", "y"]
        )
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.is_installed", return_value=False
        ), patch(
            "quiver.harness.commands.live_version", return_value=None
        ), patch(
            "quiver.harness.commands.read_line", side_effect=_rl_side_effect(inputs)
        ):
            rc = cmd_add(["-i"])
        self.assertEqual(rc, 0)
        save.assert_called_once()
        saved = save.call_args[0][0]
        self.assertEqual(saved["mytool"]["command"], "mytool-bin")


if __name__ == "__main__":
    unittest.main()
