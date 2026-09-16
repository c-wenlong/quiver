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


class SignatureTest(unittest.TestCase):
    """Known-harness roots come from evidence, not the skills dir itself.

    Cline's CLI creates only ~/.cline/data on first run; kilo keeps its
    config in ~/.config/kilo and its skills root in ~/.kilo, whose parent
    it never makes. Both are invisible to a skills/ glob and to a
    parent-exists seed — the evidence field is what finds them.
    """

    def test_kilo_found_from_config_dir_without_kilo_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".config" / "kilo").mkdir(parents=True)
            found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
            self.assertIn(".kilo/skills", found)

    def test_cline_found_from_dotdir_with_no_skills_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cline" / "data").mkdir(parents=True)
            found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
            self.assertIn(".cline/skills", found)

    def test_no_evidence_means_no_signature_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
            self.assertNotIn(".kilo/skills", found)
            self.assertNotIn(".cline/skills", found)

    def test_registry_declared_root_is_found_outside_the_glob(self):
        # capabilities.skills.root wins even where the glob cannot reach:
        # ~/.pi/agent/skills is one level deeper than .*/skills matches.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"pi": {"capabilities": {
                "skills": {"supported": True, "root": "~/.pi/agent/skills"}}}}
            found = {str(p.relative_to(home))
                     for p in discover_skill_roots(home, registry)}
            self.assertIn(".pi/agent/skills", found)

    def test_supported_false_registry_root_is_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"aside": {"capabilities": {
                "skills": {"supported": False, "root": "~/.aside/skills"}}}}
            found = {str(p.relative_to(home))
                     for p in discover_skill_roots(home, registry)}
            self.assertNotIn(".aside/skills", found)

    def test_signature_targets_link_on_init(self):
        from quiver.init.layout import plan

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cline" / "data").mkdir(parents=True)
            (home / ".config" / "kilo").mkdir(parents=True)
            instructions, skills = plan(home)
            by_label = {s.label: s for s in instructions}
            self.assertEqual(by_label["cline"].state, "create")
            self.assertEqual(by_label["cline"].path, home / ".agents" / "AGENTS.md")
            self.assertEqual(by_label["kilo"].state, "create")
            self.assertEqual(
                by_label["kilo"].path, home / ".config" / "kilo" / "AGENTS.md")
            skill_states = {s.label: s.state for s in skills}
            self.assertEqual(skill_states["cline"], "create")
            self.assertEqual(skill_states["kilo"], "create")

    def test_evidence_without_instructions_parent_still_creates(self):
        # ~/.cline exists but ~/.agents does not: the signature's evidence
        # upgrades the row from skipped to create, and init makes the dir.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".cline" / "data").mkdir(parents=True)
            code, _ = self._run_init(home, [])
            self.assertEqual(code, 0)
            self.assertTrue((home / ".agents" / "AGENTS.md").is_symlink())
            self.assertTrue((home / ".cline" / "skills").is_symlink())

    def test_uninstalled_signature_instruction_stays_skipped(self):
        from quiver.init.layout import plan

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            instructions, _ = plan(home)
            by_label = {s.label: s for s in instructions}
            self.assertEqual(by_label["cline"].state, "skipped")
            self.assertEqual(by_label["kilo"].state, "skipped")

    def _run_init(self, home, args):
        from quiver.init import commands as init_commands

        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
        return code, buf.getvalue()


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
