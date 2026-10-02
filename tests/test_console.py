"""ANSI-aware word wrapping.

The pager used to cut any line carrying colour, because wrapping one with
textwrap counts escape sequences as characters and can slice one in half.
These tests pin the two properties that let a coloured line wrap instead:
every line fits the width in *visible* characters, and a break inside a
styled run closes the run and re-opens it on the next line.
"""

import textwrap
import unittest

from quiver.console import (
    COLORS, c, cell_len, cellpad, fill_ansi, lpad, sanitize, strip_ansi,
    visible_len, wrap_ansi,
)

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
CYAN = "\x1b[36m"


class PlainWrapTest(unittest.TestCase):
    """With no escapes in play, the wrapper is textwrap."""

    CASES = (
        ("the quick brown fox jumps over the lazy dog", 12),
        ("the quick brown fox jumps over the lazy dog", 7),
        ("one  two   three", 9),
        ("    indented start of a paragraph that runs on", 14),
        ("short", 40),
        ("a b c d e f g h i j k", 5),
    )

    def test_matches_textwrap_on_visible_text(self):
        for text, width in self.CASES:
            with self.subTest(text=text, width=width):
                self.assertEqual(
                    wrap_ansi(text, width),
                    textwrap.wrap(text, width, break_on_hyphens=False),
                )

    def test_a_line_that_fits_is_returned_unchanged(self):
        self.assertEqual(wrap_ansi("no wrapping needed", 40),
                         ["no wrapping needed"])

    def test_leading_indent_survives_on_the_first_line_only(self):
        lines = wrap_ansi("    four spaces then a long sentence here", 16)
        self.assertTrue(lines[0].startswith("    "))
        for line in lines[1:]:
            self.assertFalse(line.startswith(" "), line)

    def test_a_word_longer_than_the_width_is_broken_hard(self):
        lines = wrap_ansi("supercalifragilistic", 6)
        self.assertEqual(lines, ["superc", "alifra", "gilist", "ic"])

    def test_a_long_word_fills_the_room_left_on_the_line(self):
        self.assertEqual(wrap_ansi("hi supercalifragilistic", 10),
                         ["hi superca", "lifragilis", "tic"])

    def test_blank_input_stays_one_blank_line(self):
        # A pager shows a document; a dropped blank changes its shape.
        self.assertEqual(wrap_ansi("", 20), [""])
        self.assertEqual(wrap_ansi("   ", 20), [""])
        self.assertEqual(wrap_ansi("\x1b[1m  \x1b[0m", 20), [""])

    def test_a_useless_width_returns_the_line_untouched(self):
        self.assertEqual(wrap_ansi("anything at all", 0), ["anything at all"])
        self.assertEqual(wrap_ansi("anything at all", -3), ["anything at all"])


