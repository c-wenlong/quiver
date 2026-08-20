"""`swe find -i`: browse a resource instead of printing it.

The printed listing tells you a plugin has 11 skills and then gives you
no way to see which. Nothing else in quiver answers that either, which is
the gap this closes.

Below the top level a plugin is just a directory, so the browser walks
the real filesystem and only the roots differ per resource. That is why
descending into skills/, commands/ and agents/ needs no plugin-specific
code, and keeps working when a harness invents another one.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.console import strip_ansi
from quiver.find.entries import HIDE_DIRS, Entry


class EntryContractTest(unittest.TestCase):
    def test_a_grouping_row_descends_without_a_path(self):
        """Marketplaces have no directory of their own, but you still
        navigate through them."""
        e = Entry("dv", children=[Entry("cloudflare")])
        self.assertIsNone(e.path)
        self.assertTrue(e.can_descend)

    def test_a_childless_pathless_row_is_a_dead_end(self):
        self.assertFalse(Entry("gone").can_descend)

    def test_a_directory_descends(self):
        d = Path(tempfile.mkdtemp())
        self.assertTrue(Entry("d", d).can_descend)

    def test_a_file_does_not(self):
        d = Path(tempfile.mkdtemp())
        f = d / "SKILL.md"
        f.write_text("x")
        self.assertFalse(Entry("f", f).can_descend)

    def test_an_unreadable_path_does_not_raise(self):
        e = Entry("x", Path("/nope/does/not/exist"))
        self.assertFalse(e.is_dir)


class BrowserSafetyTest(unittest.TestCase):
    def test_it_returns_rather_than_hanging_without_a_terminal(self):
        """Piping `swe find plugins -i` must not block a script forever."""
        from quiver.find.browser import browse

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = browse([Entry("a")], title="T")
        self.assertEqual(code, 0)
        self.assertIn("not a terminal", strip_ansi(buf.getvalue()))

    def test_empty_roots_return_cleanly(self):
        from quiver.find.browser import browse

        with redirect_stdout(io.StringIO()):
            self.assertEqual(browse([], title="T"), 0)

    def test_noise_directories_are_hidden(self):
        """A plugin cache keeps lock and marker dirs beside real content;
        showing them buries what the reader opened the browser to find."""
        for name in (".in_use", ".git", "__pycache__", "node_modules"):
            self.assertIn(name, HIDE_DIRS)


class BrowseDispatchTest(unittest.TestCase):
    def _run(self, argv):
        from quiver.find.commands import cmd_find

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cmd_find(argv)
        return code, strip_ansi(buf.getvalue())

    def test_each_browsable_topic_reaches_the_browser(self):
        for topic in ("plugins", "skills", "amd"):
            with mock.patch("quiver.find.browser.browse", return_value=0) as m:
                code, _ = self._run([topic, "-i"])
            self.assertEqual(code, 0, topic)
            m.assert_called_once()

    def test_the_long_flag_works_too(self):
        with mock.patch("quiver.find.browser.browse", return_value=0) as m:
            self._run(["plugins", "--interactive"])
        m.assert_called_once()

    def test_scope_is_passed_through(self):
        with mock.patch("quiver.find.roots.plugins_roots",
                        return_value=[Entry("x")]) as roots, \
             mock.patch("quiver.find.browser.browse", return_value=0):
            self._run(["plugins", "-i", "--scope=all"])
        self.assertEqual(roots.call_args.kwargs.get("scope"), "all")

    def test_a_topic_with_no_tree_is_refused(self):
        """mcps has no filesystem tree to walk, and guessing another
        resource would land you somewhere you did not ask for."""
        code, out = self._run(["mcps", "-i"])
        self.assertEqual(code, 1)
        self.assertIn("Cannot browse", out)

    def test_nothing_to_browse_says_so_rather_than_opening_blank(self):
        with mock.patch("quiver.find.roots.plugins_roots", return_value=[]):
            code, out = self._run(["plugins", "-i"])
        self.assertEqual(code, 0)
        self.assertIn("nothing to browse", out)

    def test_the_flag_does_not_leak_into_the_printed_path(self):
        """-i is stripped before topic dispatch, so it can never be read
        as a scope or a filter."""
        code, out = self._run(["plugins"])
        self.assertEqual(code, 0)
        self.assertNotIn("Cannot browse", out)


class PluginRootsTest(unittest.TestCase):
    """The reason the feature exists: reach a plugin's own directory."""

    def test_plugins_are_grouped_harness_then_marketplace(self):
        from quiver.find.roots import plugins_roots

        roots = plugins_roots()
        if not roots:
            self.skipTest("no plugins installed here")
        harness = roots[0]
        self.assertTrue(harness.children, "no marketplaces under the harness")
        market = harness.children[0]
        self.assertTrue(market.children, "no plugins under the marketplace")

    def test_a_plugin_carries_a_real_directory(self):
        from quiver.find.roots import plugins_roots

        found = [p for h in plugins_roots() for m in h.children
                 for p in m.children if p.path is not None]
        if not found:
            self.skipTest("no plugins installed here")
        self.assertTrue(found[0].can_descend,
                        "cannot descend into a plugin, which is the whole point")

    def test_a_plugin_whose_directory_is_gone_is_still_listed(self):
        """Dropping it would hide an install record that no longer
        matches the disk, which is worth seeing."""
        from quiver.find.roots import plugins_roots

        gone = [p for h in plugins_roots() for m in h.children
                for p in m.children if p.path is None]
        for p in gone:
            self.assertIn("no directory", p.detail)

    def test_every_adapter_survives_a_bogus_home(self):
        from quiver.find import roots

        for fn in (roots.agents_roots, roots.skills_roots, roots.plugins_roots):
            self.assertIsInstance(fn(home=Path("/nope/nowhere")), list)

    def test_an_unknown_scope_does_not_raise(self):
        from quiver.find import roots

        for fn in (roots.agents_roots, roots.skills_roots, roots.plugins_roots):
            self.assertIsInstance(fn(scope="nonsense"), list)


