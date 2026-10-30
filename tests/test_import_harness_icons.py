"""app/mac/script/import-harness-icons.py: path rewriting and safe refusal."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "app" / "mac" / "script" / "import-harness-icons.py"
# The Nix package build copies only the Python sources, not app/.
NEEDS_APP = unittest.skipUnless(SCRIPT.exists(), "app/mac/ is not in this source tree")


def load():
    spec = importlib.util.spec_from_file_location("import_harness_icons", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@NEEDS_APP
class DropBoxSubpathTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_a_relative_move_after_a_dropped_box_keeps_its_place(self):
        d = self.mod.spell_out("M512 512H0V0H512V512Zm-400-400h100v100h-100z")
        self.assertEqual(self.mod.drop_box_subpath(d, ["0", "0", "512", "512"]),
                         "M 112 112 h 100 v 100 h -100 z")

    def test_implicit_lines_after_the_move_stay_relative(self):
        d = self.mod.spell_out("M512 512H0V0H512V512Zm-400-400 100 0 0 100-100 0z")
        self.assertEqual(self.mod.drop_box_subpath(d, ["0", "0", "512", "512"]),
                         "M 112 112 l 100 0 0 100 -100 0 z")

    def test_a_lone_box_is_kept(self):
        d = "M 0 0 H 10 V 10 H 0 Z"
        self.assertEqual(self.mod.drop_box_subpath(d, ["0", "0", "10", "10"]), d)

    def test_mono_turns_gradients_black(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
               '<path fill="url(#g)" d="M1 1h5v5z"/></svg>')
        self.assertIn('fill="#000"', self.mod.normalise(svg, mono=True))
        self.assertIn('fill="url(#g)"', self.mod.normalise(svg, mono=False))


@NEEDS_APP
class RefusalTest(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out = root / "Resources"
        self.out.mkdir()
        (self.out / "harness-old.svg").write_text("<svg/>")
        (self.out / "harness-icons.tsv").write_text("id\tkind\tsource_url\tlicence\tnotes\nold\tmono\tx\ty\t\n")
        self.mod.OUT = self.out
        (root / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><path d="M0 0h4v4z"/></svg>')
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return {p.name: p.read_text() for p in self.out.iterdir() if p.is_file()}

    def test_a_table_missing_columns_changes_nothing(self):
        before = self.snapshot()
        table = self.root / "sources.tsv"
        table.write_text("id\tkind\tfile\na\tmono\ta.svg\n")
        with self.assertRaises(SystemExit):
            self.mod.main(str(table))
        self.assertEqual(self.snapshot(), before)

    def test_an_id_that_leaves_the_folder_changes_nothing(self):
        # harness-x/../../evil.svg resolves to root/evil.svg once the
        # harness-x folder exists, so without the id check this writes.
        (self.out / "harness-x").mkdir()
        before = self.snapshot()
        table = self.root / "sources.tsv"
        table.write_text("id\tkind\tfile\tsource_url\tlicence\tnotes\n"
                         "x/../../evil\tmono\ta.svg\thttps://e\tMIT\t\n")
        with self.assertRaises(SystemExit) as raised:
            self.mod.main(str(table))
        self.assertIn("id must be", str(raised.exception))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / "evil.svg").exists())

    def test_a_good_table_replaces_the_set(self):
        table = self.root / "sources.tsv"
        table.write_text("id\tkind\tfile\tsource_url\tlicence\tnotes\na\tmono\ta.svg\thttps://e\tMIT\t\n")
        self.mod.main(str(table))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["harness-a.svg", "harness-icons.tsv"])


if __name__ == "__main__":
    unittest.main()
