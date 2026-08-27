import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.catalog import HARNESS_CATALOG
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

    def test_load_registry_creates_empty_file_when_missing(self):
        """A new registry starts empty; only discovery may put rows in it.

        Seeding from the catalogue would list harnesses that are not
        installed on this machine, each with a description, and once with a
        version constant that no command had probed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            with patch("quiver.harness.registry.CONFIG_DIR", config_dir), patch(
                "quiver.harness.registry.HARNESS_FILE", registry_file
            ), patch("quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"):
                tools = load_registry()
                self.assertEqual(tools, {})
                self.assertTrue(registry_file.exists())
                self.assertEqual(json.loads(registry_file.read_text()), {})

    def test_catalog_carries_no_version(self):
        """Versions come from probing a binary, never from a constant."""
        for name, meta in HARNESS_CATALOG.items():
            self.assertNotIn("version", meta, f"{name} pins a version")

    def test_save_and_reload_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            custom = dict(HARNESS_CATALOG)
            custom["mytool"] = {
                "command": "mytool",
                "description": "test",
                "version": None,
                "tags": ["agentic"],
                "aliases": ["mt"],
            }
            with patch("quiver.harness.registry.CONFIG_DIR", config_dir), patch(
                "quiver.harness.registry.HARNESS_FILE", registry_file
            ), patch("quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"):
                save_registry(custom)
                loaded = load_registry()
                self.assertIn("mytool", loaded)
                self.assertEqual(resolve(loaded, "mt"), "mytool")


if __name__ == "__main__":
    unittest.main()
