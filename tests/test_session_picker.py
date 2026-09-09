"""Tests for the arrow-key session picker.

Everything that needs a real terminal is patched: the TTY check, termios/tty,
stdin's fileno, and the key reader (fed a canned sequence of key names). The
one thing these tests care about beyond the return value is that the terminal
is always handed back, so tcsetattr is asserted on the failure paths too.
"""

import io
import unittest
from unittest.mock import Mock, patch

from quiver.console import COLORS, c, strip_ansi, visible_len
from quiver.sessions import picker


ROWS = [f"session-{i}  claude  2h ago" for i in range(4)]
HEADER = ["ID        TOOL    WHEN", "─" * 24]


def read_key(data, ready=True):
    """Run _read_key over a canned byte stream, one os.read per byte.

    ``ready`` is what select reports after an Esc: True means the rest of a
    sequence is already queued, False means the user pressed Esc itself.
    """
    poll = ([0], [], []) if ready else ([], [], [])
    with patch("os.read", side_effect=[bytes([b]) for b in data]), \
            patch("select.select", return_value=poll):
        return picker._read_key(0)


def drive(keys, rows=None, **kwargs):
    """Run the picker over a canned key sequence.

    Returns (result, drawn_output, tcsetattr_mock).
    """
    out = io.StringIO()
    stdin = Mock()
    stdin.fileno.return_value = 0
    with patch.object(picker, "_supported", return_value=True), \
            patch("sys.stdout", out), \
            patch("sys.stdin", stdin), \
            patch("termios.tcgetattr", return_value=["saved"]), \
            patch("termios.tcsetattr") as restore, \
            patch("tty.setraw"), \
            patch.object(picker, "_read_key", side_effect=keys):
        result = picker.pick_session(rows if rows is not None else ROWS, **kwargs)
    return result, out.getvalue(), restore


class MovementTest(unittest.TestCase):
    def test_down_down_enter_selects_third_row(self):
        result, _, _ = drive(["down", "down", "enter"])
        self.assertEqual(result, 2)

    def test_enter_on_open_selects_the_most_recent(self):
        result, _, _ = drive(["enter"])
        self.assertEqual(result, 0)

    def test_up_from_the_top_wraps_to_the_last_row(self):
        result, _, _ = drive(["up", "enter"])
        self.assertEqual(result, len(ROWS) - 1)

    def test_down_from_the_bottom_wraps_to_the_top(self):
        result, _, _ = drive(["up", "down", "enter"])
        self.assertEqual(result, 0)

    def test_j_and_k_are_read_as_down_and_up(self):
        # _read_key does the mapping, so assert it there rather than in the loop.
        with patch("os.read", side_effect=[b"j"]):
            self.assertEqual(picker._read_key(0), "down")
        with patch("os.read", side_effect=[b"k"]):
            self.assertEqual(picker._read_key(0), "up")


class CancelTest(unittest.TestCase):
    def test_q_returns_none(self):
        result, _, restore = drive(["down", "cancel"])
        self.assertIsNone(result)
        self.assertTrue(restore.called)

    def test_escape_returns_none(self):
        result, _, _ = drive(["escape"])
        self.assertIsNone(result)

    def test_bare_escape_with_nothing_pending_is_a_cancel(self):
        with patch("os.read", side_effect=[b"\x1b"]), \
                patch("select.select", return_value=([], [], [])):
            self.assertEqual(picker._read_key(0), "escape")

    def test_escape_followed_by_an_arrow_is_a_move(self):
        self.assertEqual(read_key(b"\x1b[B"), "down")

    def test_closed_stdin_is_a_cancel(self):
        with patch("os.read", side_effect=[b""]):
            self.assertEqual(picker._read_key(0), "cancel")


