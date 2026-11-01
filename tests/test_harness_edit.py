import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from swe.harness.commands import (
    _apply_edits,
    _parse_edit_flags,
    _show_edit_fields,
    cmd_edit,
)


class HarnessEditTest(unittest.TestCase):
    def test_parse_edit_flags(self):
        updates, rest = _parse_edit_flags(
            ["mastracode", "--description=Mastra", "--aliases=mc,ms"]
        )
        self.assertEqual(rest, ["mastracode"])
        self.assertEqual(updates["description"], "Mastra")
        self.assertEqual(updates["aliases"], "mc,ms")

        updates, rest = _parse_edit_flags(
            ["droid", "--set=tags=agentic,coding,autonomous,notes=hi"]
        )
        self.assertEqual(rest, ["droid"])
        self.assertEqual(updates["tags"], "agentic,coding,autonomous")
        self.assertEqual(updates["notes"], "hi")

    def test_parse_edit_flags_accepts_display_name_spellings(self):
        for flag in ("--display-name=Qwen Code", "--display_name=Qwen Code"):
            updates, rest = _parse_edit_flags(["qwen", flag])
            self.assertEqual(rest, ["qwen"])
            self.assertEqual(updates, {"display_name": "Qwen Code"})
        updates, _ = _parse_edit_flags(["qwen", "--set=display_name=Qwen Code,tags=a,b"])
        self.assertEqual(updates, {"display_name": "Qwen Code", "tags": "a,b"})
        updates, _ = _parse_edit_flags(["qwen", "--set=display-name=Qwen Code"])
        self.assertEqual(updates, {"display_name": "Qwen Code"})
        with self.assertRaises(ValueError):
            _parse_edit_flags(["qwen", "--display-name"])

    def test_apply_edits_sets_and_clears_display_name(self):
        tools = {"qwen": {"command": "qwen", "aliases": [], "tags": []}}
        new_info, changes = _apply_edits(tools, "qwen", {"display_name": "  Qwen Code "})
        self.assertEqual(new_info["display_name"], "Qwen Code")
        self.assertEqual(changes, ["display_name: — → Qwen Code"])
        tools["qwen"] = new_info
        cleared, changes = _apply_edits(tools, "qwen", {"display_name": ""})
        self.assertNotIn("display_name", cleared)
        self.assertEqual(changes, ["display_name: Qwen Code → —"])
        _, changes = _apply_edits({"qwen": cleared}, "qwen", {"display_name": " "})
        self.assertEqual(changes, [])

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

    def test_parse_edit_flags_accepts_primary_color(self):
        for flag in ("--primary-color=#d97757", "--primary_color=#d97757"):
            updates, rest = _parse_edit_flags(["claude", flag])
            self.assertEqual(rest, ["claude"])
            self.assertEqual(updates, {"primary_color": "#d97757"})
        updates, rest = _parse_edit_flags(["claude", "--primary-color="])
        self.assertEqual(updates, {"primary_color": ""})
        with self.assertRaises(ValueError):
            _parse_edit_flags(["claude", "--primary-color"])

    def test_apply_edits_sets_and_normalises_primary_color(self):
        tools = {"claude": {"command": "claude", "aliases": [], "tags": []}}
        for raw in ("#d97757", "d97757", "#D97757", " D97757 "):
            new_info, changes = _apply_edits(tools, "claude", {"primary_color": raw})
            self.assertEqual(new_info["primary_color"], "#D97757", raw)

    def test_apply_edits_clears_primary_color(self):
        tools = {"claude": {"command": "claude", "primary_color": "#123456",
                            "aliases": [], "tags": []}}
        new_info, changes = _apply_edits(tools, "claude", {"primary_color": ""})
        self.assertNotIn("primary_color", new_info)
        self.assertEqual(changes, ["primary_color: #123456 → —"])

    def test_apply_edits_rejects_a_bad_colour(self):
        tools = {"claude": {"command": "claude", "aliases": [], "tags": []}}
        for raw in ("red", "#12345", "1234567", "#GGHHII", "##D97757", "0xD97757"):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as ctx:
                _apply_edits(tools, "claude", {"primary_color": raw})
            self.assertIn("Use a colour like #D97757.", str(ctx.exception))

    def test_cmd_edit_flag_mode_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
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
            with patch("swe.harness.registry.CONFIG_DIR", config_dir), patch(
                "swe.harness.registry.HARNESS_FILE", registry_file
            ), patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"
            ), patch("swe.harness.commands.load_registry") as load, patch(
                "swe.harness.commands.save_registry"
            ) as save, patch("swe.harness.commands.resolve", return_value="mastracode"):
                tools = json.loads(registry_file.read_text())
                load.return_value = tools

                def _save(t):
                    registry_file.write_text(json.dumps(t, indent=2))

                save.side_effect = _save
                rc = cmd_edit(
                    [
                        "mastracode",
                        "--description=Mastra Code — AI coding agent",
                        "--aliases=mc",
                    ]
                )
                self.assertEqual(rc, 0)
                saved = json.loads(registry_file.read_text())
                self.assertEqual(saved["mastracode"]["description"], "Mastra Code — AI coding agent")
                self.assertEqual(saved["mastracode"]["aliases"], ["mc"])

    def test_cmd_edit_primary_color_persists_and_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            config_dir.mkdir(parents=True)
            registry_file.write_text(
                json.dumps({"claude": {"command": "claude", "aliases": [], "tags": []}})
            )
            with patch("swe.harness.commands.load_registry") as load, patch(
                "swe.harness.commands.save_registry"
            ) as save, patch("swe.harness.commands.resolve", return_value="claude"):
                load.side_effect = lambda: json.loads(registry_file.read_text())

                def _save(t):
                    registry_file.write_text(json.dumps(t, indent=2))

                save.side_effect = _save

                rc = cmd_edit(["claude", "--primary-color=d97757"])
                self.assertEqual(rc, 0)
                saved = json.loads(registry_file.read_text())
                self.assertEqual(saved["claude"]["primary_color"], "#D97757")

                rc = cmd_edit(["claude", "--primary-color="])
                self.assertEqual(rc, 0)
                saved = json.loads(registry_file.read_text())
                self.assertNotIn("primary_color", saved["claude"])

    def test_cmd_edit_rejects_a_bad_colour(self):
        tools = {"claude": {"command": "claude", "aliases": [], "tags": []}}
        with patch("swe.harness.commands.load_registry", return_value=tools), patch(
            "swe.harness.commands.save_registry"
        ) as save, patch("swe.harness.commands.resolve", return_value="claude"):
            rc = cmd_edit(["claude", "--primary-color=notacolour"])
            self.assertEqual(rc, 1)
            save.assert_not_called()

    def test_cmd_edit_interactive_alias_collision_continues_loop(self):
        """When a user saves an alias that collides, the loop continues instead of exiting."""
        tools = {
            "tau": {
                "command": "tau",
                "description": "tau",
                "aliases": [],
                "tags": ["coding"],
                "version": None,
            },
            "augment": {
                "command": "augment",
                "description": "augment",
                "aliases": ["au"],
                "tags": ["coding"],
                "version": None,
            },
        }
        # 1st save: colliding alias 'au' → loop continues
        # 2nd: re-enter aliases field with 'ta'
        # 3rd save: succeeds
        inputs = iter(["aliases", "au", "save", "aliases", "ta", "save"])
        with patch("swe.harness.commands.load_registry", return_value=tools), patch(
            "swe.harness.commands.save_registry"
        ) as save, patch(
            "swe.harness.commands.resolve", return_value="tau"
        ), patch("swe.harness.commands.read_line", side_effect=lambda *_a, **_k: next(inputs)):
            rc = cmd_edit(["tau"])
            self.assertEqual(rc, 0)
            save.assert_called_once()
            saved_tools = save.call_args[0][0]
            self.assertEqual(saved_tools["tau"]["aliases"], ["ta"])

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
        with patch("swe.harness.commands.load_registry", return_value=tools), patch(
            "swe.harness.commands.save_registry"
        ) as save, patch(
            "swe.harness.commands.resolve", return_value="tau"
        ), patch("swe.harness.commands.read_line", side_effect=lambda *_a, **_k: next(inputs)):
            rc = cmd_edit(["tau"])
            self.assertEqual(rc, 0)
            save.assert_called_once()
            saved_tools = save.call_args[0][0]
            self.assertEqual(saved_tools["tau"]["description"], "Tau — coding agent")

    def test_cmd_edit_interactive_sets_display_name_under_any_spelling(self):
        for typed in ("display name", "display-name", "display_name", "Display Name"):
            tools = {"tau": {"command": "tau", "aliases": [], "tags": []}}
            inputs = iter([typed, "Tau Code", "save"])
            with patch("swe.harness.commands.load_registry", return_value=tools), patch(
                "swe.harness.commands.save_registry"
            ) as save, patch(
                "swe.harness.commands.resolve", return_value="tau"
            ), patch("swe.harness.commands.read_line",
                     side_effect=lambda *_a, **_k: next(inputs)):
                rc = cmd_edit(["tau"])
            self.assertEqual(rc, 0, typed)
            self.assertEqual(save.call_args[0][0]["tau"]["display_name"], "Tau Code", typed)

    def test_edit_field_listing_shows_the_display_name(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _show_edit_fields("tau", {"command": "tau", "display_name": "Tau Code"})
        self.assertRegex(buf.getvalue(), r"display name:\s+Tau Code")
        buf = io.StringIO()
        with redirect_stdout(buf):
            _show_edit_fields("tau", {"command": "tau"})
        self.assertRegex(buf.getvalue(), r"display name:\s+—")


if __name__ == "__main__":
    unittest.main()
