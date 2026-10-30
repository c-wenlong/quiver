"""Launch-model capabilities and the recently-used model lists."""

import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.catalog import HARNESS_CATALOG
from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.ledger.store import LedgerStore, ledger_path_for
from quiver.remote import catalog as catalog_mod
from quiver.remote import launch_model, recent_models

from tests._tempdir import temp_dir


def _rec(key, harness, model, ts):
    return LedgerRecord(
        key=key,
        event=UsageEvent(harness=harness, account="", session="s", cwd="",
                         model=model, ts=ts, input=1, output=1))


class TestLaunchModel(unittest.TestCase):
    def test_fallback_names_known_harnesses(self):
        # Keyed by registry names, like HARNESS_ROOTS and PLUGIN_FALLBACK.
        for name in launch_model.MODEL_FALLBACK:
            self.assertIn(name, HARNESS_CATALOG)
            self.assertIsNotNone(launch_model.capability(name, {}))

    def test_registry_overrides_and_switches_off(self):
        reg = {"cursor": {"capabilities": {"model": {"flag": "-M"}}},
               "devin": {"capabilities": {"model": {"supported": False}}},
               "gemini": {"capabilities": {"model": {"flag": "nope"}}}}
        self.assertEqual(
            launch_model.model_argv("cursor", "x", settings_dir=Path("/nx"),
                                    stem="s", reg=reg),
            ["-M", "x"])
        self.assertIsNone(launch_model.capability("devin", reg))
        # A flag that isn't one declares nothing usable.
        self.assertIsNone(launch_model.capability("gemini", reg))
        with self.assertRaises(ValueError):
            launch_model.model_argv("devin", "x", settings_dir=Path("/nx"),
                                    stem="s", reg=reg)

    def test_malformed_capabilities_are_ignored(self):
        for caps in ("oops", ["model"], {"model": "--model"}):
            reg = {"claude": {"capabilities": caps}}
            # Falls back to the table instead of raising.
            self.assertEqual(launch_model.capability("claude", reg),
                             {"flag": "--model"})

    def test_settings_file_nests_the_key_and_prunes_old_ones(self):
        folder = temp_dir(self)
        stale = folder / "old.json"
        stale.write_text("{}")
        import os
        os.utime(stale, (0, 0))
        argv = launch_model.model_argv("droid", "glm-5", settings_dir=folder,
                                       stem="qv-x", reg={})
        self.assertEqual(argv, ["--settings", str(folder / "qv-x.json")])
        self.assertEqual(json.loads((folder / "qv-x.json").read_text()),
                         {"sessionDefaultSettings": {"model": "glm-5"}})
        self.assertFalse(stale.exists())

    def test_launchable_reports_launch_model_from_the_capability(self):
        reg = {"claude": {"command": "claude"},
               "gemini": {"command": "gemini"},
               "devin": {"command": "devin",
                         "capabilities": {"model": {"supported": False}}}}
        with patch("quiver.harness.registry.load_registry_readonly",
                   return_value=reg), \
             patch.object(catalog_mod, "_provider_visibility",
                          return_value=([], set())), \
             patch.object(catalog_mod.shutil, "which",
                          return_value="/x/bin"):
            items = {i["name"]: i for i in catalog_mod.launchable_harnesses()}
        self.assertTrue(items["claude"]["launch_model"])
        self.assertFalse(items["gemini"]["launch_model"])
        self.assertFalse(items["devin"]["launch_model"])


