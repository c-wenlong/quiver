import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.configuration import CorruptConfigurationError
from swe.harness.catalog import HARNESS_CATALOG
from swe.harness.registry import (
    alias_map,
    load_registry,
    load_registry_readonly,
    resolve,
    save_registry,
    starred_names,
)


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

    def test_load_registry_reads_empty_without_writing(self):
        """A missing registry reads as empty and stays missing.

        A read used to write {} to disk just so a file existed: every
        invocation then created ~/.quiver, including a failing unknown
        command that fell through to the full help — anything wanting to
        place content at that path had to land before the first swe run.
        Only a writer creates the file now, via save_registry.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            with patch("swe.harness.registry.CONFIG_DIR", config_dir), patch(
                "swe.harness.registry.HARNESS_FILE", registry_file
            ), patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"):
                tools = load_registry()
                self.assertEqual(tools, {})
                self.assertFalse(registry_file.exists())
                self.assertFalse(config_dir.exists())

    def test_save_registry_is_the_scaffolder(self):
        """load's counterpart: the write path still makes the directory."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            with patch("swe.harness.registry.CONFIG_DIR", config_dir), patch(
                "swe.harness.registry.HARNESS_FILE", registry_file
            ), patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"):
                save_registry({"claude": {"command": "claude"}})
                self.assertTrue(registry_file.is_file())
                self.assertEqual(load_registry(), {"claude": {"command": "claude"}})

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
            with patch("swe.harness.registry.CONFIG_DIR", config_dir), patch(
                "swe.harness.registry.HARNESS_FILE", registry_file
            ), patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"):
                save_registry(custom)
                loaded = load_registry()
                self.assertIn("mytool", loaded)
                self.assertEqual(resolve(loaded, "mt"), "mytool")


class RegistryRobustnessTest(unittest.TestCase):
    """#383: malformed entries error cleanly; state-only rows stay readable."""

    def _config_dir(self, tmp):
        config_dir = Path(tmp) / ".quiver" / "config"
        config_dir.mkdir(parents=True)
        return config_dir

    def _patches(self, config_dir):
        return (
            patch("swe.harness.registry.CONFIG_DIR", config_dir),
            patch("swe.harness.registry.HARNESS_FILE", config_dir / "harness.json"),
            patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
            patch("swe.harness.registry.STARS_FILE", config_dir / "stars.json"),
            patch("swe.harness.registry.ARCHIVE_FILE", config_dir / "archived.json"),
        )

    def test_non_object_entry_raises_controlled_error(self):
        """`{"claude": "oops"}` used to reach callers that then died on
        ``entry.get(...)`` with a bare AttributeError."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self._config_dir(tmp)
            (config_dir / "harness.json").write_text(
                json.dumps({"claude": "oops", "codex": {"command": "codex"}}))
            patches = self._patches(config_dir)
            for p in patches:
                p.start()
            try:
                with self.assertRaises(CorruptConfigurationError) as ctx:
                    load_registry()
            finally:
                for p in patches:
                    p.stop()
            self.assertIn("claude", str(ctx.exception))

    def test_readonly_path_validates_legacy_tools_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self._config_dir(tmp)
            (config_dir / "tools.json").write_text(json.dumps({"claude": 42}))
            patches = self._patches(config_dir)
            for p in patches:
                p.start()
            try:
                with self.assertRaises(CorruptConfigurationError):
                    load_registry_readonly()
            finally:
                for p in patches:
                    p.stop()

    def test_alias_map_tolerates_hand_edited_aliases(self):
        """A nested list here is unhashable, so every name lookup in every
        command raised TypeError; a bare string read as one alias per
        letter."""
        reg = {
            "claude": {"aliases": "cc"},
            "codex": {"aliases": [["cx"], {"a": 1}, "", "co"]},
            "gemini": {"aliases": 7},
        }
        self.assertEqual(alias_map(reg), {"claude": "claude", "cc": "claude",
                                          "codex": "codex", "co": "codex",
                                          "gemini": "gemini"})

    def test_starred_names_tolerates_a_non_numeric_pin(self):
        reg = {
            "alpha": {"command": "a", "state": "starred", "pin": "oops"},
            "beta": {"command": "b", "state": "starred", "pin": 2},
        }
        # Used to TypeError sorting str against int; a bad pin sorts as 0.
        self.assertEqual(starred_names(reg), ["alpha", "beta"])

    def test_migration_keeps_a_star_for_a_name_tools_does_not_know(self):
        """State-only rows are a feature of the stars shim (star before the
        harness is registered); migration must carry them, and the readers
        must tolerate the missing command."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = self._config_dir(tmp)
            (config_dir / "tools.json").write_text(
                json.dumps({"claude": {"command": "claude"}}))
            (config_dir / "stars.json").write_text(
                json.dumps(["claude", "ghost-tool"]))
            patches = self._patches(config_dir)
            for p in patches:
                p.start()
            try:
                reg = load_registry()
            finally:
                for p in patches:
                    p.stop()
            self.assertEqual(reg["claude"]["state"], "starred")
            self.assertEqual(reg["ghost-tool"]["state"], "starred")
            self.assertEqual(reg["ghost-tool"]["pin"], 2)
            self.assertEqual(starred_names(reg), ["claude", "ghost-tool"])
            # Readers that keyed on entry["command"] must not crash on the ghost.
            self.assertEqual(reg["ghost-tool"].get("command"), None)

    def _corrupt_legacy_case(self, filename: str):
        """tools.json valid + one corrupt legacy file: the migration must
        refuse rather than commit a harness.json missing that state."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config_dir = self._config_dir(tmp.name)
        (config_dir / "tools.json").write_text(
            json.dumps({"claude": {"command": "claude"}}))
        (config_dir / filename).write_text("{corrupt")
        patches = self._patches(config_dir)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return config_dir

    def test_migration_refuses_a_corrupt_stars_file(self):
        config_dir = self._corrupt_legacy_case("stars.json")
        with self.assertRaises(CorruptConfigurationError) as ctx:
            load_registry()
        self.assertIn("stars.json", str(ctx.exception))
        # Nothing committed: harness.json must not exist yet.
        self.assertFalse((config_dir / "harness.json").exists())

    def test_migration_refuses_a_corrupt_archive_file(self):
        config_dir = self._corrupt_legacy_case("archived.json")
        with self.assertRaises(CorruptConfigurationError) as ctx:
            load_registry()
        self.assertIn("archived.json", str(ctx.exception))
        self.assertFalse((config_dir / "harness.json").exists())

    def test_migration_runs_once_the_corrupt_file_is_removed(self):
        config_dir = self._corrupt_legacy_case("stars.json")
        (config_dir / "stars.json").unlink()
        reg = load_registry()
        self.assertEqual(reg["claude"]["command"], "claude")
        self.assertTrue((config_dir / "harness.json").exists())


if __name__ == "__main__":
    unittest.main()
