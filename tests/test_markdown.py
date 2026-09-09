"""Markdown rendered for the session pager.

Two properties matter more than any individual construct. Nothing is
lost: every word of the source survives, in order, once the escapes are
stripped. And nothing is invented: a line carrying no markup comes back
byte for byte, because most of a transcript is ordinary prose.
"""

import unittest

from quiver.console import strip_ansi
from quiver.markdown import render_markdown

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
CYAN = "\x1b[36m"


def visible(text: str) -> list[str]:
    return [strip_ansi(line) for line in render_markdown(text)]


class BlockTest(unittest.TestCase):
    def test_a_heading_loses_its_hashes_and_turns_bold(self):
        line = render_markdown("## Steps")[0]
        self.assertEqual(strip_ansi(line), "Steps")
        self.assertIn(BOLD, line)

    def test_a_top_level_heading_also_takes_a_colour(self):
        line = render_markdown("# Title")[0]
        self.assertEqual(strip_ansi(line), "Title")
        self.assertIn(BOLD, line)
        self.assertIn(CYAN, line)

    def test_all_six_heading_levels_are_recognised(self):
        for level in range(1, 7):
            with self.subTest(level=level):
                text = "#" * level + " Head"
                self.assertEqual(visible(text), ["Head"])

    def test_a_hash_with_no_space_is_not_a_heading(self):
        self.assertEqual(render_markdown("#hashtag"), ["#hashtag"])

    def test_bullets_become_dots_and_keep_their_indent(self):
        source = "- one\n* two\n+ three\n  - nested"
        self.assertEqual(visible(source),
                         ["• one", "• two", "• three", "  • nested"])

    def test_numbered_items_keep_their_numbers(self):
        self.assertEqual(visible("1. first\n2) second\n  10. tenth"),
                         ["1. first", "2) second", "  10. tenth"])

    def test_a_blockquote_gets_a_bar_and_dims(self):
        line = render_markdown("> quoted words")[0]
        self.assertEqual(strip_ansi(line), "│ quoted words")
        self.assertIn(DIM, line)

    def test_a_horizontal_rule_becomes_a_rule(self):
        for source in ("---", "***", "___", "-----"):
            with self.subTest(source=source):
                line = render_markdown(source)[0]
                self.assertEqual(strip_ansi(line), "─" * 40)
                self.assertIn(DIM, line)

    def test_a_dash_list_item_is_not_mistaken_for_a_rule(self):
        self.assertEqual(visible("- a"), ["• a"])

    def test_a_table_row_joins_its_cells_and_the_separator_is_a_rule(self):
        rendered = render_markdown("| file | change |\n|------|--------|\n| a.py | guard |")
        self.assertEqual(strip_ansi(rendered[0]), "file │ change")
        self.assertEqual(strip_ansi(rendered[1]), "─" * 40)
        self.assertEqual(strip_ansi(rendered[2]), "a.py │ guard")

    def test_blank_lines_are_kept(self):
        self.assertEqual(render_markdown("a\n\n\nb"), ["a", "", "", "b"])


class FenceTest(unittest.TestCase):
    def test_a_fenced_block_drops_its_fences_and_dims_the_code(self):
        rendered = render_markdown("```python\nx = 1\n```")
        self.assertEqual([strip_ansi(line) for line in rendered], ["  x = 1"])
        self.assertIn(DIM, rendered[0])

    def test_tilde_fences_work_too(self):
        self.assertEqual(visible("~~~\ny = 2\n~~~"), ["  y = 2"])

    def test_markup_inside_a_fence_stays_literal(self):
        rendered = render_markdown("```\n**not** bold `x` # not a heading\n```")
        self.assertEqual(strip_ansi(rendered[0]),
                         "  **not** bold `x` # not a heading")

    def test_indentation_inside_a_fence_survives(self):
        self.assertEqual(visible("```\ndef f():\n    return 1\n```"),
                         ["  def f():", "      return 1"])

    def test_an_unclosed_fence_still_renders_its_body(self):
        self.assertEqual(visible("```\nx = 1"), ["  x = 1"])