class AnsiWrapTest(unittest.TestCase):
    def test_every_line_fits_the_width_in_visible_characters(self):
        text = c("bold", c("cyan", "a styled sentence")) + " then plain words"
        for width in range(1, 30):
            with self.subTest(width=width):
                for line in wrap_ansi(text, width):
                    self.assertLessEqual(visible_len(line), width, repr(line))

    def test_escapes_cost_nothing_so_colour_does_not_shorten_a_line(self):
        plain = "the quick brown fox jumps"
        styled = "the quick " + c("green", "brown") + " fox jumps"
        self.assertEqual([strip_ansi(line) for line in wrap_ansi(styled, 12)],
                         wrap_ansi(plain, 12))

    def test_an_escape_in_the_middle_of_a_line_is_never_split(self):
        text = "aaa " + CYAN + "bbb" + RESET + " ccc ddd eee"
        for width in range(1, 20):
            with self.subTest(width=width):
                for line in wrap_ansi(text, width):
                    # A halved escape leaves a bare ESC in the visible text.
                    self.assertNotIn("\x1b", strip_ansi(line), repr(line))

    def test_a_line_that_already_fits_comes_back_byte_identical(self):
        # The escape closing a code span sits mid-word, before the comma
        # that follows it. Deferring it to the end of the word painted the
        # punctuation as if it were part of the span.
        text = "dropped in " + CYAN + "auth.py" + RESET + ", so every fork"
        self.assertEqual(wrap_ansi(text, 60), [text])

    def test_an_escape_inside_a_word_keeps_its_place(self):
        line = wrap_ansi("a " + CYAN + "auth.py" + RESET + ", so on", 40)[0]
        self.assertIn(CYAN + "auth.py" + RESET + ",", line)

    def test_a_break_inside_a_styled_run_re_opens_it_on_the_next_line(self):
        lines = wrap_ansi(CYAN + "hello there" + RESET, 6)
        self.assertEqual([strip_ansi(line) for line in lines], ["hello", "there"])
        self.assertTrue(lines[1].startswith(CYAN), repr(lines[1]))

    def test_a_broken_line_ends_with_a_reset_so_colour_cannot_bleed(self):
        lines = wrap_ansi(CYAN + "hello there world" + RESET, 6)
        self.assertTrue(lines[0].endswith(RESET), repr(lines[0]))
        self.assertTrue(lines[1].endswith(RESET), repr(lines[1]))

    def test_nested_bold_and_colour_are_both_re_opened(self):
        lines = wrap_ansi(BOLD + CYAN + "hello there" + RESET, 6)
        self.assertTrue(lines[1].startswith(BOLD + CYAN), repr(lines[1]))

    def test_a_run_that_ended_before_the_break_is_not_re_opened(self):
        lines = wrap_ansi(CYAN + "hello" + RESET + " there world", 6)
        self.assertNotIn("\x1b", lines[1], repr(lines[1]))

    def test_a_style_that_opens_after_the_break_is_carried_across(self):
        # The escape sits in the whitespace the break eats; the colour it
        # turns on still has to reach the word that follows.
        lines = wrap_ansi("hello " + CYAN + " there world", 6)
        self.assertEqual(strip_ansi(lines[1]), "there")
        self.assertIn(CYAN, lines[1])

    def test_a_styled_word_longer_than_the_width_keeps_its_style(self):
        lines = wrap_ansi(BOLD + "supercalifragilistic" + RESET, 6)
        self.assertEqual([strip_ansi(line) for line in lines],
                         ["superc", "alifra", "gilist", "ic"])
        for line in lines:
            self.assertTrue(line.startswith(BOLD), repr(line))
            self.assertTrue(line.endswith(RESET), repr(line))

    def test_no_visible_text_is_lost(self):
        text = c("bold", "one two") + " three " + c("dim", "four five")
        joined = "".join(strip_ansi(line) for line in wrap_ansi(text, 7))
        self.assertEqual(joined.replace(" ", ""),
                         strip_ansi(text).replace(" ", ""))



class FillAnsiTest(unittest.TestCase):
    """Padding a styled line has to land inside the style, not after it."""

    def test_padding_goes_before_the_reset_so_a_background_covers_it(self):
        filled = fill_ansi(c("user_bg", "hi"), 10)
        self.assertEqual(10, visible_len(filled))
        self.assertTrue(filled.endswith(COLORS["reset"]))
        # lpad puts the spaces outside the run, which leaves the row bare
        # past the last word; that is the bug this exists to avoid.
        self.assertNotEqual(strip_ansi(lpad(c("user_bg", "hi"), 10)), "")
        self.assertIn(" " * 8 + COLORS["reset"], filled)

    def test_a_plain_line_is_padded_on_the_end(self):
        self.assertEqual("hi        ", fill_ansi("hi", 10))

    def test_a_line_already_at_or_over_the_width_is_untouched(self):
        self.assertEqual("abcdefghij", fill_ansi("abcdefghij", 10))
        self.assertEqual("abcdefghijk", fill_ansi("abcdefghijk", 10))

    def test_an_empty_styled_line_becomes_a_full_bar(self):
        bar = fill_ansi(c("user_bg", ""), 6)
        self.assertEqual(6, visible_len(bar))
        self.assertEqual(" " * 6, strip_ansi(bar))

    def test_it_survives_a_wrap(self):
        # wrap_ansi re-opens the run on each continuation line, so every row
        # can be filled independently and still close its own style.
        rows = wrap_ansi(c("user_bg", "one two three four five"), 9)
        for row in rows:
            self.assertEqual(9, visible_len(fill_ansi(row, 9)))


