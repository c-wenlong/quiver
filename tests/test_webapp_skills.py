"""The Skills page: the standalone skills, one skill's detail, and the
guard that keeps file reads inside the skill's folder."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

from quiver.webapp.pages import PageError
from quiver.webapp.pages import skills as page

from tests.test_remote_hub import HubBase


def _skill(name, path, source="harness", harnesses=("codex",), root="codex"):
    return {"name": name, "description": f"{name} does things", "path": str(path),
            "root": root, "source": source, "harnesses": list(harnesses)}


def _catalog(plugin_folder):
    return {"marketplaces": [{"plugins": [{
        "ref": "tools@market", "source_path": str(plugin_folder),
        "harnesses": {"claude": {"path": None, "copies": []}},
    }]}]}


class TestFrontmatter(unittest.TestCase):
    def test_simple_fields_only(self):
        text = "---\nname: tdd\ndescription:  Use it\nmetadata:\n  tags: [a]\nbad key: x\nlicense: MIT\n---\nbody\n"
        self.assertEqual(page.frontmatter(text), [
            {"key": "name", "value": "tdd"}, {"key": "description", "value": "Use it"},
            {"key": "license", "value": "MIT"}])

    def test_no_frontmatter(self):
        self.assertIsNone(page.frontmatter("# Title\n"))
        self.assertIsNone(page.frontmatter("---\nname: open\n"))
        self.assertEqual(page.frontmatter("---\n---\n"), [])


class TestReadText(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_text_binary_and_big(self):
        (self.dir / "a.md").write_text("héllo")
        (self.dir / "b.bin").write_bytes(b"\x00\x01\x02")
        (self.dir / "c.md").write_bytes(b"x" * (page.MAX_TEXT + 1))
        self.assertEqual(page.read_text(self.dir / "a.md"), "héllo")
        self.assertIsNone(page.read_text(self.dir / "b.bin"))
        self.assertIsNone(page.read_text(self.dir / "c.md"))
        self.assertIsNone(page.read_text(self.dir / "missing.md"))
        self.assertIsNone(page.read_text(self.dir))
        (self.dir / "d.txt").write_bytes(b"ok \xff\xfe")
        self.assertIsNone(page.read_text(self.dir / "d.txt"))
        (self.dir / "e.md").write_bytes(b"a" * 8191 + "é and more".encode())
        self.assertTrue(page.read_text(self.dir / "e.md").endswith("é and more"))


class TestPlacement(unittest.TestCase):
    def test_plugin_skills_owned_by_one_plugin_move(self):
        base = Path(tempfile.mkdtemp())
        plugin = base / "tools"
        (plugin / "skills" / "lint").mkdir(parents=True)
        own = _skill("lint", plugin / "skills" / "lint", source="plugin", harnesses=["claude"])
        loose = _skill("stray", base / "elsewhere", source="plugin", harnesses=["claude"])
        mine = _skill("tdd", base / "mine")
        with patch("quiver.plugins.catalog.build_catalog", return_value=_catalog(plugin)):
            listed, moved, error = page.placement([own, loose, mine], base)
        self.assertEqual([s["name"] for s in listed], ["stray", "tdd"])
        self.assertEqual((moved, error), (1, None))

    def test_unreadable_catalog_keeps_every_skill(self):
        skills = [_skill("lint", "/x/lint", source="plugin")]
        with patch("quiver.plugins.catalog.build_catalog", side_effect=ValueError("bad json")):
            listed, moved, error = page.placement(skills, Path("/x"))
        self.assertEqual((listed, moved, error), (skills, 0, "bad json"))


class TestDetail(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.root = self.home / ".codex" / "skills" / "tdd"
        (self.root / "references").mkdir(parents=True)
        (self.root / "SKILL.md").write_text("---\nname: tdd\ndescription: Test first\n---\n# TDD <b>\n")
        (self.root / "references" / "loop.md").write_text("red, green")
        (self.root / ".hidden").write_text("x")
        (self.root / "logo.png").write_bytes(b"\x89PNG\x00\x00")
        secret = self.home / "secret.txt"
        secret.write_text("private")
        os.symlink(secret, self.root / "escape.md")
        self.skill = _skill("tdd", self.root)

    def test_detail_reads_frontmatter_skill_md_and_tree(self):
        d = page.detail(self.skill, self.home)
        self.assertFalse(d["editable"])
        self.assertEqual(d["skill_md"].splitlines()[-1], "# TDD <b>")
        self.assertEqual(d["frontmatter"][1], {"key": "description", "value": "Test first"})
        self.assertEqual([s["kind"] for s in d["tree"]], ["other"])
        names = [e["name"] for e in d["tree"][0]["entries"]]
        self.assertEqual(names, ["SKILL.md", "escape.md", "logo.png", "references/loop.md"])

    def test_sections_for_plugin_shaped_folders(self):
        (self.root / "skills" / "inner").mkdir(parents=True)
        (self.root / "skills" / "inner" / "SKILL.md").write_text("x")
        (self.root / "commands").mkdir()
        (self.root / "commands" / "go.md").write_text("x")
        kinds = {s["kind"]: s for s in page.tree(self.root)}
        self.assertEqual(list(kinds), ["skills", "commands", "other"])
        inner = kinds["skills"]["entries"][0]
        self.assertTrue(inner["folder"])
        self.assertEqual(inner["preview"], "skills/inner/SKILL.md")
        self.assertEqual(inner["children"], [{"name": "SKILL.md", "path": "skills/inner/SKILL.md"}])

    def test_editable_only_under_quiver_outside_cache(self):
        self.assertTrue(page.editable(str(self.home / ".quiver" / "skills" / "x"), self.home))
        self.assertFalse(page.editable(str(self.home / ".quiver" / "cache" / "x"), self.home))
        self.assertFalse(page.editable(str(self.root), self.home))

    def test_file_view_stays_inside_the_folder(self):
        self.assertEqual(page.file_view(self.skill, "references/loop.md")["text"], "red, green")
        self.assertIsNone(page.file_view(self.skill, "logo.png")["text"])
        for name in ("escape.md", "../../../secret.txt"):
            view = page.file_view(self.skill, name)
            self.assertTrue(view["outside"], name)
            self.assertIsNone(view["text"])
        with self.assertRaises(PageError) as bad:
            page.file_view(self.skill, "/etc/passwd")
        self.assertEqual(bad.exception.status, 400)
        with self.assertRaises(PageError) as missing:
            page.file_view(self.skill, "nope.md")
        self.assertEqual(missing.exception.status, 404)

    def test_get_dispatches_by_args(self):
        inventory = {"shared_dir": "/s", "harnesses": [{"name": "codex"}], "skills": [self.skill]}
        ctx = SimpleNamespace(home=self.home, machine={"name": "box"})
        with patch.object(page, "_inventory", return_value=inventory):
            d = page.get(ctx, {"skill": str(self.root)})
            self.assertEqual((d["machine"], d["harnesses"]), ("box", [{"name": "codex"}]))
            f = page.get(ctx, {"skill": str(self.root), "file": "SKILL.md"})
            self.assertIn("# TDD", f["text"])
            with self.assertRaises(PageError) as unknown:
                page.get(ctx, {"skill": str(self.home)})
            self.assertEqual(unknown.exception.status, 404)


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.inventory = {
            "shared_dir": str(self.srv.home / ".quiver" / "skills"),
            "harnesses": [{"name": "claude", "label": "claude", "path": "/c", "state": "linked",
                           "detail": "", "archived": False}],
            "skills": [_skill("lint", "/p/lint", source="plugin", harnesses=["claude"]),
                       _skill("tdd", "/h/tdd")],
        }
        patcher = patch("quiver.skills.inventory.installed_skills", return_value=self.inventory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_needs_a_device_and_lists_standalone_skills(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/skills")[0], 401)
        with patch("quiver.plugins.catalog.build_catalog", return_value=_catalog("/p")):
            status, data = self.srv.request("GET", "/v1/app/skills", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual([s["name"] for s in body["skills"]], ["tdd"])
        self.assertEqual(body["plugin_skill_count"], 1)
        self.assertEqual(body["harnesses"][0]["state"], "linked")
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_unknown_skill_is_a_404(self):
        status, data = self.srv.request("GET", "/v1/app/skills?skill=" + quote("/etc"), headers=self.auth())
        self.assertEqual(status, 404, data)

    def test_page_assets_are_bundled(self):
        status, data = self.srv.request("GET", "/app/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b'registerPage("skills"', data)
        self.assertIn(b'registerPage("agents"', data)
        self.assertIn(b".sk-row", self.srv.request("GET", "/app/pages.css")[1])


if __name__ == "__main__":
    unittest.main()