class InlineTest(unittest.TestCase):
    def test_bold_in_both_spellings(self):
        for source in ("say **loud** now", "say __loud__ now"):
            with self.subTest(source=source):
                line = render_markdown(source)[0]
                self.assertEqual(strip_ansi(line), "say loud now")
                self.assertIn(BOLD, line)

    def test_italic_in_both_spellings(self):
        for source in ("say *soft* now", "say _soft_ now"):
            with self.subTest(source=source):
                line = render_markdown(source)[0]
                self.assertEqual(strip_ansi(line), "say soft now")
                self.assertIn(ITALIC, line)

    def test_snake_case_is_not_italic(self):
        # The single most common false positive in real transcripts.
        for source in ("call session_id here", "read test_session_commands.py"):
            with self.subTest(source=source):
                self.assertEqual(render_markdown(source), [source])

    def test_inline_code_loses_its_backticks_and_takes_a_colour(self):
        line = render_markdown("run `swe session` now")[0]
        self.assertEqual(strip_ansi(line), "run swe session now")
        self.assertIn(CYAN, line)

    def test_markup_inside_a_code_span_stays_literal(self):
        line = render_markdown("the `**kwargs` idiom")[0]
        self.assertEqual(strip_ansi(line), "the **kwargs idiom")
        self.assertNotIn(BOLD, line)

    def test_a_link_keeps_its_text_and_trails_the_url(self):
        line = render_markdown("see [the docs](https://example.com/a_b) here")[0]
        self.assertEqual(strip_ansi(line),
                         "see the docs (https://example.com/a_b) here")
        self.assertIn(DIM, line)

    def test_strikethrough_dims(self):
        line = render_markdown("~~gone~~ now")[0]
        self.assertEqual(strip_ansi(line), "gone now")
        self.assertIn(DIM, line)

    def test_inline_markup_inside_a_bullet_is_rendered(self):
        line = render_markdown("- read **auth.py** first")[0]
        self.assertEqual(strip_ansi(line), "• read auth.py first")
        self.assertIn(BOLD, line)


class PassthroughTest(unittest.TestCase):
    PLAIN = (
        "Looking at auth.py now.",
        "  indented prose that means nothing in particular",
        "a * b * c and 2 - 1",
        "call load_registry(home) with the path",
        "no markup here at all",
    )

    def test_a_line_with_no_markdown_comes_back_byte_identical(self):
        for source in self.PLAIN:
            with self.subTest(source=source):
                self.assertEqual(render_markdown(source), [source])

    def test_one_output_line_per_source_line(self):
        source = "\n".join(self.PLAIN)
        self.assertEqual(len(render_markdown(source)), len(self.PLAIN))

    def test_empty_text_renders_nothing(self):
        self.assertEqual(render_markdown(""), [])


class MixedDocumentTest(unittest.TestCase):
    DOC = """# Fixing the login bug

The **root cause** is in `auth.py`: the session_id is dropped.

## Steps

- read the *handler*
- patch `verify()`
1. run the tests

> the fix is small

| file | change |
|------|--------|
| auth.py | guard |

```python
def verify(token):
    return token
```

See [the docs](https://example.com) for ~~more~~ detail.

---
plain tail line
"""

    def test_every_word_of_the_source_survives_in_order(self):
        rendered = " ".join(strip_ansi(line) for line in render_markdown(self.DOC))
        words = [word for word in rendered.split() if word.strip("•│─")]
        wanted = [
            "Fixing", "the", "login", "bug", "The", "root", "cause", "is", "in",
            "auth.py:", "the", "session_id", "is", "dropped.", "Steps", "read",
            "the", "handler", "patch", "verify()", "1.", "run", "the", "tests",
            "the", "fix", "is", "small", "file", "change", "auth.py", "guard",
            "def", "verify(token):", "return", "token", "See", "the", "docs",
            "(https://example.com)", "for", "more", "detail.", "plain", "tail",
            "line",
        ]
        self.assertEqual(words, wanted)

    def test_no_line_still_carries_raw_markup(self):
        for line in render_markdown(self.DOC):
            plain = strip_ansi(line)
            with self.subTest(line=plain):
                self.assertNotIn("**", plain)
                self.assertNotIn("`", plain)
                self.assertNotIn("](", plain)

    def test_the_pager_can_wrap_every_rendered_line(self):
        from quiver.console import visible_len, wrap_ansi

        for line in render_markdown(self.DOC):
            for wrapped in wrap_ansi(line, 24):
                self.assertLessEqual(visible_len(wrapped), 24, repr(wrapped))


if __name__ == "__main__":
    unittest.main()
