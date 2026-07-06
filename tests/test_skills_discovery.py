import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.skills.discovery import discover_skills, parse_skill_md, skill_roots


class SkillsDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.skills = self.home / ".agents" / "skills"
        self.builtin = self.home / ".cursor" / "skills-cursor"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_skill(self, root: Path, folder: str, name: str, description: str):
        skill_dir = root / folder
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
            encoding="utf-8",
        )

    def test_parse_skill_md_reads_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "demo-skill" / "SKILL.md"
            md.parent.mkdir()
            md.write_text("---\nname: demo\ndescription: A demo skill\n---\n")
            name, desc = parse_skill_md(md)
            self.assertEqual(name, "demo")
            self.assertEqual(desc, "A demo skill")

    def test_skill_roots_dedupes_symlinked_paths(self):
        self.skills.mkdir(parents=True)
        self.builtin.mkdir(parents=True)
        linked = self.home / ".cursor" / "skills"
        linked.symlink_to(self.skills)

        roots = skill_roots(home=self.home, cwd=self.home)
        labels = [label for label, _ in roots]
        self.assertIn("shared", labels)
        self.assertIn("cursor-builtin", labels)
        # shared and cursor both point at same real path — first label wins
        real_paths = [str(p.resolve()) for _, p in roots]
        self.assertEqual(len(real_paths), len(set(real_paths)))

    def test_discover_skills_finds_multiple_scopes(self):
        self._write_skill(self.skills, "alpha", "alpha", "First skill")
        self._write_skill(self.builtin, "beta", "beta", "Builtin skill")

        with patch("quiver.skills.discovery.load_skill_catalogs", return_value=[]):
            found = discover_skills(home=self.home, cwd=self.home)
        names = {s["name"] for s in found}
        scopes = {s["scope"] for s in found}
        self.assertEqual(names, {"alpha", "beta"})
        self.assertIn("shared", scopes)
        self.assertIn("cursor-builtin", scopes)


if __name__ == "__main__":
    unittest.main()