class EscapeSequenceTest(unittest.TestCase):
    """Only a bare Esc cancels; every other sequence moves or is dropped."""

    def test_application_cursor_keys_move(self):
        # DECCKM is on in plenty of terminals, and then an arrow is Esc O A.
        self.assertEqual(read_key(b"\x1bOA"), "up")
        self.assertEqual(read_key(b"\x1bOB"), "down")

    def test_normal_cursor_keys_move(self):
        self.assertEqual(read_key(b"\x1b[A"), "up")
        self.assertEqual(read_key(b"\x1b[B"), "down")

    def test_home_and_end_jump_to_the_ends_in_all_three_spellings(self):
        for seq in (b"\x1b[H", b"\x1bOH", b"\x1b[1~"):
            self.assertEqual(read_key(seq), "top", seq)
        for seq in (b"\x1b[F", b"\x1bOF", b"\x1b[4~"):
            self.assertEqual(read_key(seq), "bottom", seq)

    def test_the_wheel_reads_as_one_line_up_or_down(self):
        self.assertEqual(read_key(b"\x1b[<64;10;5M"), "up")
        self.assertEqual(read_key(b"\x1b[<65;10;5M"), "down")

    def test_a_wheel_report_with_wide_coordinates_still_reads(self):
        # SGR is what lifts the 223-column clamp, so the tail can be long.
        self.assertEqual(read_key(b"\x1b[<65;204;118M"), "down")

    def test_clicks_and_releases_are_dropped_rather_than_acted_on(self):
        self.assertEqual(read_key(b"\x1b[<0;10;5M"), "")     # left press
        self.assertEqual(read_key(b"\x1b[<0;10;5m"), "")     # its release
        self.assertEqual(read_key(b"\x1b[<64;10;5m"), "")    # wheel release
        self.assertEqual(read_key(b"\x1b[<2;10;5M"), "")     # right press

    def test_an_unknown_sequence_is_ignored_not_a_cancel(self):
        self.assertEqual(read_key(b"\x1b[1;5C"), "")         # ctrl-right
        self.assertEqual(read_key(b"\x1b[200~"), "")         # bracketed paste
        self.assertEqual(read_key(b"\x1b[?1;2c"), "")        # a device reply

    def test_escape_and_a_plain_byte_is_ignored(self):
        self.assertEqual(read_key(b"\x1ba"), "")

    def test_a_sequence_that_never_ends_hits_the_cap_and_is_dropped(self):
        self.assertEqual(read_key(b"\x1b[" + b"0" * 64), "")

    def test_a_sequence_cut_short_by_a_closed_stdin_is_dropped(self):
        with patch("os.read", side_effect=[b"\x1b", b"[", b""]), \
                patch("select.select", return_value=([0], [], [])):
            self.assertEqual(picker._read_key(0), "")


class NoTerminalTest(unittest.TestCase):
    def test_empty_rows_returns_none_without_touching_the_terminal(self):
        out = io.StringIO()
        with patch.object(picker, "_supported") as supported, \
                patch("termios.tcgetattr") as getattr_, \
                patch("sys.stdout", out):
            self.assertIsNone(picker.pick_session([]))
        supported.assert_not_called()
        getattr_.assert_not_called()
        self.assertEqual(out.getvalue(), "")

    def test_not_a_terminal_returns_none_and_says_so(self):
        out = io.StringIO()
        with patch.object(picker, "_supported", return_value=False), \
                patch("termios.tcgetattr") as getattr_, \
                patch("sys.stdout", out):
            self.assertIsNone(picker.pick_session(ROWS))
        getattr_.assert_not_called()
        self.assertIn("not a terminal, nothing selected", strip_ansi(out.getvalue()))


class DigitJumpTest(unittest.TestCase):
    def test_digit_moves_the_cursor_to_that_one_based_row(self):
        result, _, _ = drive(["3", "enter"])
        self.assertEqual(result, 2)

    def test_digit_does_not_select_on_its_own(self):
        result, _, _ = drive(["3", "down", "enter"])
        self.assertEqual(result, 3)

    def test_digit_past_the_end_of_the_list_is_ignored(self):
        result, _, _ = drive(["9", "enter"])
        self.assertEqual(result, 0)

    def test_digits_are_read_as_themselves(self):
        with patch("os.read", side_effect=[b"3"]):
            self.assertEqual(picker._read_key(0), "3")
        with patch("os.read", side_effect=[b"0"]):
            self.assertEqual(picker._read_key(0), "")


