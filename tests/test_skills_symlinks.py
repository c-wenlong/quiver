import tempfile
import unittest
from pathlib import Path

from quiver.skills.symlinks import apply_skills_symlink_hints, skills_symlink_hints


class SkillsSymlinksTest(unittest.TestCase):
    def test_suggests_symlink_when_cursor_skills_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)
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
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)

            hints = skills_symlink_hints(home=home)
            applied = apply_skills_symlink_hints(hints, home=home)
            self.assertTrue(any("cursor" in item for item in applied))
            cursor_link = home / ".cursor" / "skills"
            self.assertTrue(cursor_link.is_symlink())
            self.assertEqual(cursor_link.resolve(), shared.resolve())


if __name__ == "__main__":
    unittest.main()
