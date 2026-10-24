import tempfile
import unittest
from pathlib import Path

from quiver.skills.symlinks import (
    SkillsSymlinkHint,
    apply_skills_symlink_hints,
    skills_symlink_hints,
)


class SkillsSymlinksTest(unittest.TestCase):
    def test_suggests_symlink_when_cursor_skills_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".quiver" / "skills"
            shared.mkdir(parents=True)
            (home / ".cursor").mkdir()
            (shared / "demo-skill").mkdir()
            (shared / "demo-skill" / "SKILL.md").write_text("---\nname: demo\n---\n")

            hints = skills_symlink_hints(home=home)
            cursor = [h for h in hints if h.label == "cursor"]
            self.assertEqual(len(cursor), 1)
            self.assertEqual(cursor[0].action, "symlink")
            self.assertIn("ln -sf", cursor[0].command)

    def test_apply_creates_symlink_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".quiver" / "skills"
            shared.mkdir(parents=True)
            (home / ".cursor").mkdir()

            hints = skills_symlink_hints(home=home)
            applied = apply_skills_symlink_hints(hints, home=home)
            self.assertTrue(any("cursor" in item for item in applied))
            cursor_link = home / ".cursor" / "skills"
            self.assertTrue(cursor_link.is_symlink())
            self.assertEqual(cursor_link.resolve(), shared.resolve())

    def test_a_harness_without_its_home_folder_gets_no_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".quiver" / "skills").mkdir(parents=True)
            (home / ".claude").mkdir()
            (home / ".config").mkdir()

            hints = skills_symlink_hints(home=home)
            self.assertEqual([h.label for h in hints], ["claude"])
            applied = apply_skills_symlink_hints(hints, home=home)
            self.assertEqual(applied, ["symlink claude"])
            self.assertEqual(sorted(p.name for p in home.iterdir()),
                             [".claude", ".config", ".quiver"])
            self.assertEqual(list((home / ".config").iterdir()), [])

    def test_apply_never_creates_a_harness_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".quiver" / "skills").mkdir(parents=True)
            stale = SkillsSymlinkHint(label="augment", path=home / ".augment" / "skills",
                                      action="symlink", command="", reason="")

            self.assertEqual(apply_skills_symlink_hints([stale], home=home), [])
            self.assertFalse((home / ".augment").exists())


if __name__ == "__main__":
    unittest.main()
