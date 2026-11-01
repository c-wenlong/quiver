"""`swe setup --apply` run twice must not invent harnesses.

The first apply made ~/.quiver/skills and stopped there; the second saw it
and linked skills into every root in HARNESS_ROOTS, making ~/.augment,
~/.kiro, ~/.pi and the rest to hold the links. The linking pass in the same
run then read those fresh dirs as install evidence and registered and
linked every one of them.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

# Discovery also searches /opt/homebrew/bin and /usr/local/bin whatever PATH
# says, so a developer's own installs would register here. Empty that list
# so PATH, which holds only the fake claude, is the whole machine. PATH
# leaves out /usr/bin too: a Linux box can have a harness installed there.
BOOT = (
    "import sys; import swe.harness.discover as d; d.EXTRA_BIN_DIRS = []; "
    "from swe.cli import main; sys.argv = ['swe'] + sys.argv[1:]; "
    "sys.exit(main())"
)


class SetupApplyTwiceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root / "home"
        (self.home / ".claude").mkdir(parents=True)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        claude = bin_dir / "claude"
        claude.write_text("#!/bin/sh\necho '1.0.0 (Claude Code)'\n")
        claude.chmod(0o755)
        self.env = {
            "HOME": str(self.home),
            "PATH": str(bin_dir),
            "PYTHONPATH": str(SRC),
            "NO_COLOR": "1",
        }

    def _apply(self) -> dict:
        proc = subprocess.run(
            [sys.executable, "-c", BOOT, "setup", "--apply", "--json"],
            capture_output=True, text=True, env=self.env, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_second_apply_leaves_absent_harnesses_alone(self):
        self._apply()
        second = self._apply()

        self.assertEqual(
            sorted(p.name for p in self.home.iterdir()), [".claude", ".quiver"])
        self.assertTrue((self.home / ".claude" / "skills").is_symlink())
        self.assertTrue((self.home / ".claude" / "CLAUDE.md").is_symlink())
        registry = json.loads(
            (self.home / ".quiver" / "config" / "harness.json").read_text())
        self.assertEqual(sorted(registry), ["claude"])
        applied = second["data"]["applied"]
        self.assertEqual(applied["skills"], [])
        linked = {
            row["harness"] for row in applied["links"]["rows"]
            if row["result"] == "linked"
        }
        self.assertEqual(linked, {"claude"})


if __name__ == "__main__":
    unittest.main()