if __name__ == "__main__":
    unittest.main()


class ThreePaneLayoutTest(unittest.TestCase):
    """Parent, current, preview.

    Two panes lost the sense of where back would take you, so left and
    right felt like jumps rather than movement. The parent pane is only
    for orientation, which is why it is dimmed and why it gives up its
    width first when the window is tight.
    """

    def _widths(self, width, ratio):
        from quiver.find.browser import _pane_widths

        return _pane_widths(width, ratio)

    def test_the_three_panes_fit_the_row(self):
        """Assert the invariant, not the overhead arithmetic: the panes
        plus the separators between them must not exceed the window."""
        from quiver.find.browser import MIN_PANE

        separators = 7          # two " │ " plus the leading margin
        for width in (40, 80, 120, 200):
            p, c_, v = self._widths(width, (2, 3, 5))
            budget = max(3 * MIN_PANE + separators, width)
            self.assertLessEqual(p + c_ + v + separators, budget, width)

    def test_no_pane_collapses_to_nothing(self):
        from quiver.find.browser import MIN_PANE

        for width in (20, 40, 200):
            for ratio in ((2, 3, 5), (1, 1, 20), (20, 1, 1)):
                for w in self._widths(width, ratio):
                    self.assertGreaterEqual(w, MIN_PANE, (width, ratio))

    def test_a_bigger_weight_gets_a_wider_pane(self):
        narrow = self._widths(200, (1, 3, 5))[0]
        wide = self._widths(200, (6, 3, 5))[0]
        self.assertGreater(wide, narrow)

    def test_weights_survive_a_window_resize(self):
        """Relative widths, not fixed columns, so the split a reader
        chose still means the same thing in a different window."""
        small = self._widths(100, (2, 3, 5))
        large = self._widths(200, (2, 3, 5))
        self.assertLess(small[2], large[2])

    def test_a_zero_ratio_does_not_divide_by_zero(self):
        self.assertTrue(all(w > 0 for w in self._widths(120, (0, 0, 0))))

    def test_the_resize_keys_are_bound(self):
        import os

        from quiver.find.browser import _read_key

        for seq, want in ((b"[", "wider_parent"), (b"]", "narrower_parent"),
                          (b"{", "wider_preview"), (b"}", "narrower_preview")):
            r, w = os.pipe()
            os.write(w, seq)
            os.close(w)
            self.assertEqual(_read_key(r), want, seq)
            os.close(r)

    def test_the_footer_mentions_resizing(self):
        from quiver.find.browser import FOOTER

        self.assertIn("resize", FOOTER)


