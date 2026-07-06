import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.defaults import DEFAULT_TOOLS
from quiver.harness.registry import alias_map, load_registry, resolve, save_registry


class HarnessRegistryTest(unittest.TestCase):
    def test_alias_map_includes_canonical_and_aliases(self):
        tools = {
            "claude": {"command": "claude", "aliases": ["cc"]},
            "codex": {"command": "codex", "aliases": ["cx"]},
        }
        mapping = alias_map(tools)
        self.assertEqual(mapping["claude"], "claude")
        self.assertEqual(mapping["cc"], "claude")
        self.assertEqual(mapping["cx"], "codex")

    def test_resolve_unknown_returns_none(self):
        tools = {"claude": {"aliases": ["cc"]}}
        self.assertIsNone(resolve(tools, "missing"))

    def test_load_registry_creates_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            registry_file = config_dir / "tools.json"
            with patch("quiver.harness.registry.CONFIG_DIR", config_dir), patch(
                "quiver.harness.registry.REGISTRY_FILE", registry_file
            ):
                tools = load_registry()
                self.assertIn("claude", tools)
                self.assertTrue(registry_file.exists())
                self.assertEqual(json.loads(registry_file.read_text())["claude"], tools["claude"])

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            registry_file = config_dir / "tools.json"
            custom = dict(DEFAULT_TOOLS)
            custom["mytool"] = {
                "command": "mytool",
                "description": "test",
                "version": None,
                "tags": ["agentic"],
                "aliases": ["mt"],
            }
            with patch("quiver.harness.registry.CONFIG_DIR", config_dir), patch(
                "quiver.harness.registry.REGISTRY_FILE", registry_file
            ):
                save_registry(custom)
                loaded = load_registry()
                self.assertIn("mytool", loaded)
                self.assertEqual(resolve(loaded, "mt"), "mytool")


if __name__ == "__main__":
    unittest.main()
