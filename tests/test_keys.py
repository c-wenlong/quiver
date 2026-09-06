"""The one escape-sequence reader every raw-mode widget goes through.

Driven over a real pipe wherever possible, because the bug this module
exists to kill was about how many bytes came off the wire: a reader that
took a fixed two after the Esc turned one wheel notch into a cancel plus a
handful of stray letters. A pipe reproduces that; a mocked os.read that
hands over exactly the right number of bytes cannot.

Only the two cases a pipe cannot express are patched: a bare Esc needs the
write end held open so the poll times out, and a closed stdin needs an
empty read.
"""

import os
import unittest
from unittest.mock import patch

from quiver import keys


def feed(data: bytes, letters=None, sequences=None) -> str:
    """Run read_key over ``data`` on a real pipe whose writer is closed."""
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    try:
        return keys.read_key(r, letters, sequences)
    finally:
        os.close(r)


class DefaultSequenceTest(unittest.TestCase):
    """Both spellings of every cursor key, and the navigation keys."""

    def test_normal_cursor_keys(self):
        for seq, want in ((b"\x1b[A", "up"), (b"\x1b[B", "down"),
                          (b"\x1b[C", "right"), (b"\x1b[D", "left")):
            self.assertEqual(feed(seq), want, seq)

    def test_application_cursor_keys(self):
        # DECCKM is on in plenty of terminals, and then an arrow is Esc O A.
        for seq, want in ((b"\x1bOA", "up"), (b"\x1bOB", "down"),
                          (b"\x1bOC", "right"), (b"\x1bOD", "left")):
            self.assertEqual(feed(seq), want, seq)

    def test_page_keys(self):
        self.assertEqual(feed(b"\x1b[5~"), "pageup")
        self.assertEqual(feed(b"\x1b[6~"), "pagedown")

    def test_home_and_end_in_all_three_spellings(self):
        for seq in (b"\x1b[H", b"\x1bOH", b"\x1b[1~"):
            self.assertEqual(feed(seq), "top", seq)
        for seq in (b"\x1b[F", b"\x1bOF", b"\x1b[4~"):
            self.assertEqual(feed(seq), "bottom", seq)


class DefaultLetterTest(unittest.TestCase):
    def test_the_shared_plain_bytes(self):
        for byte, want in ((b"\r", "enter"), (b"\n", "enter"),
                           (b"\x03", "cancel"), (b"q", "cancel"),
                           (b" ", "space"), (b"k", "up"), (b"j", "down")):
            self.assertEqual(feed(byte), want, byte)

    def test_digits_are_not_bound_here(self):
        """Only the session picker jumps to a row by number, so binding a
        digit in the shared map would swallow it in every other widget."""
        for byte in (b"0", b"3", b"9"):
            self.assertEqual(feed(byte), "", byte)

    def test_an_unbound_letter_is_ignored_rather_than_a_cancel(self):
        self.assertEqual(feed(b"z"), "")


class OverrideTest(unittest.TestCase):
    """The caller wins over the defaults, in both maps."""

    def test_a_caller_letter_beats_the_default(self):
        self.assertEqual(feed(b" ", letters={b" ": "next"}), "next")
        self.assertEqual(feed(b"q", letters={b"q": "quiet"}), "quiet")

    def test_a_caller_letter_can_add_a_binding(self):
        self.assertEqual(feed(b"a", letters={b"a": "all"}), "all")

    def test_a_caller_sequence_beats_the_default(self):
        arrows = {b"[C": "open", b"[D": "back"}
        self.assertEqual(feed(b"\x1b[C", sequences=arrows), "open")
        self.assertEqual(feed(b"\x1b[D", sequences=arrows), "back")

    def test_an_override_does_not_disturb_the_rest_of_the_map(self):
        arrows = {b"[C": "open"}
        self.assertEqual(feed(b"\x1b[A", sequences=arrows), "up")
        self.assertEqual(feed(b"j", letters={b"a": "all"}), "down")

    def test_the_default_maps_are_not_mutated_by_a_call(self):
        before = dict(keys.SEQUENCES), dict(keys.LETTERS)
        feed(b"\x1b[C", letters={b"z": "zap"}, sequences={b"[C": "open"})
        self.assertEqual((keys.SEQUENCES, keys.LETTERS), before)


