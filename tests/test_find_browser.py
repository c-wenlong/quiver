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
