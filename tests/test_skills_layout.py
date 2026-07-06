import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.skills.layout import enumerate_skill_roots, layout_groups, sync_link_records_from_filesystem
from quiver.skills.link_ops import SkillLayoutError, link_skill_root, move_skill, unlink_skill_root


class SkillsLayoutTest(unittest.TestCase):
    def _write_skill(self, root: Path, folder: str, name: str = "demo"):
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")

    def test_enumerate_shows_symlinks_not_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "alpha", "alpha")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            entries = {e.label: e for e in enumerate_skill_roots(home=home)}
            self.assertEqual(entries["shared"].kind, "directory")
            self.assertEqual(entries["codex"].kind, "symlink")
            self.assertEqual(entries["codex"].link_target_label, "shared")
            self.assertIn("codex", entries["shared"].aliases)

    def test_link_and_unlink_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "shared-skill", "shared-skill")
            codex = home / ".codex" / "skills"

            link_skill_root("codex", "shared", home=home)
            self.assertTrue(codex.is_symlink())
            self.assertEqual(codex.resolve(), shared.resolve())

            unlink_skill_root("codex", home=home, mkdir=True)
            self.assertFalse(codex.is_symlink())
            self.assertTrue(codex.is_dir())

    def test_move_skill_between_unlinked_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".agents" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")

            src, dest = move_skill("move-me", "shared", "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_rejects_same_resolved_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "x", "x")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with self.assertRaises(SkillLayoutError):
                move_skill("x", "shared", "codex", home=home)

    def test_sync_link_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            shared = home / ".agents" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with patch("quiver.paths.SKILL_LINKS_FILE", links_file), patch(
                "quiver.paths.CONFIG_DIR", config_dir
            ), patch("quiver.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "quiver.skills.layout.CONFIG_DIR", config_dir
            ):
                synced = sync_link_records_from_filesystem(home=home)
                self.assertIn("codex", synced)
                data = json.loads(links_file.read_text())
                self.assertEqual(data["links"][0]["label"], "codex")


if __name__ == "__main__":
    unittest.main()