class EscapeTest(unittest.TestCase):
    def test_a_bare_escape_with_nothing_pending_is_an_escape(self):
        # The writer stays open, so the poll finds nothing queued and times
        # out, which is the only thing that separates Esc from a sequence.
        r, w = os.pipe()
        os.write(w, b"\x1b")
        try:
            self.assertEqual(keys.read_key(r), "escape")
        finally:
            os.close(w)
            os.close(r)

    def test_escape_and_a_plain_byte_is_ignored(self):
        self.assertEqual(feed(b"\x1ba"), "")

    def test_an_unknown_sequence_is_ignored_not_a_cancel(self):
        self.assertEqual(feed(b"\x1b[1;5C"), "")      # ctrl-right
        self.assertEqual(feed(b"\x1b[200~"), "")      # bracketed paste
        self.assertEqual(feed(b"\x1b[?1;2c"), "")     # a device reply

    def test_a_sequence_that_never_ends_hits_the_cap_and_is_dropped(self):
        self.assertEqual(feed(b"\x1b[" + b"0" * 64), "")

    def test_a_sequence_cut_short_by_a_closed_stdin_is_dropped(self):
        self.assertEqual(feed(b"\x1b["), "")


class MouseTest(unittest.TestCase):
    def test_the_wheel_reads_as_one_line_up_or_down(self):
        self.assertEqual(feed(b"\x1b[<64;10;5M"), "up")
        self.assertEqual(feed(b"\x1b[<65;10;5M"), "down")

    def test_wide_coordinates_still_read(self):
        # SGR is what lifts the 223-column clamp, so the tail can be long.
        self.assertEqual(feed(b"\x1b[<65;204;118M"), "down")

    def test_clicks_and_releases_are_dropped_rather_than_acted_on(self):
        self.assertEqual(feed(b"\x1b[<0;10;5M"), "")     # left press
        self.assertEqual(feed(b"\x1b[<0;10;5m"), "")     # its release
        self.assertEqual(feed(b"\x1b[<64;10;5m"), "")    # wheel release
        self.assertEqual(feed(b"\x1b[<2;10;5M"), "")     # right press

    def test_a_click_is_dropped_even_when_the_caller_rebinds_arrows(self):
        self.assertEqual(feed(b"\x1b[<0;10;5M",
                              sequences={b"[C": "open"}), "")

    def test_the_tracking_switches_are_a_matched_pair(self):
        self.assertEqual(keys.MOUSE_ON, "\x1b[?1000h\x1b[?1006h")
        self.assertEqual(keys.MOUSE_OFF, "\x1b[?1000l\x1b[?1006l")


class ClosedStdinTest(unittest.TestCase):
    def test_an_empty_read_is_a_cancel(self):
        """A loop that kept going here would spin on an endless stream of
        empty reads rather than exiting."""
        r, w = os.pipe()
        os.close(w)
        try:
            self.assertEqual(keys.read_key(r), "cancel")
        finally:
            os.close(r)

    def test_it_is_a_cancel_however_the_read_comes_back_empty(self):
        with patch("os.read", side_effect=[b""]):
            self.assertEqual(keys.read_key(0), "cancel")


class LayeringTest(unittest.TestCase):
    def test_the_module_imports_nothing_from_the_project(self):
        """It sits at the bottom beside console and paths, so every widget
        can reach it without inventing a cycle."""
        from pathlib import Path

        source = Path("src/quiver/keys.py").read_text()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                self.assertNotIn("quiver", stripped, line)


class WidgetsShareItTest(unittest.TestCase):
    """Every raw-mode reader in the tree is a wrapper over this one."""

    def test_the_three_widgets_delegate_rather_than_hand_rolling(self):
        from quiver.find import browser
        from quiver import multiselect

        readers = [multiselect._read_key,
                   multiselect._read_state_key, browser._read_key]
        for reader in readers:
            with patch.object(keys, "read_key", return_value="sentinel") as m:
                self.assertEqual(reader(0), "sentinel", reader)
            self.assertEqual(m.call_count, 1, reader)

    def test_a_wheel_notch_is_a_move_in_every_widget(self):
        from quiver.find import browser
        from quiver import multiselect

        for reader in (multiselect._read_key,
                       multiselect._read_state_key, browser._read_key):
            r, w = os.pipe()
            os.write(w, b"\x1b[<65;10;5M")
            os.close(w)
            try:
                self.assertEqual(reader(r), "down", reader)
            finally:
                os.close(r)


if __name__ == "__main__":
    unittest.main()