class TestRecentModels(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.store = LedgerStore(ledger_path_for(self.home))

    def tearDown(self):
        self.store.close()

    def _add(self, *recs):
        for rec in recs:
            self.store.upsert(rec)
        self.store.db.commit()

    def test_newest_first_capped_and_deduped(self):
        self._add(*[_rec(f"c{i}", "claude", f"m{i}", i) for i in range(8)],
                  _rec("again", "claude", "m1", 100))
        got = recent_models.recent_models(self.home)
        self.assertEqual(got["claude"], ["m1", "m7", "m6", "m5", "m4"])

    def test_junk_is_dropped(self):
        self._add(
            _rec("d1", "devin", "swe-2-max", 1),
            _rec("d2", "devin", "compactor", 2),
            _rec("d3", "devin", "summarizer", 3),
            _rec("u1", "cursor", "default", 4),
            _rec("u2", "cursor", "Auto (gpt-5)", 5),
            _rec("u3", "cursor", "gpt-5", 1),
            _rec("k1", "claude", "<synthetic>", 9),
            _rec("k2", "claude", "unknown", 8),
        )
        got = recent_models.recent_models(self.home)
        self.assertEqual(got["devin"], ["swe-2-max"])
        self.assertEqual(got["cursor"], ["gpt-5"])
        self.assertNotIn("claude", got)

    def test_harness_filter_and_last_models(self):
        self._add(_rec("a", "claude", "sonnet", 1),
                  _rec("b", "codex", "gpt-5", 2))
        self.assertEqual(
            recent_models.recent_models(self.home, ["codex"]),
            {"codex": ["gpt-5"]})
        self.assertEqual(catalog_mod.last_used_models(self.home),
                         {"claude": "sonnet", "codex": "gpt-5"})

    def test_new_rows_are_folded_in(self):
        self._add(_rec("a", "claude", "sonnet", 1))
        self.assertEqual(recent_models.recent_models(self.home),
                         {"claude": ["sonnet"]})
        self._add(_rec("b", "claude", "opus", 2))
        self.assertEqual(recent_models.recent_models(self.home),
                         {"claude": ["opus", "sonnet"]})

    def test_an_unchanged_ledger_is_not_reread(self):
        self._add(_rec("a", "claude", "sonnet", 1))
        recent_models.recent_models(self.home)
        with patch.object(recent_models, "_open_ro") as opened:
            recent_models.recent_models(self.home)
        opened.assert_not_called()

    def test_opencode_bare_names_take_the_provider_from_opencode_db(self):
        db_path = self.home / ".local/share/opencode/opencode.db"
        db_path.parent.mkdir(parents=True)
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE session_v2 (id TEXT, model TEXT,"
                   " time_updated INTEGER)")
        db.execute("INSERT INTO session_v2 VALUES ('s1', ?, 1)",
                   (json.dumps({"id": "MiniMax-M3", "providerID": "minimax",
                                "variant": "thinking"}),))
        db.execute("INSERT INTO session_v2 VALUES ('s2', NULL, 2)")
        db.execute("INSERT INTO session_v2 VALUES ('s3', ?, 3)",
                   (json.dumps({"id": "nvidia/nemotron:free",
                                "providerID": "openrouter"}),))
        db.commit()
        db.close()
        self._add(_rec("o1", "opencode", "MiniMax-M3", 3),
                  _rec("o2", "opencode", "anthropic/claude-opus-5", 2),
                  _rec("o3", "opencode", "minimax/MiniMax-M3", 1),
                  _rec("o4", "opencode", "no-provider-anywhere", 4),
                  # A bare id with a slash of its own still takes one.
                  _rec("o5", "opencode", "nvidia/nemotron:free", 0))
        self.assertEqual(recent_models.recent_models(self.home)["opencode"],
                         ["minimax/MiniMax-M3", "anthropic/claude-opus-5",
                          "openrouter/nvidia/nemotron:free"])

    def test_an_in_place_relabel_shows_after_the_full_reread(self):
        self._add(_rec("a", "claude", "sonnet", 1))
        recent_models.recent_models(self.home)
        # A relabel keeps the rowid, so only the periodic re-read sees
        # it, even when the ledger then stays unchanged.
        self.store.db.execute("UPDATE events SET model = 'opus'")
        self.store.db.commit()
        recent_models.recent_models(self.home)
        key = str(ledger_path_for(self.home))
        recent_models._state[key]["full_at"] -= recent_models._FULL_EVERY_S
        self.assertEqual(recent_models.recent_models(self.home),
                         {"claude": ["opus"]})

    def test_no_ledger_is_no_history(self):
        self.assertEqual(
            recent_models.recent_models(temp_dir(self)), {})


if __name__ == "__main__":
    unittest.main()
