"""Tests for scripts/docs_site.py, the generator behind the
app/landing-page/{overview,cli,changelog,contributing} pages."""

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

try:
    import mistune  # noqa: F401
    HAVE_MISTUNE = True
except ImportError:
    HAVE_MISTUNE = False
MISTUNE_MISSING = "mistune is not installed (pip install -r docs/requirements.txt)"


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
        self.missing = []

    def rewrite(self, text, src="README.md", root=None):
        return docs_site.rewrite_links(text, src, root or Path("/nonexistent"),
                                       self.missing)

    def test_a_link_to_another_staged_page_becomes_relative(self):
        out = self.rewrite("see [changelog](CHANGELOG.md)")
        self.assertEqual(out, "see [changelog](../changelog/)")

    def test_a_repo_file_links_to_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "LICENSE").write_text("MIT")
            out = self.rewrite("[license](LICENSE)", root=root)
        self.assertEqual(
            out,
            "[license](https://github.com/c-wenlong/quiver/blob/main/LICENSE)")

    def test_a_repo_directory_links_to_github_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            out = self.rewrite("[docs](docs)", root=root)
        self.assertEqual(
            out,
            "[docs](https://github.com/c-wenlong/quiver/tree/main/docs)")

    def test_an_image_links_to_raw_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs" / "img").mkdir(parents=True)
            (root / "docs" / "img" / "shot.png").write_bytes(b"\x89PNG")
            out = self.rewrite(
                "![shot](../docs/img/shot.png)", src="docs/guide.md", root=root)
        self.assertEqual(
            out,
            "![shot](https://raw.githubusercontent.com/c-wenlong/quiver/main/"
            "docs/img/shot.png)")

    def test_absolute_and_anchor_links_pass_through(self):
        text = ("[x](https://example.com/a) [y](#frag) "
                "[z](mailto:a@b.c) [w](//cdn.example.com/x)")
        self.assertEqual(self.rewrite(text), text)
        self.assertEqual(self.missing, [])

    def test_links_inside_code_are_left_alone(self):
        text = "`[x](README.md)`\n\n```\n[y](CHANGELOG.md)\n```"
        self.assertEqual(self.rewrite(text), text)

    def test_a_missing_target_is_reported_and_left_alone(self):
        out = self.rewrite("[gone](no/such/file.md)", root=Path("/nonexistent"))
        self.assertEqual(out, "[gone](no/such/file.md)")
        self.assertEqual(self.missing, ["README.md: no/such/file.md"])

    def test_inline_html_targets_are_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "LICENSE").write_text("MIT")
            out = self.rewrite('<a href="LICENSE">x</a>', root=root)
        self.assertEqual(
            out,
            '<a href="https://github.com/c-wenlong/quiver/blob/main/LICENSE">'
            "x</a>")


@unittest.skipUnless(SCRIPT.exists(), SCRIPT_MISSING)
@unittest.skipUnless(HAVE_MISTUNE, MISTUNE_MISSING)
class RenderTests(unittest.TestCase):
    def test_headings_get_github_style_anchors(self):
        render = docs_site._renderer()
        html = render("## Nix\n\n### `swe list` (ls)\n\n## Nix")
        self.assertIn('id="nix"', html)
        self.assertIn('id="swe-list-ls"', html)
        self.assertIn('id="nix-1"', html)  # second heading dedupes

    def test_pages_render_the_site_chrome(self):
        render = docs_site._renderer()
        page = docs_site.render_page("changelog", render("# Hi\n\nbody"))
        for needle in ('<nav>', "../icon.png", "../overview/",
                       "quiver is MIT licensed", 'id="hi"'):
            self.assertIn(needle, page)

    def test_committed_output_is_fresh(self):
        """The generated pages under app/landing-page/ match the sources —
        fails when a Markdown source or `swe help` changed without regen."""
        pages, missing = docs_site.build_pages(ROOT)
        self.assertEqual(missing, [])
        for slug, html in pages.items():
            committed = ROOT / "app" / "landing-page" / slug / "index.html"
            self.assertTrue(committed.is_file(),
                            f"{committed} missing; run scripts/docs_site.py")
            self.assertEqual(
                committed.read_text(encoding="utf-8"), html,
                f"{committed} is stale; run scripts/docs_site.py")


if __name__ == "__main__":
    unittest.main()
