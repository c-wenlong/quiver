"""`swe star` toggles, so `swe unstar` is gone.

The two commands did the same job from opposite directions, except
`unstar` could also remove a star whose registry entry no longer existed.
That fallback moved into the toggle, so removing the command loses
nothing.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from quiver.console import strip_ansi


class StarTogglesTest(unittest.TestCase):
    def setUp(self):
        self.stars = []
        p1 = mock.patch("quiver.harness.stars.load_stars",
                        side_effect=lambda: list(self.stars))
        p2 = mock.patch("quiver.harness.stars.save_stars",
                        side_effect=lambda s: self.stars.__init__(list(s)))
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _star(self, name):
        from quiver.harness.commands import cmd_star

        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_star([name])
        return strip_ansi(buf.getvalue())

    def test_the_command_is_no_longer_registered(self):
        from quiver.cli import COMMANDS

        self.assertNotIn("unstar", COMMANDS)

    def test_it_is_not_offered_in_completion(self):
        import quiver.completion as comp

        source = comp.__file__
        with open(source) as fh:
            self.assertNotIn('"unstar"', fh.read())

    def test_help_no_longer_advertises_it(self):
        from pathlib import Path

        text = Path("src/quiver/help_text.py").read_text()
        self.assertNotIn("swe unstar", text)

    def test_star_then_star_again_returns_to_the_start(self):
        from quiver.harness.stars import is_starred, toggle_star

        with mock.patch("quiver.harness.stars.load_stars",
                        side_effect=lambda: list(self.stars)), \
             mock.patch("quiver.harness.stars.save_stars",
                        side_effect=lambda s: self.stars.__init__(list(s))):
            self.assertTrue(toggle_star("claude"))
            self.assertTrue(is_starred("claude", self.stars))
            self.assertFalse(toggle_star("claude"))
            self.assertFalse(is_starred("claude", self.stars))

    def test_an_orphan_star_can_still_be_removed(self):
        """A star can outlive its registry entry; removing one used to
        need `swe unstar`, which fell back to the raw name."""
        from quiver.harness.commands import cmd_star

        self.stars.append("ghost-tool")
        with mock.patch("quiver.harness.commands.load_registry", return_value={}), \
             mock.patch("quiver.harness.commands.is_starred", return_value=True), \
             mock.patch("quiver.harness.commands.toggle_star", return_value=False) as tg:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_star(["ghost-tool"])
            tg.assert_called_once_with("ghost-tool")
        self.assertIn("Unstarred", strip_ansi(buf.getvalue()))

    def test_an_unknown_name_is_still_an_error(self):
        from quiver.harness.commands import cmd_star

        with mock.patch("quiver.harness.commands.load_registry", return_value={}), \
             mock.patch("quiver.harness.commands.is_starred", return_value=False):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_star(["no-such-tool"])
        self.assertIn("not found", strip_ansi(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