class PreviewTest(unittest.TestCase):
    """Space opens the transcript on the alternate screen; esc comes back."""

    def setUp(self):
        self.calls = []

    def preview(self, index):
        self.calls.append(index)
        return [f"preview line A for {index}", f"preview line B for {index}"]

    def test_space_opens_the_transcript_and_escape_returns_to_the_list(self):
        result, out, _ = drive(["space", "escape", "enter"],
                               preview=self.preview, height=10)
        self.assertEqual(result, 0)
        self.assertEqual(self.calls, [0])
        self.assertIn(picker.ALT_SCREEN_ON, out)
        self.assertIn(picker.ALT_SCREEN_OFF, out)
        self.assertLess(out.index(picker.ALT_SCREEN_ON), out.index("preview line A for 0"))
        self.assertLess(out.index("preview line A for 0"), out.index(picker.ALT_SCREEN_OFF))

    def test_the_view_is_titled_with_the_highlighted_row(self):
        _, out, _ = drive(["down", "space", "escape", "enter"],
                          preview=self.preview, height=10)
        view = strip_ansi(out.split(picker.ALT_SCREEN_ON)[1])
        self.assertIn(ROWS[1].strip(), view.splitlines()[0])
        self.assertEqual(self.calls, [1])

    def test_enter_inside_the_view_resumes_that_session(self):
        result, _, _ = drive(["down", "down", "space", "enter"],
                             preview=self.preview, height=10)
        self.assertEqual(result, 2)

    def test_q_inside_the_view_only_closes_the_view(self):
        result, out, _ = drive(["space", "cancel", "down", "enter"],
                               preview=self.preview, height=10)
        self.assertEqual(result, 1)
        # The list is redrawn after the view closes.
        self.assertGreater(out.count("\x1b[J"), 2)

    def test_a_raising_preview_does_not_take_the_picker_down(self):
        def boom(index):
            raise RuntimeError("no transcript")

        result, out, _ = drive(["space", "escape", "enter"], preview=boom, height=10)
        self.assertEqual(result, 0)
        self.assertIn("(preview unavailable)", strip_ansi(out))

    def test_footer_mentions_preview_only_when_one_is_given(self):
        _, with_preview, _ = drive(["enter"], preview=self.preview, height=10)
        _, without, _ = drive(["enter"], height=10)
        self.assertIn("space preview", strip_ansi(with_preview))
        self.assertNotIn("preview", strip_ansi(without))

    def test_space_without_a_preview_does_nothing(self):
        result, out, _ = drive(["space", "enter"], height=10)
        self.assertEqual(result, 0)
        self.assertNotIn(picker.ALT_SCREEN_ON, out)

    def test_the_alternate_screen_is_left_even_if_the_view_raises(self):
        with patch.object(picker, "_view", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                drive(["space"], preview=self.preview, height=10)
        # drive() re-raises before returning output, so look at the mock
        # stdout through a second, captured run of the same failure.
        out = io.StringIO()
        stdin = Mock()
        stdin.fileno.return_value = 0
        with patch.object(picker, "_supported", return_value=True), \
                patch("sys.stdout", out), patch("sys.stdin", stdin), \
                patch("termios.tcgetattr", return_value=["saved"]), \
                patch("termios.tcsetattr") as restore, patch("tty.setraw"), \
                patch.object(picker, "_read_key", side_effect=["space"]), \
                patch.object(picker, "_view", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                picker.pick_session(ROWS, preview=self.preview, height=10)
        self.assertIn(picker.ALT_SCREEN_OFF, out.getvalue())
        self.assertTrue(restore.called)


class ViewTest(unittest.TestCase):
    """The transcript view wraps prose, cuts labels, and pages."""

    def frames(self, keys, document, size=(40, 8)):
        out = io.StringIO()
        with patch("sys.stdout", out), \
                patch("shutil.get_terminal_size", return_value=size), \
                patch.object(picker, "_read_key", side_effect=keys):
            outcome = picker._view(0, document, "title")
        return outcome, [strip_ansi(f) for f in out.getvalue().split("\x1b[2J")[1:]]

    def test_plain_lines_wrap_to_the_width_instead_of_being_cut(self):
        long = "word " * 30
        _, frames = self.frames(["escape"], [long], size=(40, 30))
        body = frames[0]
        self.assertNotIn("...", body)
        self.assertEqual(body.count("word"), 30)
        for line in body.splitlines():
            self.assertLessEqual(len(line.rstrip()), 39 + len("  1–4 of 4 ·" + picker.VIEW_FOOTER))

    def test_coloured_lines_wrap_and_keep_their_style(self):
        label = c("dim", "  ⚙ Edit: " + "x " * 40)
        wrapped = picker._wrap(label, 30)
        self.assertGreater(len(wrapped), 1)
        for line in wrapped:
            self.assertLessEqual(visible_len(line), 30)
            self.assertIn("\x1b[2m", line)          # dim re-opened on each line
            self.assertTrue(line.endswith("\x1b[0m"))
        self.assertEqual(strip_ansi(" ".join(wrapped)).split(), strip_ansi(label).split())
        self.assertEqual(picker._wrap(c("green", "ai"), 30), [c("green", "ai")])

    def test_blank_lines_survive_wrapping(self):
        self.assertEqual(picker._wrap("", 30), [""])
        self.assertEqual(picker._wrap("   ", 30), [""])

    def test_opens_at_the_end_and_pages_back_up(self):
        doc = [f"line {i:02d}" for i in range(20)]
        outcome, frames = self.frames(["pageup", "top", "bottom", "escape"], doc)
        self.assertEqual(outcome, "close")
        self.assertIn("line 19", frames[0])
        self.assertNotIn("line 00", frames[0])
        self.assertIn("line 00", frames[2])      # after "top"
        self.assertIn("line 19", frames[3])      # after "bottom"

    def test_up_and_down_scroll_one_line_and_clamp(self):
        doc = [f"line {i:02d}" for i in range(10)]
        _, frames = self.frames(["down", "up", "up", "escape"], doc, size=(40, 7))
        self.assertIn("line 09", frames[0])
        self.assertIn("line 09", frames[1])      # down at the end stays
        self.assertNotIn("line 09", frames[3])   # two ups scroll it off

    def test_enter_reports_a_pick(self):
        outcome, _ = self.frames(["enter"], ["a"])
        self.assertEqual(outcome, "enter")

    def test_page_keys_are_read_with_their_trailing_tilde(self):
        self.assertEqual(read_key(b"\x1b[6~"), "pagedown")
        self.assertEqual(read_key(b"\x1b[5~"), "pageup")

    def test_the_wheel_scrolls_the_pager_instead_of_closing_it(self):
        # A notch arrives as "up"/"down", so the pager moves a line and stays.
        doc = [f"line {i:02d}" for i in range(10)]
        outcome, frames = self.frames(["up", "down", "enter"], doc, size=(40, 7))
        self.assertIn("line 09", frames[0])
        self.assertNotIn("line 09", frames[1])   # wheel up, off the end
        self.assertIn("line 09", frames[2])      # wheel down, back to it
        self.assertEqual(outcome, "enter")

    def test_an_ignored_key_leaves_the_pager_open_where_it_was(self):
        doc = [f"line {i:02d}" for i in range(10)]
        outcome, frames = self.frames(["", "", "escape"], doc, size=(40, 7))
        self.assertEqual(outcome, "close")
        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0], frames[1])   # redrawn, unmoved


