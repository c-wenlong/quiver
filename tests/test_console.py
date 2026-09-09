"""ANSI-aware word wrapping.

The pager used to cut any line carrying colour, because wrapping one with
textwrap counts escape sequences as characters and can slice one in half.
These tests pin the two properties that let a coloured line wrap instead:
every line fits the width in *visible* characters, and a break inside a
styled run closes the run and re-opens it on the next line.
"""

import textwrap
import unittest

from quiver.console import c, strip_ansi, visible_len, wrap_ansi

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


if __name__ == "__main__":
    unittest.main()
