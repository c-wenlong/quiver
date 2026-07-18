import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.commands import (
    _apply_edits,
    _parse_edit_flags,
    cmd_edit,
)


class HarnessEditTest(unittest.TestCase):
    def test_parse_edit_flags(self):
        updates, rest = _parse_edit_flags(
            ["mastracode", "--description", "Mastra", "--aliases", "mc,ms"]
        )
        self.assertEqual(rest, ["mastracode"])
        self.assertEqual(updates["description"], "Mastra")
        self.assertEqual(updates["aliases"], "mc,ms")

        updates, rest = _parse_edit_flags(
            ["droid", "--set", "tags=agentic,coding,autonomous,notes=hi"]
        )
        self.assertEqual(rest, ["droid"])
        self.assertEqual(updates["tags"], "agentic,coding,autonomous")
        self.assertEqual(updates["notes"], "hi")

    def test_apply_edits_updates_description_and_aliases(self):
        tools = {
            "mastracode": {
                "command": "mastracode",
                "description": "mc",
                "aliases": [],
                "tags": ["coding"],
            },
            "claude": {
                "command": "claude",
                "description": "Claude",
                "aliases": ["cc"],
                "tags": ["coding"],
            },
        }
        new_info, changes = _apply_edits(
            tools,
            "mastracode",
            {"description": "Mastra Code — AI coding agent", "aliases": "mc"},
        )
        self.assertEqual(new_info["description"], "Mastra Code — AI coding agent")
        self.assertEqual(new_info["aliases"], ["mc"])
        self.assertTrue(any("description" in line for line in changes))
        self.assertTrue(any("aliases" in line for line in changes))

    def test_apply_edits_rejects_alias_collision(self):
        tools = {
            "mastracode": {"command": "mastracode", "aliases": [], "tags": []},
            "claude": {"command": "claude", "aliases": ["cc"], "tags": []},
        }
        with self.assertRaises(ValueError) as ctx:
            _apply_edits(tools, "mastracode", {"aliases": "cc"})
        self.assertIn("cc", str(ctx.exception))

    def test_apply_edits_rejects_empty_command(self):
        tools = {"droid": {"command": "droid", "aliases": [], "tags": []}}
        with self.assertRaises(ValueError):
            _apply_edits(tools, "droid", {"command": "  "})

    def test_cmd_edit_flag_mode_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            registry_file = config_dir / "tools.json"
            config_dir.mkdir(parents=True)
            registry_file.write_text(
                json.dumps(
                    {
                        "mastracode": {
                            "command": "mastracode",
                            "description": "mc",
                            "aliases": [],
                            "tags": ["coding"],
                            "version": None,
                        }
                    }
                )
            )
            with patch("quiver.harness.registry.CONFIG_DIR", config_dir), patch(
                "quiver.harness.registry.REGISTRY_FILE", registry_file
            ), patch("quiver.harness.commands.load_registry") as load, patch(
                "quiver.harness.commands.save_registry"
            ) as save, patch("quiver.harness.commands.resolve", return_value="mastracode"):
                tools = json.loads(registry_file.read_text())
                load.return_value = tools

                def _save(t):
                    registry_file.write_text(json.dumps(t, indent=2))

                save.side_effect = _save
                rc = cmd_edit(
                    [
                        "mastracode",
                        "--description",
                        "Mastra Code — AI coding agent",
                        "--aliases",
                        "mc",
                    ]
                )
                self.assertEqual(rc, 0)
                saved = json.loads(registry_file.read_text())
                self.assertEqual(saved["mastracode"]["description"], "Mastra Code — AI coding agent")
                self.assertEqual(saved["mastracode"]["aliases"], ["mc"])

    def test_cmd_edit_interactive_save(self):
        tools = {
            "tau": {
                "command": "tau",
                "description": "tau",
                "aliases": [],
                "tags": ["coding"],
                "version": None,
            }
        }
        inputs = iter(["description", "Tau — coding agent", "save"])
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.save_registry"
        ) as save, patch(
            "quiver.harness.commands.resolve", return_value="tau"
        ), patch("quiver.harness.commands.read_line", side_effect=lambda *_a, **_k: next(inputs)):
            rc = cmd_edit(["tau"])
            self.assertEqual(rc, 0)
            save.assert_called_once()
            saved_tools = save.call_args[0][0]
            self.assertEqual(saved_tools["tau"]["description"], "Tau — coding agent")


if __name__ == "__main__":
    unittest.main()