class PreviewTest(unittest.TestCase):
    """Arrowing onto a file shows the file, not the next folder."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_a_directory_previews_its_children(self):
        from quiver.find.browser import _preview

        (self.d / "skills").mkdir()
        (self.d / "commands").mkdir()
        lines = _preview(Entry("p", self.d), limit=20)
        self.assertTrue(any("skills" in ln for ln in lines))

    def test_a_text_file_previews_its_contents(self):
        from quiver.find.browser import _preview

        f = self.d / "SKILL.md"
        f.write_text("---\nname: wrangler\n---\n\n# Wrangler\n\nDeploy Workers.\n")
        lines = _preview(Entry("f", f), limit=20)
        self.assertTrue(any("Wrangler" in ln for ln in lines),
                        f"file contents not shown: {lines[:3]}")

    def test_an_oversized_file_is_described_not_dumped(self):
        from quiver.find.browser import MAX_PREVIEW_BYTES, _preview

        f = self.d / "big.md"
        f.write_bytes(b"x" * (MAX_PREVIEW_BYTES + 1))
        lines = _preview(Entry("f", f), limit=20)
        self.assertLess(len(lines), 20)

    def test_an_unreadable_path_does_not_raise(self):
        from quiver.find.browser import _preview

        self.assertIsInstance(_preview(Entry("x", Path("/nope/nope")), 10), list)

    def test_a_grouping_row_previews_its_children(self):
        from quiver.find.browser import _preview

        e = Entry("dv", children=[Entry("cloudflare"), Entry("blaxel")])
        lines = _preview(e, limit=10)
        self.assertTrue(any("cloudflare" in ln for ln in lines))


class SelectionBarTest(unittest.TestCase):
    """A filled bar, not coloured text.

    A foreground-only highlight is easy to lose in a pane you are not
    driving, and impossible to find at a glance across three of them.
    """

    def test_no_glyph_precedes_the_name(self):
        """The bar says which row is selected and colour says which rows
        descend, so a triangle on every line only ate the name column."""
        from quiver.console import strip_ansi
        from quiver.find.browser import _left_cell

        for active in (True, False):
            plain = strip_ansi(_left_cell(Entry("dv"), 20, active=active))
            self.assertNotIn("▸", plain)
            self.assertNotIn("·", plain)
            self.assertTrue(plain.lstrip().startswith("dv"), plain)

    def test_the_selected_row_fills_its_whole_cell(self):
        from quiver.console import visible_len
        from quiver.find.browser import _left_cell

        cell = _left_cell(Entry("x", detail="1 item"), 30, active=True)
        self.assertIn("\x1b[48;5;", cell, "no background set")
        self.assertEqual(visible_len(cell), 30, "bar does not span the cell")

    def test_an_unselected_row_has_no_bar(self):
        from quiver.find.browser import _left_cell

        self.assertNotIn("48;5;", _left_cell(Entry("x"), 30, active=False))

    def test_the_parent_uses_a_quieter_bar(self):
        """Two bars of the same colour would compete; the parent is
        context, not the pane being driven."""
        from quiver.find.browser import _left_cell

        live = _left_cell(Entry("x"), 30, active=True, muted=False)
        parent = _left_cell(Entry("x"), 30, active=True, muted=True)
        self.assertNotEqual(live, parent)
        self.assertIn("48;5;", parent)

    def test_the_bar_closes_its_escape(self):
        """An unterminated background bleeds into the rest of the row."""
        from quiver.find.browser import _left_cell

        self.assertTrue(_left_cell(Entry("x"), 20, active=True).endswith("\x1b[0m"))

    def test_a_long_label_still_fits_the_bar(self):
        from quiver.console import visible_len
        from quiver.find.browser import _left_cell

        cell = _left_cell(Entry("z" * 90, detail="d" * 40), 24, active=True)
        self.assertEqual(visible_len(cell), 24)


class ResizeTest(unittest.TestCase):
    """The browser follows the window while it is open."""

    def test_widths_are_recomputed_from_the_ratio(self):
        from quiver.find.browser import _pane_widths

        self.assertNotEqual(_pane_widths(80, (2, 3, 5)),
                            _pane_widths(160, (2, 3, 5)))

    def test_the_size_comes_from_the_terminal_not_the_environment(self):
        """shutil.get_terminal_size prefers COLUMNS, which a resize never
        updates, so a shell exporting it would pin the browser to the size
        it started at."""
        from pathlib import Path

        source = Path("src/quiver/find/browser.py").read_text()
        measure = source[source.index("def measure()"):]
        measure = measure[:measure.index("\n    heading")]
        self.assertIn("os.get_terminal_size", measure)

    def test_a_resize_handler_is_installed_and_restored(self):
        from pathlib import Path

        source = Path("src/quiver/find/browser.py").read_text()
        self.assertIn("SIGWINCH", source)
        # Restored in the finally, beside the termios reset: leaving a
        # handler installed would outlive the browser.
        tail = source[source.index("    finally:"):]
        self.assertIn("signal.signal", tail)

    def test_it_waits_on_select_rather_than_a_bare_read(self):
        """PEP 475 retries an interrupted read, so a blocking os.read
        would never notice the signal."""
        from pathlib import Path

        source = Path("src/quiver/find/browser.py").read_text()
        self.assertIn("select.select", source)
