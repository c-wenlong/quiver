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
    """Star state now lives on each harness's row in harness.json (see
    quiver.harness.registry); patch that file rather than a standalone
    stars.json, since quiver.harness.stars is a thin shim over it."""

    def _patch_paths(self, tmp: str):
        config_dir = Path(tmp) / ".quiver" / "config"
        return patch.multiple(
            "quiver.harness.registry",
            CONFIG_DIR=config_dir,
            HARNESS_FILE=config_dir / "harness.json",
            TOOLS_FILE=config_dir / "tools.json",
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
                raw = json.loads(
                    (Path(tmp) / ".quiver" / "config" / "harness.json").read_text())
                self.assertEqual(raw["droid"]["pin"], 1)
                self.assertEqual(raw["claude"]["pin"], 2)
                self.assertEqual(raw["droid"]["state"], "starred")

    def test_sort_tools_puts_starred_block_first_each_by_usage(self):
        tools = {
            "zzz": {"command": "zzz"},
            "droid": {"command": "droid"},
            "claude": {"command": "claude"},
            "aaa": {"command": "aaa"},
        }
        counts = {"zzz": 99, "aaa": 50, "claude": 10, "droid": 1}
        stars = ["droid", "claude"]
        ordered = [name for name, _ in _sort_tools(tools, counts, stars)]
        # Starred block first, but ordered by usage within it, not pin order.
        self.assertEqual(ordered[:2], ["claude", "droid"])
        # remaining sorted by usage desc
        self.assertEqual(ordered[2:], ["zzz", "aaa"])


if __name__ == "__main__":
    unittest.main()