class WindowTest(unittest.TestCase):
    def test_the_window_scrolls_to_keep_the_cursor_visible(self):
        rows = [f"row-{i}" for i in range(10)]
        keys = ["down"] * 8 + ["enter"]
        result, out, _ = drive(keys, rows=rows, height=3)
        plain = strip_ansi(out)
        self.assertEqual(result, 8)
        self.assertIn("row-8", plain)
        self.assertIn("of 10", plain)

    def test_no_range_line_when_everything_fits(self):
        _, out, _ = drive(["enter"], height=10)
        self.assertNotIn("of 4", strip_ansi(out))

    def test_header_is_drawn_above_the_rows_every_frame(self):
        _, out, _ = drive(["down", "enter"], header=HEADER, height=10)
        plain = strip_ansi(out)
        self.assertEqual(plain.count("ID        TOOL    WHEN"), 2)

    def test_row_text_is_never_rewritten(self):
        _, out, _ = drive(["enter"], height=10)
        for row in ROWS:
            self.assertIn(row, out)

    def test_render_reports_the_number_of_lines_it_wrote(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            drawn = picker._render(ROWS, HEADER, 0, 0, 10)
        body = out.getvalue().split("\x1b[J", 1)[1]
        self.assertEqual(drawn, body.count("\r\n"))
        self.assertEqual(drawn, len(HEADER) + len(ROWS) + 1)


class PrefixWidthTest(unittest.TestCase):
    """The caller renders rows two columns narrower than the terminal.

    That budget only holds if every line the picker draws costs exactly two
    visible columns in front of the text it was handed, pointer included.
    """

    def _frame(self, cursor):
        out = io.StringIO()
        with patch("sys.stdout", out):
            picker._render(ROWS, HEADER, cursor, 0, 10)
        body = out.getvalue().split("\x1b[J", 1)[1]
        return [line for line in body.split("\r\n") if line]

    def test_every_line_is_indented_by_exactly_two_columns(self):
        lines = self._frame(cursor=1)
        for line, text in zip(lines, HEADER + ROWS):
            self.assertEqual(visible_len(line) - visible_len(text), 2)
            self.assertTrue(strip_ansi(line).endswith(text))

    def test_the_cursor_row_spends_its_two_columns_on_the_pointer(self):
        lines = self._frame(cursor=1)
        cursor_line = lines[len(HEADER) + 1]
        self.assertEqual(strip_ansi(cursor_line), "> " + ROWS[1])
        self.assertIn(c("cyan", ">"), cursor_line)

    def test_rows_without_the_cursor_are_indented_with_plain_spaces(self):
        lines = self._frame(cursor=1)
        other = lines[len(HEADER)]
        self.assertEqual(other, "  " + ROWS[0])

    def test_header_lines_take_the_same_indent(self):
        lines = self._frame(cursor=0)
        for line, text in zip(lines, HEADER):
            self.assertEqual(line, "  " + text)


class MouseTest(unittest.TestCase):
    """The wheel only reaches the picker while tracking is on."""

    def test_tracking_is_turned_on_before_the_first_frame(self):
        _, out, _ = drive(["enter"], height=10)
        self.assertIn(picker.MOUSE_ON, out)
        self.assertLess(out.index(picker.MOUSE_ON), out.index(ROWS[0]))

    def test_tracking_is_turned_off_before_the_terminal_is_restored(self):
        out = io.StringIO()
        stdin = Mock()
        stdin.fileno.return_value = 0
        seen = []
        with patch.object(picker, "_supported", return_value=True), \
                patch("sys.stdout", out), \
                patch("sys.stdin", stdin), \
                patch("termios.tcgetattr", return_value=["saved"]), \
                patch("termios.tcsetattr",
                      side_effect=lambda *a: seen.append(out.getvalue())), \
                patch("tty.setraw"), \
                patch.object(picker, "_read_key", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                picker.pick_session(ROWS)
        # Even on the raising path, what the terminal had already been sent by
        # the time termios came back includes the off switch.
        self.assertEqual(len(seen), 1)
        self.assertIn(picker.MOUSE_OFF, seen[0])

    def test_a_wheel_notch_moves_the_cursor_like_an_arrow(self):
        # _read_key turns the report into "down", so the loop needs no branch.
        self.assertEqual(read_key(b"\x1b[<65;1;1M"), "down")
        result, _, _ = drive(["down", "down", "enter"])
        self.assertEqual(result, 2)

    def test_an_ignored_key_neither_moves_the_cursor_nor_exits(self):
        result, _, restore = drive(["", "", "enter"])
        self.assertEqual(result, 0)
        self.assertTrue(restore.called)
        self.assertEqual(drive(["down", "", "enter"])[0], 1)


class RestoreTest(unittest.TestCase):
    def test_the_terminal_is_restored_when_the_key_reader_raises(self):
        out = io.StringIO()
        stdin = Mock()
        stdin.fileno.return_value = 0
        with patch.object(picker, "_supported", return_value=True), \
                patch("sys.stdout", out), \
                patch("sys.stdin", stdin), \
                patch("termios.tcgetattr", return_value=["saved"]), \
                patch("termios.tcsetattr") as restore, \
                patch("tty.setraw"), \
                patch.object(picker, "_read_key", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                picker.pick_session(ROWS)
        self.assertEqual(restore.call_count, 1)
        self.assertEqual(restore.call_args[0][2], ["saved"])
        self.assertIn("\x1b[?25h", out.getvalue())

    def test_the_caret_is_restored_on_a_normal_exit(self):
        _, out, restore = drive(["enter"])
        self.assertEqual(restore.call_count, 1)
        self.assertTrue(out.endswith("\x1b[?25h"))



class TranscriptWrapTest(unittest.TestCase):
    """A painted prompt has to read as a block, not a highlighted phrase."""

    WIDTH = 30

    def _wrap(self, line):
        return picker._wrap(line, self.WIDTH)

    def test_a_short_prompt_still_fills_the_row(self):
        rows = self._wrap(c("user_bg", "hi"))
        self.assertEqual(1, len(rows))
        self.assertEqual(self.WIDTH, visible_len(rows[0]))
        self.assertEqual("hi" + " " * (self.WIDTH - 2), strip_ansi(rows[0]))

    def test_every_wrapped_row_of_a_long_prompt_is_filled(self):
        rows = self._wrap(c("user_bg", "word " * 20))
        self.assertGreater(len(rows), 1)
        for row in rows:
            self.assertEqual(self.WIDTH, visible_len(row))
            self.assertTrue(row.startswith(COLORS["user_bg"]), row)

    def test_a_blank_line_inside_a_prompt_becomes_a_full_bar(self):
        # Otherwise a paragraph break would cut the slab in half.
        rows = self._wrap(c("user_bg", ""))
        self.assertEqual(1, len(rows))
        self.assertEqual(self.WIDTH, visible_len(rows[0]))
        self.assertEqual(" " * self.WIDTH, strip_ansi(rows[0]))

    def test_the_paint_closes_on_every_row(self):
        # An unclosed background would bleed down the rest of the screen.
        for row in self._wrap(c("user_bg", "word " * 20)):
            self.assertTrue(row.endswith(COLORS["reset"]), repr(row))

    def test_unpainted_lines_are_left_ragged(self):
        rows = self._wrap("an assistant sentence")
        self.assertEqual(["an assistant sentence"], rows)

    def test_a_blank_unpainted_line_stays_blank(self):
        self.assertEqual([""], self._wrap(""))

    def test_a_styled_but_unpainted_line_is_not_filled(self):
        row = self._wrap(c("cyan", "code span"))[0]
        self.assertEqual(len("code span"), visible_len(row))


if __name__ == "__main__":
    unittest.main()