class CellWidthTest(unittest.TestCase):
    """cell_len counts terminal cells: CJK two, combining marks zero."""

    def test_wide_glyphs_count_as_two_cells(self):
        self.assertEqual(cell_len("a漢字b"), 6)
        self.assertEqual(cell_len("✨"), 2)

    def test_combining_marks_count_as_zero(self):
        # e + combining acute (decomposed) is one visible glyph.
        self.assertEqual(cell_len("café"), 4)
        self.assertEqual(len("café"), 5)

    def test_ansi_codes_do_not_widen(self):
        self.assertEqual(cell_len(c("red", "hi")), 2)

    def test_cellpad_pads_by_cells_not_characters(self):
        padded = cellpad("漢", 3)
        self.assertEqual(cell_len(padded), 3)
        self.assertEqual(padded, "漢 ")
        self.assertEqual(cellpad("abc", 2), "abc")

    def test_joined_emoji_measure_as_one_glyph(self):
        # Each of these renders as a single two-cell glyph:
        # ZWJ chain, skin tone, RI flag pair, VS16 promotion, tag flag.
        self.assertEqual(cell_len("\U0001f468‍\U0001f469‍\U0001f467"), 2)
        self.assertEqual(cell_len("\U0001f44b\U0001f3fd"), 2)
        self.assertEqual(cell_len("\U0001f1fa\U0001f1f8"), 2)
        self.assertEqual(cell_len("\U0001f1eb\U0001f1f7\U0001f1e9\U0001f1ea"), 4)
        self.assertEqual(cell_len("a❤️"), 3)
        self.assertEqual(cell_len("a️"), 1)
        # Keycap: digit + VS16 + U+20E3 — the mark is not a combining
        # char, so it lands in the width-1 branch and the pair totals 2.
        self.assertEqual(cell_len("1️⃣"), 2)
        self.assertEqual(
            cell_len("\U0001f3f4\U000e0067\U000e0062\U000e0065"
                     "\U000e006e\U000e0067\U000e007f"), 2)


class SanitizeTest(unittest.TestCase):
    """sanitize() makes an untrusted string terminal-safe.

    Titles, transcript lines, filenames and server names all originate
    outside quiver, so a crafted value must not be able to paint itself,
    open a link, rewrite the clipboard, or move the cursor.
    """

    def test_osc8_links_are_removed(self):
        text = "click \x1b]8;;https://evil.example\x07here\x1b]8;;\x07 ok"
        self.assertEqual(sanitize(text), "click here ok")

    def test_osc52_clipboard_writes_are_removed(self):
        text = "name\x1b]52;c;AAAAAAAA\x07tail"
        self.assertEqual(sanitize(text), "nametail")

    def test_st_terminated_osc_is_removed(self):
        text = "a\x1b]0;owned\x1b\\b"
        self.assertEqual(sanitize(text), "ab")

    def test_unterminated_osc_consumes_to_end(self):
        text = "a\x1b]8;;https://evil.example never closed"
        self.assertEqual(sanitize(text), "a")

    def test_csi_sequences_are_removed(self):
        # SGR, cursor moves, private mode sets, intermediate-byte finals.
        self.assertEqual(sanitize("a\x1b[31mb\x1b[2Jc\x1b[?25ld\x1b[ qe"),
                         "abcde")

    def test_charset_and_two_byte_escapes_are_removed(self):
        self.assertEqual(sanitize("a\x1b(Bb\x1b#8c\x1bMd"), "abcd")

    def test_a_lone_escape_is_removed(self):
        self.assertEqual(sanitize("a\x1bb"), "ab")

    def test_control_characters_become_spaces(self):
        # A newline in a title used to fuse two rows into one.
        self.assertEqual(sanitize("a\rb\nc\td\x07e"), "a b c d e")
        self.assertEqual(sanitize("x\x00y\x9bz"), "x y z")

    def test_sanitize_document_keeps_layout_and_spaces_controls(self):
        from quiver.console import sanitize_document

        self.assertEqual(sanitize_document("a\nb\tc"), "a\nb\tc")
        # A control between words becomes a space, not a fusion.
        self.assertEqual(sanitize_document("warning\rerror"), "warning error")
        self.assertEqual(sanitize_document("a\x07b\nc"), "a b\nc")
        self.assertEqual(sanitize_document("x\x1b]8;;u\x07y"), "xy")

    def test_intentional_paint_still_round_trips(self):
        # sanitize is for foreign text; our own strings stay untouched
        # when they carry no escapes of their own.
        self.assertEqual(sanitize("plain label ✓ 12"), "plain label ✓ 12")

    def test_strip_ansi_still_strips_only_escapes(self):
        # strip_ansi is a measuring tool for our own painted strings: it
        # removes every escape class but does not touch control chars.
        text = "a\x1b]8;;u\x07b\x07"
        self.assertEqual(strip_ansi(text), "ab\x07")
        self.assertEqual(visible_len("x\x1b]8;;u\x07y"), 2)


if __name__ == "__main__":
    unittest.main()
