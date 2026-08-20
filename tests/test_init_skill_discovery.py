"""Skill roots are discovered, not listed.

A hardcoded list went stale the moment a new harness was installed: it covered
14 roots while 60 existed on disk, so seven byte-identical duplicate trees sat
unlinked for a month. These tests pin the discovery rules and, more
importantly, the safety rule: a directory holding skills that exist nowhere
else is never replaced without --force.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.init import commands as init_commands
from quiver.init.layout import (
    classify_skill_root,
    discover_skill_roots,
    skill_root_label,
)


def _skill(root: Path, name: str, body: str = "x") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")


def _home(tmp: str) -> Path:
    home = Path(tmp)
    shared = home / ".quiver" / "skills"
    for n in ("alpha", "beta"):
        _skill(shared, n)
    return home


class DiscoveryTest(unittest.TestCase):
    def test_finds_dotdir_and_config_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".config" / "opencode" / "skills").mkdir(parents=True)
            found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
            self.assertIn(".claude/skills", found)
            self.assertIn(".config/opencode/skills", found)

    def test_excludes_the_shared_tree_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
            self.assertNotIn(".quiver/skills", found)

    def test_skips_backup_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".hermes.pre-bootstrap-20260730" / "skills").mkdir(parents=True)
            (home / ".hermes" / "skills").mkdir(parents=True)
            found = {p.parent.name for p in discover_skill_roots(home)}
            self.assertIn(".hermes", found)
            self.assertNotIn(".hermes.pre-bootstrap-20260730", found)

    def test_does_not_recurse_into_projects(self):
        # Project-level .cursor/skills is not quiver's business.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            deep = home / "Desktop" / "proj" / ".cursor" / "skills"
            deep.mkdir(parents=True)
            self.assertNotIn(deep, discover_skill_roots(home))

    def test_label_strips_the_dot(self):
        self.assertEqual(skill_root_label(Path("/h/.qwen/skills")), "qwen")
        self.assertEqual(skill_root_label(Path("/h/.config/opencode/skills")), "opencode")


class ClassifyTest(unittest.TestCase):
    def test_empty_directory_is_absorbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".qwen" / "skills"
            root.mkdir(parents=True)
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "absorb")
            self.assertEqual(detail, "empty")

    def test_pure_duplicate_is_absorbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".copilot" / "skills"
            for n in ("alpha", "beta"):
                _skill(root, n)
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "absorb")
            self.assertIn("already shared", detail)

    def test_unique_content_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            _skill(root, "alpha")
            _skill(root, "only-here")
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")
            self.assertIn("1 of 2", detail)

    def test_existing_symlink_reads_as_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".qwen" / "skills"
            root.parent.mkdir(parents=True)
            root.symlink_to(home / ".quiver" / "skills")
            self.assertEqual(classify_skill_root(root, home)[0], "linked")

    def test_symlink_elsewhere_reads_as_relink(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            other = home / "somewhere"
            other.mkdir()
            root = home / ".qwen" / "skills"
            root.parent.mkdir(parents=True)
            root.symlink_to(other)
            self.assertEqual(classify_skill_root(root, home)[0], "relink")


class SafetyTest(unittest.TestCase):
    def _run(self, home, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
        return code, buf.getvalue()

    def test_unique_skills_survive_a_plain_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            pane = home / ".pane" / "skills"
            _skill(pane, "only-here", "irreplaceable")
            dup = home / ".copilot" / "skills"
            _skill(dup, "alpha")

            self._run(home, [])

            self.assertFalse(pane.is_symlink(), "unique tree must not be replaced")
            self.assertTrue((pane / "only-here" / "SKILL.md").is_file())
            self.assertTrue(dup.is_symlink(), "pure duplicate should be absorbed")

    def test_protected_roots_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".pane" / "skills", "only-here")
            _, out = self._run(home, [])
            self.assertIn("exist nowhere else", out)
            self.assertIn(".pane/skills", out)

    def test_force_absorbs_a_unique_tree_but_backs_it_up_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            pane = home / ".pane" / "skills"
            _skill(pane, "only-here", "irreplaceable")

            self._run(home, ["--force"])
            self.assertTrue(pane.is_symlink())

            saved = list((home / ".quiver" / "backups").glob("*pane_skills*"))
            self.assertEqual(len(saved), 1)
            self.assertIn(
                "irreplaceable", (saved[0] / "only-here" / "SKILL.md").read_text()
            )

    def test_absorbed_duplicate_is_backed_up_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            dup = home / ".copilot" / "skills"
            _skill(dup, "alpha")
            self._run(home, [])
            self.assertEqual(
                len(list((home / ".quiver" / "backups").glob("*copilot_skills*"))), 1
            )

    def test_check_mode_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            dup = home / ".copilot" / "skills"
            _skill(dup, "alpha")
            self._run(home, ["--check"])
            self.assertFalse(dup.is_symlink())
            self.assertTrue((dup / "alpha" / "SKILL.md").is_file())

    def test_rerun_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".copilot" / "skills", "alpha")
            _skill(home / ".pane" / "skills", "only-here")
            self._run(home, [])
            backups_after_first = len(list((home / ".quiver" / "backups").iterdir()))
            self._run(home, [])
            self.assertEqual(
                len(list((home / ".quiver" / "backups").iterdir())),
                backups_after_first,
                "a second run should absorb nothing new",
            )


if __name__ == "__main__":
    unittest.main()
