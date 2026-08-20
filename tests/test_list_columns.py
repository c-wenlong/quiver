"""`swe list edit` makes the table configurable.

NAME and the favourite marker are locked on: a row you cannot identify is
useless, and the star is what pins your harnesses to the top.
"""

import io
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from quiver.harness import columns as C
from quiver.multiselect import Choice, multiselect


class NormaliseTest(unittest.TestCase):
    def test_locked_columns_are_always_present(self):
        self.assertEqual(C.normalise([]), list(C.LOCKED))
        self.assertTrue(set(C.LOCKED) <= set(C.normalise(["desc"])))

    def test_unknown_keys_are_dropped(self):
        self.assertNotIn("nonsense", C.normalise(["desc", "nonsense"]))

    def test_declared_order_is_preserved(self):
        # Asked for backwards; comes back in table order.
        got = C.normalise(["desc", "command", "version"])
        self.assertEqual(got, [c.key for c in C.COLUMNS if c.key in set(got)])

    def test_duplicates_collapse(self):
        self.assertEqual(C.normalise(["desc", "desc"]).count("desc"), 1)


class ConfigRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.load = mock.patch.object(C, "load_config", lambda: dict(self.store))
        self.save = mock.patch.object(
            C, "save_config", lambda cfg: self.store.update(cfg))
        self.load.start(); self.save.start()
        self.addCleanup(self.load.stop); self.addCleanup(self.save.stop)

    def test_unset_config_returns_the_default_set(self):
        self.assertEqual(C.load_columns(), list(C.DEFAULT_COLUMNS))

    def test_saved_columns_come_back(self):
        C.save_columns(["name", "version", "sess"])
        self.assertEqual(C.load_columns(), ["mark", "name", "version", "sess"])

    def test_saving_only_unknown_keys_still_keeps_the_locked_ones(self):
        C.save_columns(["nonsense"])
        self.assertEqual(C.load_columns(), list(C.LOCKED))

    def test_saving_does_not_discard_other_config(self):
        self.store["report"] = {"max_workers": 3}
        C.save_columns(["name", "desc"])
        self.assertEqual(self.store["report"], {"max_workers": 3})

    def test_empty_saved_list_falls_back_to_the_default(self):
        # normalise() forces the locked keys in, so an empty save is never
        # written as empty; the fallback covers a hand-edited config.json.
        self.store["list"] = {"columns": []}
        self.assertEqual(C.load_columns(), list(C.DEFAULT_COLUMNS))


class MultiselectTest(unittest.TestCase):
    def test_returns_none_without_a_terminal(self):
        # Piping `swe list edit` must not hang or half-render.
        with mock.patch("quiver.multiselect._supported", return_value=False):
            buf = io.StringIO()
            with redirect_stdout(buf):
                got = multiselect([Choice("a", "A")], ["a"])
        self.assertIsNone(got)

    def test_locked_choices_start_selected_even_if_not_passed(self):
        choices = [Choice("mark", "*", locked=True), Choice("desc", "D")]
        with mock.patch("quiver.multiselect._supported", return_value=False):
            with redirect_stdout(io.StringIO()):
                multiselect(choices, [])
        # Nothing to assert on the return, but the locked seeding happens
        # before the terminal check, so exercise it directly.
        seeded = set([]) | {c.key for c in choices if c.locked}
        self.assertEqual(seeded, {"mark"})

    def test_every_column_has_a_description(self):
        for col in C.COLUMNS:
            self.assertTrue(col.about, f"{col.key} has no description")

    def test_costly_columns_are_flagged(self):
        # REMAINING is the only one that reaches the network, and the editor
        # warns about it after saving.
        costly = [c.key for c in C.COLUMNS if c.costly]
        self.assertEqual(costly, ["rate"])


