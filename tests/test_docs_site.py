"""Tests for scripts/docs_site.py (the Zensical staging script)."""

import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "docs_site.py"
docs_site = None
if SCRIPT.exists():
    _SPEC = importlib.util.spec_from_file_location("docs_site", SCRIPT)
    docs_site = importlib.util.module_from_spec(_SPEC)
    sys.modules.setdefault("docs_site", docs_site)
    _SPEC.loader.exec_module(docs_site)

SCRIPT_MISSING = "scripts/ is not part of this source tree (Nix package build)"


def _repo(test: unittest.TestCase, files: dict) -> Path:
    """Write ``files`` (repo-relative path -> text) into a fake repo root."""
    root = Path(tempfile.mkdtemp(prefix="quiver-docs-test-"))
    test.addCleanup(shutil.rmtree, root, True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@unittest.skipUnless(SCRIPT.exists(), SCRIPT_MISSING)
class RewriteLinksTests(unittest.TestCase):
    def setUp(self):
        self.staged = {"README.md": "index.md", "docs/data-model.md": "architecture/data-model.md"}
        self.missing = []
        self.assets = set()

    def rewrite(self, text, src="README.md", root=None):
        root = root or Path("/nonexistent-repo")
        return docs_site.rewrite_links(
            text, src, dict(self.staged), root, self.missing, self.assets)

    def test_staged_page_from_repo_root(self):
        out = self.rewrite("See the [data model](docs/data-model.md#files).")
        self.assertIn("[data model](architecture/data-model.md#files)", out)
        self.assertEqual(self.missing, [])

    def test_staged_page_relative_up(self):
        # docs/page.md linking ../ARCHITECTURE.md resolves to a staged page.
        staged = dict(self.staged, **{
            "ARCHITECTURE.md": "architecture/index.md",
            "docs/backend-architecture.md": "architecture/backend-architecture.md"})
        missing, assets = [], set()
        out = docs_site.rewrite_links(
            "See [arch](../ARCHITECTURE.md#layers).",
            "docs/backend-architecture.md", staged, Path("/nope"), missing, assets)
        self.assertIn("[arch](index.md#layers)", out)
        self.assertEqual(missing, [])

    def test_repo_file_links_to_github_blob(self):
        root = _repo(self, {"src/quiver/cli.py": "x"})
        out = self.rewrite("See [the CLI](src/quiver/cli.py).", root=root)
        self.assertIn(
            "(https://github.com/c-wenlong/quiver/blob/main/src/quiver/cli.py)", out)
        self.assertEqual(self.missing, [])

    def test_repo_directory_links_to_github_tree(self):
        root = _repo(self, {"src/quiver/cli.py": "x"})
        out = self.rewrite("Browse [src](src/quiver).", root=root)
        self.assertIn(
            "(https://github.com/c-wenlong/quiver/tree/main/src/quiver)", out)

    def test_local_asset_is_copied_and_rewritten(self):
        root = _repo(self, {"assets/banner.png": "png-bytes"})
        out = self.rewrite("![mascot](assets/banner.png)", root=root)
        self.assertIn("![mascot](assets/banner.png)", out)
        self.assertIn("assets/banner.png", self.assets)

    def test_external_and_special_links_untouched(self):
        text = ("[site](https://example.com/x.md) [mail](mailto:a@b.c) "
                "[here](#anchor) [rel](//cdn.example.com/x)")
        self.assertEqual(self.rewrite(text), text)
        self.assertEqual(self.missing, [])

    def test_fenced_code_and_inline_code_untouched(self):
        text = ("```\n[bad](docs/missing.md)\n```\n"
                "inline `[also bad](docs/missing.md)` code\n"
                "but [good](docs/data-model.md) is rewritten")
        out = self.rewrite(text)
        self.assertIn("[bad](docs/missing.md)", out)
        self.assertIn("`[also bad](docs/missing.md)`", out)
        self.assertIn("[good](architecture/data-model.md)", out)
        self.assertEqual(self.missing, [])

    def test_missing_target_is_an_error(self):
        out = self.rewrite("[gone](docs/nope.md)")
        self.assertIn("[gone](docs/nope.md)", out)  # left as-is
        self.assertEqual(self.missing, ["README.md: docs/nope.md"])

    def test_reference_style_definitions_rewrite(self):
        out = self.rewrite("text [x][m]\n\n[m]: docs/data-model.md\n")
        self.assertIn("[m]: architecture/data-model.md", out)


@unittest.skipUnless(SCRIPT.exists(), SCRIPT_MISSING)
class StageTests(unittest.TestCase):
    def test_stage_copies_pages_assets_and_rewrites(self):
        root = _repo(self, {
            "README.md": "hi [arch](ARCHITECTURE.md) ![b](assets/b.png)",
            "ARCHITECTURE.md": "# arch\nlink to [cli](src/x.py)",
            "src/x.py": "x",
            "assets/b.png": "png",
            "app/landing-page/index.html": "<h1>landing</h1>",
            "app/landing-page/mascot.jpg": "jpg",
            "app/landing-page/media/sessions.mp4": "mp4",
            "app/landing-page/vercel.json": "{}",
        })
        pages = [("README.md", "index.md"), ("ARCHITECTURE.md", "architecture/index.md")]
        stage_dir = root / "build" / "docs-site"
        orig = (docs_site.PAGES, docs_site.ASSETS, docs_site.cli_page)
        try:
            docs_site.PAGES = pages
            docs_site.ASSETS = []
            docs_site.cli_page = lambda _root: "# CLI reference\n"
            code = docs_site.stage(root=root, stage_dir=stage_dir)
        finally:
            docs_site.PAGES, docs_site.ASSETS, docs_site.cli_page = orig
        self.assertEqual(code, 0)
        index = (stage_dir / "index.md").read_text()
        self.assertIn("[arch](architecture/index.md)", index)
        self.assertTrue((stage_dir / "assets/b.png").is_file())
        arch = (stage_dir / "architecture/index.md").read_text()
        self.assertIn("(https://github.com/c-wenlong/quiver/blob/main/src/x.py)", arch)
        self.assertEqual((stage_dir / "index.html").read_text(), "<h1>landing</h1>")
        self.assertTrue((stage_dir / "mascot.jpg").is_file())
        self.assertTrue((stage_dir / "media/sessions.mp4").is_file())
        self.assertFalse((stage_dir / "vercel.json").exists())

    def test_stage_fails_without_landing_page(self):
        root = _repo(self, {"README.md": "hi"})
        orig = (docs_site.PAGES, docs_site.ASSETS, docs_site.cli_page)
        try:
            docs_site.PAGES = [("README.md", "overview.md")]
            docs_site.ASSETS = []
            docs_site.cli_page = lambda _root: "# CLI reference\n"
            code = docs_site.stage(root=root, stage_dir=root / "out")
        finally:
            docs_site.PAGES, docs_site.ASSETS, docs_site.cli_page = orig
        self.assertEqual(code, 1)

    def test_stage_fails_on_missing_target(self):
        root = _repo(self, {"README.md": "[gone](NOPE.md)", "ARCHITECTURE.md": "x"})
        orig = (docs_site.PAGES, docs_site.ASSETS, docs_site.cli_page)
        try:
            docs_site.PAGES = [("README.md", "index.md"),
                               ("ARCHITECTURE.md", "architecture/index.md")]
            docs_site.ASSETS = []
            docs_site.cli_page = lambda _root: "# CLI reference\n"
            code = docs_site.stage(root=root, stage_dir=root / "out")
        finally:
            docs_site.PAGES, docs_site.ASSETS, docs_site.cli_page = orig
        self.assertEqual(code, 1)


@unittest.skipUnless(SCRIPT.exists(), SCRIPT_MISSING)
class CliPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = docs_site.cli_page(ROOT)

    def test_every_help_topic_appears(self):
        sys.path.insert(0, str(ROOT / "src"))
        import quiver.help_text as ht
        for _category, commands in ht.COMMAND_CATEGORIES:
            for primary, _alias in commands:
                self.assertIn(f"swe {primary}", self.page, primary)
        for topic in ht.HELP:
            self.assertIn(f"swe {topic}", self.page, topic)

    def test_no_ansi_escapes(self):
        self.assertNotIn("\x1b[", self.page)

    def test_generated_notice(self):
        self.assertIn("do not edit", self.page)


if __name__ == "__main__":
    unittest.main()
