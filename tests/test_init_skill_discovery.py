"""Skill roots are discovered, not listed.

A hardcoded list went stale the moment a new harness was installed: it covered
14 roots while 60 existed on disk, so seven byte-identical duplicate trees sat
unlinked for a month. These tests pin the discovery rules and, more
importantly, the safety rule: a directory holding skills that exist nowhere
else is never replaced without --force.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from swe.init import commands as init_commands
from swe.init.layout import (
    classify_skill_root,
    discover_skill_roots,
    skill_folder_scan,
    skill_root_label,
)


def _skill(root: Path, name: str, body: str = "x") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n{body}\n")


def _home(tmp: str) -> Path:
    home = Path(tmp)
    shared = home / ".swe" / "skills"
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
            self.assertNotIn(".swe/skills", found)

    def test_skips_backup_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".hermes.pre-bootstrap-20260730" / "skills").mkdir(parents=True)
            (home / ".hermes" / "skills").mkdir(parents=True)
            found = {p.parent.name for p in discover_skill_roots(home)}
            self.assertIn(".hermes", found)
            self.assertNotIn(".hermes.pre-bootstrap-20260730", found)

    def test_does_not_recurse_into_projects(self):
        # Project-level .cursor/skills is not swe's business.
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
            # A binary on PATH is evidence too (#68): stub it out or a
            # dev machine with kilo/cline installed fails this assertion.
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
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

    def test_declared_root_cannot_be_the_shared_tree(self):
        # A malformed capability resolving to ~/.swe/skills would let
        # init classify and replace the link target itself.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"evil": {"capabilities": {
                "skills": {"supported": True, "root": "~/.swe/skills"}}}}
            found = {str(p.relative_to(home))
                     for p in discover_skill_roots(home, registry)}
            self.assertNotIn(".swe/skills", found)

    def test_declared_root_cannot_escape_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"evil": {"capabilities": {
                "skills": {"supported": True, "root": "~/../escape/skills"}}}}
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                self.assertEqual(discover_skill_roots(home, registry), [])

    def test_binary_on_path_finds_signature_root(self):
        # #68: Nix-profile crush — the binary resolves but ~/.config/crush
        # was never made, so dir evidence alone skipped it. Installed by
        # command, the signature root must be found and linked.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            which = lambda name: "/fake/bin/crush" if name == "crush" else None
            with mock.patch("swe.init.layout.shutil.which", side_effect=which):
                found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
            self.assertIn(".config/crush/skills", found)
            self.assertNotIn(".kilo/skills", found)

    def test_supported_false_registry_root_is_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"aside": {"capabilities": {
                "skills": {"supported": False, "root": "~/.aside/skills"}}}}
            found = {str(p.relative_to(home))
                     for p in discover_skill_roots(home, registry)}
            self.assertNotIn(".aside/skills", found)

    def test_supported_false_suppresses_a_signature_root(self):
        # The registry wins over inference: kilo is installed (its evidence
        # is on disk) but the user declared skills unsupported, so init
        # must not create ~/.kilo/skills anyway.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".config" / "kilo").mkdir(parents=True)
            registry = {"kilo": {"capabilities": {
                "skills": {"supported": False}}}}
            found = {str(p.relative_to(home))
                     for p in discover_skill_roots(home, registry)}
            self.assertNotIn(".kilo/skills", found)

    def test_supported_false_suppresses_a_glob_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".foo" / "skills").mkdir(parents=True)
            registry = {"foo": {"capabilities": {
                "skills": {"supported": False}}}}
            found = {str(p.relative_to(home))
                     for p in discover_skill_roots(home, registry)}
            self.assertNotIn(".foo/skills", found)

    def test_declared_root_labels_as_its_registry_key(self):
        # ~/.pi/agent/skills labels as "pi", not "agent": an active pi must
        # not be re-offered to the picker, an archived one must stay
        # ignored.
        from swe.init.layout import plan
        from swe.init.manage import new_harnesses

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"pi": {"capabilities": {
                "skills": {"supported": True, "root": "~/.pi/agent/skills"}}}}
            path = home / ".pi" / "agent" / "skills"
            self.assertEqual(skill_root_label(path, home, registry), "pi")
            _, skills = plan(home, registry=registry)
            self.assertEqual(
                [s.label for s in skills if s.path == path], ["pi"])
            self.assertEqual(new_harnesses(skills, registry), [])

    def test_archived_declared_root_is_ignored(self):
        from swe.init.layout import plan

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            registry = {"pi": {
                "state": "archived",
                "capabilities": {"skills": {"root": "~/.pi/agent/skills"}},
            }}
            _, skills = plan(home, registry=registry)
            by_path = {s.path: s for s in skills}
            self.assertEqual(
                by_path[home / ".pi" / "agent" / "skills"].state, "ignored")

    def test_leftover_agents_dir_does_not_imply_cline(self):
        # ~/.agents was swe's own pre-0.2.7 root: a leftover proves
        # nothing about cline, so its skills root must not appear. The
        # AGENTS.md row still plans create — like every row, it creates a
        # missing file under an existing dir — but that file is the shared
        # standard target, not cline-specific state.
        from swe.init.layout import plan

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            (home / ".agents").mkdir()
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                found = {str(p.relative_to(home)) for p in discover_skill_roots(home)}
                self.assertNotIn(".cline/skills", found)
                instructions, _ = plan(home)
            by_label = {s.label: s for s in instructions}
            self.assertEqual(by_label["cline"].state, "create")
            self.assertFalse((home / ".cline").exists())

    def test_signature_targets_link_on_init(self):
        from swe.init.layout import plan

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
        from swe.init.layout import plan

        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            # Same PATH stub as above: on a machine with these binaries the
            # rows would legitimately read create — install by binary is
            # install evidence, and this test is about the absence of both.
            with mock.patch("swe.init.layout.shutil.which", return_value=None):
                instructions, _ = plan(home)
            by_label = {s.label: s for s in instructions}
            self.assertEqual(by_label["cline"].state, "skipped")
            self.assertEqual(by_label["kilo"].state, "skipped")

    def _run_init(self, home, args):
        from swe.init import commands as init_commands

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

    def test_same_name_different_content_is_kept(self):
        # A folder-name match is not a copy: absorbing a private "alpha"
        # with different content would silently swap in an unrelated skill.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            _skill(root, "alpha", "completely different body")
            _skill(root, "beta")
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")
            self.assertIn("1 of 2", detail)

    def test_same_name_different_file_set_is_kept(self):
        # Same SKILL.md but extra private files: not a copy either.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            _skill(root, "alpha")
            _skill(root, "beta")
            (root / "alpha" / "helper.sh").write_text("#!/bin/sh\n")
            state, _detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")

    def test_same_name_same_size_helper_is_kept(self):
        # Sizes matching is not content matching: an edited helper with the
        # same length used to pass _same_skill and get absorbed.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            shared = home / ".swe" / "skills"
            (shared / "alpha" / "helper.sh").write_text("#!/bin/sh\n")
            root = home / ".pane" / "skills"
            _skill(root, "alpha")
            _skill(root, "beta")
            (root / "alpha" / "helper.sh").write_text("#!/bin/xx\n")
            state, _detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")

    def test_second_folder_with_same_name_is_checked(self):
        # live/alpha matching shared must not hide a different
        # .sources/alpha — _skill_mds used to keep only one path per name.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            _skill(root, "beta")
            _skill(root / "live", "alpha")
            _skill(root / ".sources", "alpha", "private fork")
            state, _detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")

    def test_files_beside_shared_skills_are_kept(self):
        # alpha is a shared copy, but notes.txt beside it is user data the
        # absorb removal would delete.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            _skill(root, "alpha")
            (root / "notes.txt").write_text("mine\n")
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")
            self.assertIn("files", detail)

    def test_symlink_cycle_in_empty_tree_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            root.mkdir(parents=True)
            (root / "loop").symlink_to(root)
            state, _detail = classify_skill_root(root, home)
            self.assertEqual(state, "absorb")

    def test_nonempty_dir_without_skills_is_kept(self):
        # "Absorb empty" must mean empty: files that are not skills are
        # user data, never silently deleted.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".pane" / "skills"
            (root / "notes").mkdir(parents=True)
            (root / "notes" / "todo.txt").write_text("keep me\n")
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "keep")
            self.assertIn("no skills", detail)

    def test_regular_file_at_root_is_conflict_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".qwen" / "skills"
            root.parent.mkdir(parents=True)
            root.write_text("not a dir\n")
            state, detail = classify_skill_root(root, home)
            self.assertEqual(state, "conflict")
            self.assertIn("real file", detail)

    def test_existing_symlink_reads_as_linked(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".qwen" / "skills"
            root.parent.mkdir(parents=True)
            root.symlink_to(home / ".swe" / "skills")
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
            # --yes: an unreviewed root is held, never planned for linking,
            # so the protect path only shows for one that is managed.
            _, out = self._run(home, ["--yes"])
            self.assertIn("exist nowhere else", out)
            self.assertIn(".pane/skills", out)

    def test_force_absorbs_a_unique_tree_but_backs_it_up_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            pane = home / ".pane" / "skills"
            _skill(pane, "only-here", "irreplaceable")

            self._run(home, ["--force", "--yes"])
            self.assertTrue(pane.is_symlink())

            saved = list((home / ".swe" / "backups").glob("*pane_skills*"))
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
                len(list((home / ".swe" / "backups").glob("*copilot_skills*"))), 1
            )

    def test_check_mode_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            dup = home / ".copilot" / "skills"
            _skill(dup, "alpha")
            self._run(home, ["--check"])
            self.assertFalse(dup.is_symlink())
            self.assertTrue((dup / "alpha" / "SKILL.md").is_file())

    def test_file_at_root_does_not_crash_init(self):
        # A regular file squatting on a skills root reports as a conflict,
        # not a mid-run FileExistsError after earlier roots already moved.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            root = home / ".qwen" / "skills"
            root.parent.mkdir(parents=True)
            root.write_text("not a dir\n")
            code, _out = self._run(home, [])
            self.assertEqual(code, 1, "a blocked path reports, not crashes")
            self.assertTrue(root.is_file())
            self.assertFalse(root.is_symlink())

    def test_force_never_deletes_under_quiver_or_outside_home(self):
        # A capability pointing into ~/.swe is a real file "conflict"
        # --force used to resolve by deleting it — the registry itself.
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            qdir = home / ".swe" / "config"
            qdir.mkdir(parents=True)
            victim = qdir / "harness.json"
            victim.write_text(
                json.dumps({
                    "evil": {"capabilities": {
                        "instructions": {"file": "~/.swe/config/harness.json"},
                        "skills": {"supported": False},
                    }}
                })
            )
            code, _out = self._run(home, ["--force"])
            self.assertEqual(code, 1)
            self.assertTrue(victim.is_file(), "--force deleted inside ~/.swe")
            self.assertFalse(victim.is_symlink())

    def test_rerun_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".copilot" / "skills", "alpha")
            _skill(home / ".pane" / "skills", "only-here")
            self._run(home, [])
            backups_after_first = len(list((home / ".swe" / "backups").iterdir()))
            self._run(home, [])
            self.assertEqual(
                len(list((home / ".swe" / "backups").iterdir())),
                backups_after_first,
                "a second run should absorb nothing new",
            )


class ScanCompletenessTest(unittest.TestCase):
    def test_a_capped_scan_reports_incomplete_names(self):
        # A directory cap must not surface as a finished listing: the
        # names come back with complete=False so callers can say so.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("one", "two", "three", "four"):
                _skill(root, name)
            with mock.patch("swe.init.layout._WALK_DIR_CAP", 2):
                names, complete = skill_folder_scan(root)
            self.assertFalse(complete)
            self.assertLess(len(names), 4)


if __name__ == "__main__":
    unittest.main()
