import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.skills.catalog_discover import apply_skill_catalog_findings, discover_skill_catalogs
from swe.skills.catalogs import (
    _same_path,
    add_skill_catalog,
    discover_catalog_dirs,
    load_skill_catalogs,
    remove_skill_catalog,
    suggest_catalog_label,
)
from swe.skills.discovery import discover_skills, skill_roots


def _catalog_patches(config_dir: Path, catalogs_file: Path):
    return (
        patch("swe.paths.CONFIG_DIR", config_dir),
        patch("swe.paths.SKILL_CATALOGS_FILE", catalogs_file),
        patch("swe.skills.catalogs.SKILL_CATALOGS_FILE", catalogs_file),
        patch("swe.skills.catalogs.CONFIG_DIR", config_dir),
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
                "swe.skills.catalog_discover.discover_catalog_dirs",
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
                from swe.skills.catalog_commands import cmd_skills_catalog

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


    def test_remove_matches_a_label_or_a_whole_path_never_a_substring(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            eng = home / "engineering" / "skills"
            engines = home / "design-engines" / "skills"
            for root in (eng, engines):
                self._write_skill(root, "one", name="one")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(eng, "eng")
                add_skill_catalog(engines, "engines")
                # "eng" is a substring of both paths; only its label matches.
                self.assertTrue(remove_skill_catalog("eng"))
                self.assertEqual([c["label"] for c in load_skill_catalogs()], ["engines"])
                self.assertFalse(remove_skill_catalog("design"))
                self.assertTrue(remove_skill_catalog(str(engines)))
                self.assertEqual(load_skill_catalogs(), [])

    def test_remove_by_a_path_that_cannot_resolve_removes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            root = home / "team" / "skills"
            self._write_skill(root, "one", name="one")

            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(root, "team")
                with patch("swe.skills.catalogs.resolve_catalog_path",
                           side_effect=RuntimeError("symlink loop")):
                    self.assertFalse(remove_skill_catalog("/loop"))
                with patch("swe.skills.catalogs.expand_user",
                           side_effect=OSError("unreadable")):
                    self.assertFalse(_same_path("~/team/skills", root))
                self.assertEqual([c["label"] for c in load_skill_catalogs()], ["team"])

class SkillsCatalogCommandTest(unittest.TestCase):
    def _run(self, args):
        import io
        from contextlib import redirect_stdout
        from swe.skills.catalog_commands import cmd_skills_catalog
        out = io.StringIO()
        with redirect_stdout(out):
            code = cmd_skills_catalog(args)
        return code, out.getvalue()

    def test_help_runs(self):
        code, out = self._run(["help"])
        self.assertEqual(code, 0)

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("No catalogs configured", out)

    def test_list_renders_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "skills-tree"
            catalog.mkdir()
            SkillsCatalogTest()._write_skill(catalog, "one", name="one")
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                add_skill_catalog(catalog, "mine")
                code, out = self._run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("mine", out)
        self.assertIn("SKILLS", out)

    def test_remove_without_arg_is_usage_error(self):
        code, out = self._run(["remove"])
        self.assertEqual(code, 1)
        self.assertIn("Usage", out)

    def test_remove_missing_catalog_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                code, out = self._run(["remove", "ghost"])
        self.assertEqual(code, 1)
        self.assertIn("No catalog matching", out)

    def test_add_missing_path_fails(self):
        code, out = self._run(["add", "/definitely/not/there"])
        self.assertEqual(code, 1)

    def test_unknown_subcommand_fails(self):
        code, out = self._run(["frobnicate"])
        self.assertEqual(code, 1)

    def test_bare_path_argument_adds_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "swe"
            catalogs_file = config_dir / "skill_catalogs.json"
            catalog = home / "skills-tree"
            catalog.mkdir()
            SkillsCatalogTest()._write_skill(catalog, "one", name="one")
            p1, p2, p3, p4 = _catalog_patches(config_dir, catalogs_file)
            with p1, p2, p3, p4:
                code, _ = self._run([str(catalog)])
                self.assertEqual(code, 0)
                self.assertEqual(len(load_skill_catalogs()), 1)


class SkillsDiscoverCommandTest(unittest.TestCase):
    def _finding(self, label="cat", status="new", count=2):
        from swe.skills.catalog_discover import SkillCatalogFinding
        return SkillCatalogFinding(path=Path("/x/skills"), label=label,
                                   skill_count=count, source="desktop",
                                   status=status)

    def _run(self, args, findings, apply_return=None):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        from swe.skills import catalog_commands as cc
        apply_fn = mock.Mock(return_value=apply_return if apply_return is not None else [])
        out = io.StringIO()
        with patch.object(cc, "discover_skill_catalogs", lambda **k: findings), \
                patch.object(cc, "apply_skill_catalog_findings", apply_fn), \
                redirect_stdout(out):
            code = cc.cmd_skills_discover(args)
        return code, out.getvalue(), apply_fn

    def test_table_lists_findings(self):
        code, out, _ = self._run([], [self._finding()])
        self.assertEqual(code, 0)
        self.assertIn("Skills Discover", out)
        self.assertIn("cat", out)

    def test_empty_findings_say_so(self):
        code, out, _ = self._run([], [])
        self.assertEqual(code, 0)
        self.assertIn("No new skill catalogs", out)

    def test_apply_reports_added(self):
        code, out, apply_fn = self._run(["--apply"], [self._finding()],
                                        apply_return=["cat"])
        self.assertEqual(code, 0)
        self.assertIn("Registered 1 catalog", out)
        apply_fn.assert_called_once()

    def test_apply_nothing_to_add(self):
        code, out, _ = self._run(["--apply"], [self._finding(status="registered")],
                                 apply_return=[])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to add", out)

    def test_help_runs(self):
        code, _out, _ = self._run(["-h"], [])
        self.assertEqual(code, 0)

    def test_unknown_arg_fails(self):
        code, _out, apply_fn = self._run(["bogus"], [])
        self.assertEqual(code, 1)
        apply_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
