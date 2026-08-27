"""Harness state consolidation: one file, one accessor.

tools.json (what a harness is) + stars.json (which ones you favourite) +
archived.json (which ones you shelved) became config/harness.json, with a
`state` field on each row instead of membership in a second or third file.
These tests pin the shape of that migration and the invariants the new
schema has to hold: an absent state reads as active, pin order round-trips
through `load_stars`, archiving and restoring round-trip through
`load_archive`, and a harness.json that already exists is never re-migrated
even if the legacy files are still sitting next to it.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.harness import archive as archive_mod
from quiver.harness import registry
from quiver.harness import stars as stars_mod


class RegistryStateTest(unittest.TestCase):
    """Sandbox every test against a throwaway ~/.quiver/config, never the
    real one, by patching the paths registry.py actually reads."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config_dir = self.home / ".quiver" / "config"
        self.harness_file = self.config_dir / "harness.json"
        self.tools_file = self.config_dir / "tools.json"
        self.stars_file = self.config_dir / "stars.json"
        self.archive_file = self.config_dir / "archived.json"

        patches = [
            mock.patch.object(registry, "CONFIG_DIR", self.config_dir),
            mock.patch.object(registry, "HARNESS_FILE", self.harness_file),
            mock.patch.object(registry, "TOOLS_FILE", self.tools_file),
            mock.patch.object(registry, "STARS_FILE", self.stars_file),
            mock.patch.object(registry, "ARCHIVE_FILE", self.archive_file),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_legacy(self, tools=None, stars=None, archived=None):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.tools_file.write_text(json.dumps(tools if tools is not None else {}))
        if stars is not None:
            self.stars_file.write_text(json.dumps(stars))
        if archived is not None:
            self.archive_file.write_text(json.dumps(archived))

    # -- absent state -------------------------------------------------

    def test_absent_state_reads_as_active(self):
        self.assertEqual(registry.state_of({}), "active")
        self.assertEqual(registry.state_of({"command": "x"}), "active")

    def test_absent_state_is_active_not_archived(self):
        entry = {"command": "x"}
        self.assertTrue(registry.is_active(entry))
        self.assertNotIn("x", registry.archived_names({"x": entry}))

    def test_starred_counts_as_active(self):
        reg = {"x": {"state": "starred", "pin": 1}}
        self.assertTrue(registry.is_active(reg["x"]))
        self.assertIn("x", registry.active_names(reg))

    # -- lazy migration -------------------------------------------------

    def test_migration_only_fires_when_harness_json_is_absent(self):
        self._write_legacy(tools={"claude": {"command": "claude"}})
        self.assertFalse(self.harness_file.exists())
        registry.load_registry()
        self.assertTrue(self.harness_file.exists())

    def test_migration_needs_no_stars_or_archive_file(self):
        """tools.json alone is enough; stars/archived are optional extras."""
        self._write_legacy(tools={"claude": {"command": "claude"}})
        reg = registry.load_registry()
        self.assertEqual(reg["claude"]["command"], "claude")
        self.assertEqual(registry.state_of(reg["claude"]), "active")

    def test_migration_merges_stars_with_pin_order(self):
        self._write_legacy(
            tools={"claude": {"command": "claude"}, "droid": {"command": "droid"}},
            stars=["droid", "claude"],
        )
        reg = registry.load_registry()
        self.assertEqual(registry.state_of(reg["droid"]), "starred")
        self.assertEqual(reg["droid"]["pin"], 1)
        self.assertEqual(reg["claude"]["pin"], 2)
        self.assertEqual(registry.starred_names(reg), ["droid", "claude"])

    def test_migration_merges_archived_entries(self):
        self._write_legacy(
            tools={"kiro": {"command": "kiro"}},
            archived={"kiro": {"reason": "thin wrapper",
                               "archived_at": "2026-08-21T10:00:00",
                               "usage": "trial"}},
        )
        reg = registry.load_registry()
        self.assertEqual(registry.state_of(reg["kiro"]), "archived")
        self.assertEqual(reg["kiro"]["archived"]["reason"], "thin wrapper")
        self.assertEqual(reg["kiro"]["archived"]["usage"], "trial")
        self.assertNotIn("pin", reg["kiro"])

    def test_migration_carries_over_the_catalog_fields_untouched(self):
        self._write_legacy(tools={"claude": {"command": "claude", "version": "1.2.3",
                                             "aliases": ["cc"], "tags": ["agentic"]}})
        reg = registry.load_registry()
        self.assertEqual(reg["claude"]["version"], "1.2.3")
        self.assertEqual(reg["claude"]["aliases"], ["cc"])
        self.assertEqual(reg["claude"]["tags"], ["agentic"])

    def test_migration_moves_legacy_files_to_a_dated_backup_dir(self):
        self._write_legacy(tools={"claude": {"command": "claude"}}, stars=["claude"],
                           archived={})
        registry.load_registry()
        self.assertFalse(self.tools_file.exists())
        self.assertFalse(self.stars_file.exists())
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%d")
        backup_dir = self.home / ".quiver" / ".backup" / f"registry-migration-{stamp}"
        self.assertTrue((backup_dir / "tools.json").is_file())
        self.assertTrue((backup_dir / "stars.json").is_file())

    def test_migration_never_deletes_data_only_moves_it(self):
        self._write_legacy(tools={"claude": {"command": "claude", "note": "keep me"}})
        registry.load_registry()
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%d")
        backup_dir = self.home / ".quiver" / ".backup" / f"registry-migration-{stamp}"
        moved = json.loads((backup_dir / "tools.json").read_text())
        self.assertEqual(moved["claude"]["note"], "keep me")

    def test_an_existing_harness_json_is_never_re_migrated(self):
        """A harness.json on disk is authoritative, even with legacy files
        still sitting next to it — the data plane may have hand-migrated,
        and re-merging on top of that would clobber it."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.harness_file.write_text(json.dumps({"already": {"command": "x",
                                                              "state": "starred",
                                                              "pin": 1}}))
        # Legacy files present too, with conflicting data.
        self._write_legacy(tools={"claude": {"command": "claude"}}, stars=["claude"])

        reg = registry.load_registry()
        self.assertEqual(set(reg), {"already"})
        self.assertIn("claude", "".join(json.loads(self.tools_file.read_text())),
                      "legacy tools.json should be untouched, still holding claude")
        self.assertTrue(self.tools_file.exists(), "legacy file must be left alone, not moved")

    def test_no_files_at_all_writes_an_empty_registry(self):
        """Nothing on disk means nothing registered, not a stock lineup.

        The file is still created, so later writes have somewhere to land
        and `load_registry_if_present` can tell "never initialised" from
        "initialised and empty".
        """
        reg = registry.load_registry()
        self.assertEqual(reg, {})
        self.assertTrue(self.harness_file.exists())

    # -- save/load stability -------------------------------------------------

    def test_save_then_load_round_trips(self):
        reg = {"claude": {"command": "claude", "state": "starred", "pin": 1}}
        registry.save_registry(reg)
        self.assertEqual(registry.load_registry(), reg)

    def test_saved_file_is_the_new_harness_json_not_tools_json(self):
        registry.save_registry({"claude": {"command": "claude"}})
        self.assertTrue(self.harness_file.exists())
        self.assertFalse(self.tools_file.exists())


class StarsWrapperTest(unittest.TestCase):
    """`quiver.harness.stars` as a compatibility shim over the registry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_dir = Path(self.tmp.name) / ".quiver" / "config"
        harness_file = config_dir / "harness.json"
        patches = [
            mock.patch.object(registry, "CONFIG_DIR", config_dir),
            mock.patch.object(registry, "HARNESS_FILE", harness_file),
            mock.patch.object(registry, "TOOLS_FILE", config_dir / "tools.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        registry.save_registry({})  # start from an empty, harness.json-backed registry

    def test_starred_order_round_trips_through_load_stars(self):
        stars_mod.save_stars(["droid", "claude", "codex"])
        self.assertEqual(stars_mod.load_stars(), ["droid", "claude", "codex"])
        reg = registry.load_registry()
        self.assertEqual(reg["droid"]["pin"], 1)
        self.assertEqual(reg["claude"]["pin"], 2)
        self.assertEqual(reg["codex"]["pin"], 3)

    def test_star_toggle_round_trips(self):
        self.assertTrue(stars_mod.star("droid"))
        self.assertEqual(stars_mod.load_stars(), ["droid"])
        self.assertFalse(stars_mod.toggle_star("droid"))
        self.assertEqual(stars_mod.load_stars(), [])

    def test_unstarring_drops_a_ghost_row_entirely(self):
        """A star can outlive its catalog entry; once removed it should
        leave no trace rather than an empty {} row."""
        stars_mod.star("ghost-tool")
        stars_mod.unstar("ghost-tool")
        self.assertNotIn("ghost-tool", registry.load_registry())

    def test_unstarring_keeps_the_row_if_it_has_other_data(self):
        registry.save_registry({"claude": {"command": "claude", "state": "starred", "pin": 1}})
        stars_mod.unstar("claude")
        reg = registry.load_registry()
        self.assertIn("claude", reg)
        self.assertEqual(registry.state_of(reg["claude"]), "active")
        self.assertEqual(reg["claude"]["command"], "claude")


class ArchiveWrapperTest(unittest.TestCase):
    """`quiver.harness.archive` as a compatibility shim over the registry."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config_dir = Path(self.tmp.name) / ".quiver" / "config"
        harness_file = config_dir / "harness.json"
        patches = [
            mock.patch.object(registry, "CONFIG_DIR", config_dir),
            mock.patch.object(registry, "HARNESS_FILE", harness_file),
            mock.patch.object(registry, "TOOLS_FILE", config_dir / "tools.json"),
            mock.patch.object(archive_mod, "_derive_usage", lambda name: "trial"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        registry.save_registry({})

    def test_archive_then_unarchive_round_trips(self):
        entry = archive_mod.archive("kiro", "thin wrapper")
        self.assertEqual(entry["reason"], "thin wrapper")
        self.assertTrue(archive_mod.is_archived("kiro"))

        old = archive_mod.unarchive("kiro")
        self.assertEqual(old["reason"], "thin wrapper")
        self.assertFalse(archive_mod.is_archived("kiro"))

    def test_archived_entries_show_up_in_load_archive(self):
        archive_mod.archive("kiro", "no MCP support")
        entries = archive_mod.load_archive()
        self.assertEqual(entries["kiro"]["reason"], "no MCP support")

    def test_unarchiving_drops_a_ghost_row_entirely(self):
        archive_mod.archive("kiro", "why")
        archive_mod.unarchive("kiro")
        self.assertNotIn("kiro", registry.load_registry())

    def test_unarchiving_keeps_the_row_if_it_has_other_data(self):
        registry.save_registry({"kiro": {"command": "kiro", "state": "archived",
                                         "archived": {"reason": "x", "archived_at": "",
                                                      "usage": "trial"}}})
        archive_mod.unarchive("kiro")
        reg = registry.load_registry()
        self.assertIn("kiro", reg)
        self.assertEqual(registry.state_of(reg["kiro"]), "active")
        self.assertEqual(reg["kiro"]["command"], "kiro")

    def test_archiving_a_starred_harness_drops_the_pin(self):
        registry.save_registry({"kiro": {"command": "kiro", "state": "starred", "pin": 1}})
        archive_mod.archive("kiro", "changed my mind")
        reg = registry.load_registry()
        self.assertEqual(registry.state_of(reg["kiro"]), "archived")
        self.assertNotIn("pin", reg["kiro"])


if __name__ == "__main__":
    unittest.main()
