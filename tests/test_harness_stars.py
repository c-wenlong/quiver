import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.commands import _sort_tools
from quiver.harness.stars import (
    is_starred,
    load_stars,
    save_stars,
    star,
    toggle_star,
    unstar,
)


class HarnessStarsTest(unittest.TestCase):
    def _patch_paths(self, tmp: str):
        config_dir = Path(tmp) / ".config" / "swe"
        stars_file = config_dir / "stars.json"
        return patch.multiple(
            "quiver.harness.stars",
            CONFIG_DIR=config_dir,
            STARS_FILE=stars_file,
        )

    def test_star_toggle_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_paths(tmp):
                self.assertEqual(load_stars(), [])
                self.assertTrue(star("droid"))
                self.assertEqual(load_stars(), ["droid"])
                self.assertFalse(star("droid"))  # already starred → re-pin only
                self.assertEqual(load_stars(), ["droid"])
                self.assertTrue(star("claude"))
                self.assertEqual(load_stars(), ["claude", "droid"])
                self.assertTrue(unstar("claude"))
                self.assertEqual(load_stars(), ["droid"])
                self.assertFalse(toggle_star("droid"))
                self.assertEqual(load_stars(), [])
                self.assertTrue(toggle_star("droid"))
                self.assertTrue(is_starred("droid"))

    def test_save_stars_dedupes(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._patch_paths(tmp):
                save_stars(["droid", "claude", "droid", ""])
                self.assertEqual(load_stars(), ["droid", "claude"])
                raw = json.loads((Path(tmp) / ".config" / "swe" / "stars.json").read_text())
                self.assertEqual(raw, ["droid", "claude"])

    def test_sort_tools_pins_stars_first(self):
        tools = {
            "zzz": {"command": "zzz"},
            "droid": {"command": "droid"},
            "claude": {"command": "claude"},
            "aaa": {"command": "aaa"},
        }
        counts = {"zzz": 99, "aaa": 50, "claude": 10, "droid": 1}
        stars = ["droid", "claude"]
        ordered = [name for name, _ in _sort_tools(tools, counts, stars)]
        self.assertEqual(ordered[:2], ["droid", "claude"])
        # remaining sorted by usage desc
        self.assertEqual(ordered[2:], ["zzz", "aaa"])


if __name__ == "__main__":
    unittest.main()
