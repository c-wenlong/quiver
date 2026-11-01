import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.skills.layout import enumerate_skill_roots, layout_groups, sync_link_records_from_filesystem
from swe.skills.link_ops import (
    SkillLayoutError,
    find_skill_directory,
    link_skill_root,
    move_skill,
    unlink_skill_root,
)


class SkillsLayoutTest(unittest.TestCase):
    def _write_skill(self, root: Path, folder: str, name: str = "demo"):
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")

    def test_enumerate_shows_symlinks_not_deduped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
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
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "shared-skill", "shared-skill")
            codex = home / ".codex" / "skills"

            link_skill_root("codex", "shared", home=home)
            self.assertTrue(codex.is_symlink())
            self.assertEqual(codex.resolve(), shared.resolve())

            unlink_skill_root("codex", home=home, mkdir=True)
            self.assertFalse(codex.is_symlink())
            self.assertTrue(codex.is_dir())

    def test_link_replaces_a_dangling_symlink(self):
        # exists() is False on a dangling link, so the old code fell
        # through to symlink_to() and died with FileExistsError even
        # though the path was only a broken link.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(home / ".codex" / "gone")

            link_skill_root("codex", "shared", home=home)
            self.assertTrue(codex.is_symlink())
            self.assertEqual(codex.resolve(), shared.resolve())

    def test_unlink_removes_a_dangling_symlink(self):
        # The inverse bug: exists() reported "does not exist" for a broken
        # link, so `swe skills unlink` could never remove one.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(home / ".codex" / "gone")

            unlink_skill_root("codex", home=home)
            self.assertFalse(os.path.lexists(codex))

    def test_move_from_a_literal_path_root(self):
        # --from takes a root ref, so a path must work like a label.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foo = home / ".foo" / "skills"
            codex = home / ".codex" / "skills"
            foo.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(foo, "move-me", "move-me")

            src, dest = move_skill("move-me", str(foo), "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_from_a_discovered_root_label(self):
        # ~/.foo/skills is found by the on-disk scan but has no builtin
        # label; the label `swe find skills` reports must still resolve.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foo = home / ".foo" / "skills"
            codex = home / ".codex" / "skills"
            foo.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(foo, "move-me", "move-me")

            src, dest = move_skill("move-me", "foo", "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_from_a_symlinked_root_by_path(self):
        # foo/skills -> shared, so its skills are reported under "shared";
        # only containment under the resolved root finds them via the
        # link's own path.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            foo = home / ".foo" / "skills"
            foo.parent.mkdir(parents=True)
            foo.symlink_to(shared)

            src, dest = move_skill("move-me", str(foo), "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())

    def test_move_from_a_path_ignores_same_label_roots(self):
        # ~/.foo/skills and ~/.config/foo/skills both carry the label
        # "foo"; an explicit path must still mean exactly one root.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            foo = home / ".foo" / "skills"
            other = home / ".config" / "foo" / "skills"
            codex = home / ".codex" / "skills"
            for root in (foo, other, codex):
                root.mkdir(parents=True)
            self._write_skill(foo, "move-me", "move-me")
            self._write_skill(other, "move-me", "move-me")

            src, dest = move_skill("move-me", str(foo), "codex", home=home)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())
            # The same-named skill under the other "foo" root is untouched.
            self.assertTrue((other / "move-me" / "SKILL.md").exists())

    def test_move_reports_a_broken_destination_symlink(self):
        # The broken-link check ran after the exists() check, so it could
        # never fire: a dangling --to root read as "does not exist".
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(home / ".codex" / "gone")

            with self.assertRaises(SkillLayoutError) as ctx:
                move_skill("move-me", "shared", "codex", home=home)
            self.assertIn("broken", str(ctx.exception))
            self.assertTrue((shared / "move-me" / "SKILL.md").exists())

    def test_move_reports_a_missing_destination_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")

            # kilo is a known label whose root is not on disk.
            with self.assertRaises(SkillLayoutError) as ctx:
                move_skill("move-me", "shared", "kilo", home=home)
            self.assertIn("does not exist", str(ctx.exception))
            self.assertTrue((shared / "move-me" / "SKILL.md").exists())

    def test_find_skill_directory_with_an_unresolvable_scope(self):
        # A scope that resolves to nothing answers "No skill matching",
        # not the resolver's "Unknown skill root" error.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaises(SkillLayoutError) as ctx:
                find_skill_directory("x", "bogus", home=home, cwd=home)
            self.assertIn("No skill matching", str(ctx.exception))

    def test_move_skill_between_unlinked_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
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
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "x", "x")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with self.assertRaises(SkillLayoutError):
                move_skill("x", "shared", "codex", home=home)

    def test_move_rejects_same_resolved_tree_even_with_force(self):
        # Same-tree + force used to proceed, then fail (or worse, once
        # force gained a real overwrite path, back up the folder it was
        # about to move). The check is unconditional now.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            self._write_skill(shared, "x", "x")
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with self.assertRaises(SkillLayoutError):
                move_skill("x", "shared", "codex", home=home, force=True)
            self.assertTrue((shared / "x" / "SKILL.md").exists())

    def test_move_refuses_existing_destination_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            self._write_skill(codex, "move-me", "move-me")

            with self.assertRaises(SkillLayoutError):
                move_skill("move-me", "shared", "codex", home=home)
            self.assertTrue((shared / "move-me" / "SKILL.md").exists())

    def test_move_force_backs_up_and_replaces_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".swe" / "skills"
            codex = home / ".codex" / "skills"
            shared.mkdir(parents=True)
            codex.mkdir(parents=True)
            self._write_skill(shared, "move-me", "move-me")
            dest_dir = codex / "move-me"
            self._write_skill(codex, "move-me", "old-version")
            (dest_dir / "extra.txt").write_text("keep me safe", encoding="utf-8")

            src, dest = move_skill("move-me", "shared", "codex", home=home, force=True)
            self.assertFalse(src.exists())
            self.assertTrue((dest / "SKILL.md").exists())
            # The replaced destination was backed up, not deleted.
            backups = list((home / ".swe" / "backups").glob("**/extra.txt"))
            self.assertEqual(len(backups), 1)

    def test_sync_link_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with patch("swe.paths.SKILL_LINKS_FILE", links_file), patch(
                "swe.paths.CONFIG_DIR", config_dir
            ), patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                synced = sync_link_records_from_filesystem(home=home)
                self.assertIn("codex", synced)
                data = json.loads(links_file.read_text())
                self.assertEqual(data["links"][0]["label"], "codex")

    def test_sync_link_records_resolves_relative_target_from_link_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(Path("../.swe/skills"))

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                sync_link_records_from_filesystem(home=home)

            data = json.loads(links_file.read_text())
            codex_record = next(link for link in data["links"] if link["label"] == "codex")
            self.assertEqual(Path(codex_record["target"]), shared.resolve())

    def test_sync_link_records_removes_stale_harness_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            links_file = config_dir / "skill_links.json"
            config_dir.mkdir(parents=True)
            links_file.write_text(
                json.dumps(
                    {
                        "links": [
                            {
                                "label": "codex",
                                "path": str(home / ".codex" / "skills"),
                                "target": str(home / ".swe" / "skills"),
                                "kind": "symlink",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                synced = sync_link_records_from_filesystem(home=home)

            self.assertNotIn("codex", synced)
            self.assertEqual(json.loads(links_file.read_text())["links"], [])

    def test_save_link_records_refuses_a_corrupt_file(self):
        """Reads degrade to [] but a write must not commit that []."""
        from swe.configuration import CorruptConfigurationError
        from swe.skills.layout import load_link_records, save_link_records

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            config_dir.mkdir(parents=True)
            links_file = config_dir / "skill_links.json"
            links_file.write_text("{corrupt", encoding="utf-8")

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                self.assertEqual(load_link_records(), [])
                with self.assertRaises(CorruptConfigurationError):
                    save_link_records([{"label": "x"}])

            # Untouched: the corrupt file is still there to fix by hand.
            self.assertEqual(links_file.read_text(), "{corrupt")

    def test_unlink_preflights_the_records_file_before_unlinking(self):
        """A corrupt skill_links.json used to surface only after the
        symlink was already gone, leaving the root missing on retry."""
        from swe.configuration import CorruptConfigurationError

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            config_dir.mkdir(parents=True)
            links_file = config_dir / "skill_links.json"
            links_file.write_text("{corrupt", encoding="utf-8")
            shared = home / ".swe" / "skills"
            shared.mkdir(parents=True)
            codex = home / ".codex" / "skills"
            codex.parent.mkdir(parents=True)
            codex.symlink_to(shared)

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                with self.assertRaises(CorruptConfigurationError):
                    unlink_skill_root("codex", home=home)

            # The link still stands: nothing moved, so a fixed file can retry.
            self.assertTrue(codex.is_symlink())

    def test_save_link_records_overwrites_a_foreign_json_value(self):
        """A parseable non-dict file holds no records to lose."""
        from swe.skills.layout import save_link_records

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".config" / "swe"
            config_dir.mkdir(parents=True)
            links_file = config_dir / "skill_links.json"
            links_file.write_text("[1, 2]", encoding="utf-8")

            with patch("swe.skills.layout.SKILL_LINKS_FILE", links_file), patch(
                "swe.skills.layout.CONFIG_DIR", config_dir
            ):
                save_link_records([{"label": "x", "path": "/p",
                                    "target": "/t", "kind": "symlink"}])
            self.assertEqual(
                json.loads(links_file.read_text())["links"][0]["label"], "x")


if __name__ == "__main__":
    unittest.main()
