import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.skills.catalog_discover import apply_skill_catalog_findings, discover_skill_catalogs
from quiver.skills.catalogs import (
    add_skill_catalog,
    discover_catalog_dirs,
    load_skill_catalogs,
    remove_skill_catalog,
    suggest_catalog_label,
)
from quiver.skills.discovery import discover_skills, skill_roots


def _catalog_patches(config_dir: Path, catalogs_file: Path):
    return (
        patch("quiver.paths.CONFIG_DIR", config_dir),
        patch("quiver.paths.SKILL_CATALOGS_FILE", catalogs_file),
        patch("quiver.skills.catalogs.SKILL_CATALOGS_FILE", catalogs_file),
        patch("quiver.skills.catalogs.CONFIG_DIR", config_dir),
    )


class SkillsCatalogTest(unittest.TestCase):
    def _write_skill(self, root: Path, *parts: str, name: str = "demo"):
        skill_dir = root.joinpath(*parts)
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n",
            encoding="utf-8",
        )

    def test_suggest_label_uses_parent_when_basename_is_skills(self):
        path = Path("/tmp/ai-engineering/skills")
        self.assertEqual(suggest_catalog_label(path), "ai-engineering")

    def test_discover_finds_skills_dir_under_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            catalog = home / "Desktop" / "Personal" / "Projects" / "ai-engineering" / "skills"
            self._write_skill(catalog, "my-skill", name="my-skill")

            found = discover_catalog_dirs(home)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].resolve(), catalog.resolve())

    def test_catalog_add_and_list_makes_skills_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "Desktop" / "ai-engineering" / "skills"
            self._write_skill(catalog, "alpha", name="alpha")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(catalog, "ai-engineering")
                roots = skill_roots(home=home, cwd=home)
                labels = [label for label, _ in roots]
                self.assertIn("ai-engineering", labels)
                skills = discover_skills(home=home, cwd=home)
                self.assertEqual({s["name"] for s in skills}, {"alpha"})
                self.assertEqual(skills[0]["scope"], "ai-engineering")

    def test_discover_and_apply_registers_new_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "Documents" / "work" / "skills"
            self._write_skill(catalog, "beta", name="beta")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4, patch(
                "quiver.skills.catalog_discover.discover_catalog_dirs",
                return_value=[catalog.resolve()],
            ):
                findings = discover_skill_catalogs(home=home, cwd=home)
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].status, "new")
                added = apply_skill_catalog_findings(findings)
                self.assertEqual(added, ["work"])
                saved = load_skill_catalogs()
                self.assertEqual(len(saved), 1)

    def test_catalog_add_dot_uses_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "work" / "ai-engineering" / "skills"
            self._write_skill(catalog, "gamma", name="gamma")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4, patch("os.getcwd", return_value=str(catalog)):
                from quiver.skills.catalog_commands import cmd_skills_catalog

                self.assertEqual(cmd_skills_catalog(["."]), 0)
                saved = load_skill_catalogs()
                self.assertEqual(len(saved), 1)
                self.assertEqual(Path(saved[0]["path"]).resolve(), catalog.resolve())

                catalogs_file.unlink(missing_ok=True)
                self.assertEqual(cmd_skills_catalog(["add"]), 0)
                saved = load_skill_catalogs()
                self.assertEqual(Path(saved[0]["path"]).resolve(), catalog.resolve())

    def test_remove_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "skills-tree"
            catalog.mkdir()
            self._write_skill(catalog, "one", name="one")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(catalog, "mine")
                self.assertTrue(remove_skill_catalog("mine"))
                self.assertEqual(load_skill_catalogs(), [])


if __name__ == "__main__":
    unittest.main()