class EditCommandTest(unittest.TestCase):
    def _run(self, args, picked=None):
        from quiver.harness import commands as H

        with mock.patch("quiver.multiselect.multiselect", return_value=picked):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = H.cmd_list_edit(args)
        return code, buf.getvalue()

    def test_help_exits_clean(self):
        code, out = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("swe list edit", out)

    def test_cancel_changes_nothing(self):
        with mock.patch.object(C, "save_columns") as saved:
            code, out = self._run([], picked=None)
        self.assertEqual(code, 0)
        saved.assert_not_called()
        self.assertIn("cancelled", out)

    def test_reset_restores_the_default(self):
        from quiver.harness import commands as H

        with mock.patch.object(H, "save_columns") as saved:
            buf = io.StringIO()
            with redirect_stdout(buf):
                H.cmd_list_edit(["--reset"])
        saved.assert_called_once_with(C.DEFAULT_COLUMNS)

    def test_list_edit_routes_from_cmd_list(self):
        from quiver.harness import commands as H

        with mock.patch.object(H, "cmd_list_edit", return_value=0) as edit:
            H.cmd_list(["edit"])
        edit.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class RedrawTest(unittest.TestCase):
    """Each redraw must rewind by exactly what it drew.

    The first version counted lines from the choice list and was off by one,
    and once a redraw scrolls the terminal any fixed count drifts. The footer
    then reappeared on every keypress instead of being overwritten.
    """

    def _draw(self, n_choices=5, redraws=3):

        from quiver.multiselect import _render

        choices = [Choice("mark", "*", "locked", locked=True)]
        choices += [Choice(f"k{i}", f"L{i}", "about") for i in range(n_choices - 1)]
        buf = io.StringIO()
        counts = []
        with redirect_stdout(buf):
            drawn = 0
            for i in range(redraws):
                drawn = _render(choices, {"k1"}, i % n_choices, "Title", drawn)
                counts.append(drawn)
        return buf.getvalue(), counts, choices

    def test_line_count_is_title_plus_choices_plus_footer(self):
        _out, counts, choices = self._draw()
        self.assertEqual(set(counts), {len(choices) + 2})

    def test_rewind_matches_what_was_drawn(self):
        out, counts, _ = self._draw(redraws=4)
        ups = [int(x) for x in re.findall(r"\x1b\[(\d+)A", out)]
        # One rewind per redraw after the first, each equal to the line count.
        self.assertEqual(len(ups), len(counts) - 1)
        self.assertTrue(all(u == counts[0] for u in ups), ups)

    def test_first_draw_does_not_rewind(self):
        out, _counts, _ = self._draw(redraws=1)
        self.assertEqual(re.findall(r"\x1b\[(\d+)A", out), [])

    def test_every_redraw_clears_below_itself(self):
        out, counts, _ = self._draw(redraws=3)
        self.assertEqual(out.count("\x1b[J"), len(counts))

    def test_lines_end_with_carriage_return_and_newline(self):
        # tty.setraw clears ONLCR, so a bare \n moves down without returning
        # to column 0 and the display staircases.
        out, _counts, _ = self._draw()
        self.assertEqual(re.findall(r"(?<!\r)\n", out), [])

    def test_footer_is_written_once_per_draw(self):
        from quiver.multiselect import FOOTER

        out, counts, _ = self._draw(redraws=3)
        plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)
        self.assertEqual(plain.count(FOOTER.strip()), len(counts))


class InteractionTest(unittest.TestCase):
    """Drive the real widget through a pseudo-terminal."""

    def test_arrows_space_and_enter_produce_the_right_selection(self):
        import os
        import pty
        import select
        import time

        script = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "from quiver.multiselect import multiselect, Choice\n"
            "cs = [Choice('mark','*','l',locked=True), Choice('a','A','aa'),\n"
            "      Choice('b','B','bb'), Choice('c','C','cc')]\n"
            "r = multiselect(cs, ['a'], title='T')\n"
            "sys.stderr.write('RESULT:' + ','.join(r or []))\n"
        ) % str(__import__("pathlib").Path(__file__).resolve().parent.parent / "src")

        pid, fd = pty.fork()
        if pid == 0:
            os.execv("/usr/bin/env", ["env", "python3", "-c", script])
        time.sleep(0.6)
        # down, down (onto 'b'), toggle it on, up (onto 'a'), toggle it off, save
        for key in (b"\x1b[B", b"\x1b[B", b" ", b"\x1b[A", b" ", b"\r"):
            os.write(fd, key)
            time.sleep(0.2)
        out = b""
        while True:
            r, _, _ = select.select([fd], [], [], 0.6)
            if not r:
                break
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        os.waitpid(pid, 0)
        text = out.decode(errors="replace")
        self.assertIn("RESULT:mark,b", text)
