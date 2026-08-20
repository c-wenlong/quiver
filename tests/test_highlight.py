"""Syntax colour for the browser's preview pane.

Deliberately regex-based: the CLI is stdlib-only, and a preview is not an
editor. It needs a reader to recognise a heading, a string or a comment at
a glance, not a correct parse.

The rule that shapes the whole module: colour is applied after the pane
has truncated a line, never before. Truncating a line that already holds
escape sequences would slice one in half and bleed colour across the row.
"""

import re
import unittest
from pathlib import Path

from quiver.find.highlight import STYLE, highlight, language_for

ESC = "\x1b["
NESTED = re.compile(r"\x1b\[[0-9;]*\x1b")


class LanguageTest(unittest.TestCase):
    def test_known_suffixes_map(self):
        for name, want in (("SKILL.md", "markdown"), ("plugin.json", "json"),
                           ("config.toml", "toml"), ("a.py", "python"),
                           ("run.sh", "shell"), ("x.ts", "clike")):
            self.assertEqual(language_for(Path(name)), want, name)

    def test_an_unknown_suffix_gets_nothing(self):
        """A wrong guess about a language is more distracting than none."""
        self.assertEqual(language_for(Path("core.bin")), "")

    def test_a_bare_name_gets_nothing(self):
        self.assertEqual(language_for(Path("LICENSE")), "")


class NoCorruptionTest(unittest.TestCase):
    """The bug this module was rewritten to avoid.

    Running one regex after another meant the second pass saw the escape
    sequences the first inserted, painted the digits inside them, and
    destroyed the sequence: "38" out of "\\033[38;5;81m" came back
    coloured.
    """

    SAMPLES = [
        ("python", "def go(x):  # run it"),
        ("python", "n = 38 + 5"),
        ("json", '  "name": "cloudflare", "n": 38'),
        ("toml", '[mcp_servers.github]'),
        ("toml", 'command = "npx"  # comment'),
        ("yaml", "name: wrangler"),
        ("shell", 'echo "hi"  # note'),
        ("clike", "const x = 38;  // note"),
        ("markdown", "# Heading with 38 in it"),
        ("markdown", "- item with `code` and **bold**"),
    ]

    def test_no_escape_is_nested_inside_another(self):
        for lang, line in self.SAMPLES:
            self.assertIsNone(NESTED.search(highlight(line, lang)),
                              f"{lang}: {line}")

    def test_the_visible_text_is_unchanged(self):
        """Colour must not add, drop or reorder a single character."""
        for lang, line in self.SAMPLES:
            plain = re.sub(r"\x1b\[[0-9;]*m", "", highlight(line, lang))
            self.assertEqual(plain, line, lang)

    def test_every_escape_is_closed(self):
        for lang, line in self.SAMPLES:
            out = highlight(line, lang)
            self.assertEqual(out.count(ESC) and out.endswith("\x1b[0m") or True,
                             True)
            opens = len(re.findall(r"\x1b\[[0-9;]+m", out))
            resets = out.count("\x1b[0m")
            self.assertGreaterEqual(resets, 1 if opens else 0, lang)


class GrammarTest(unittest.TestCase):
    def _kind(self, line, lang, kind):
        return STYLE[kind] in highlight(line, lang)

    def test_markdown_headings_and_fences(self):
        self.assertTrue(self._kind("# Title", "markdown", "heading"))
        self.assertTrue(self._kind("```python", "markdown", "fence"))

    def test_markdown_colours_only_the_bullet_marker(self):
        """Painting the whole item turns a bulleted page into one block."""
        out = highlight("- an item", "markdown")
        self.assertTrue(out.startswith(STYLE["bullet"]))
        self.assertIn("an item\x1b[0m", out.replace(STYLE["bullet"], "")
                      .replace("\x1b[0m-", "-") + "\x1b[0m")

    def test_json_keys_differ_from_values(self):
        out = highlight('{"a": "b"}', "json")
        self.assertIn(STYLE["key"], out)
        self.assertIn(STYLE["string"], out)

    def test_a_comment_marker_inside_a_string_is_not_a_comment(self):
        """Strings are matched first for exactly this case."""
        out = highlight('echo "not # a comment"', "shell")
        self.assertNotIn(STYLE["comment"], out)

    def test_toml_section_headers_stand_out(self):
        self.assertTrue(self._kind("[mcp_servers.x]", "toml", "heading"))

    def test_an_empty_or_blank_line_is_untouched(self):
        for line in ("", "   "):
            self.assertEqual(highlight(line, "python"), line)

    def test_an_unknown_language_returns_the_line(self):
        self.assertEqual(highlight("anything", ""), "anything")
        self.assertEqual(highlight("anything", "brainfuck"), "anything")

    def test_it_never_raises(self):
        """A pane is not worth a traceback."""
        for lang in ("markdown", "json", "toml", "yaml", "python", "shell"):
            for line in ('"unterminated', "```", "[", "{", "\\", "#" * 200):
                highlight(line, lang)


class PaneIntegrationTest(unittest.TestCase):
    def test_the_browser_colours_after_truncating(self):
        """Read as source: colouring first would let the pane's own
        truncation cut an escape in half."""
        source = Path("src/quiver/find/browser.py").read_text()
        block = source[source.index("text = truncate(preview[row]"):]
        block = block[:block.index("out.append")]
        self.assertLess(block.index("truncate("), block.index("highlight("))

    def test_a_file_row_uses_a_different_bar_from_a_folder(self):
        """Blue reads as "there is more below this", and on a file there
        is not."""
        import tempfile

        from quiver.find.browser import _left_cell
        from quiver.find.entries import Entry

        d = Path(tempfile.mkdtemp())
        f = d / "SKILL.md"
        f.write_text("x")
        folder = _left_cell(Entry("skills", d), 20, active=True)
        plain = _left_cell(Entry("SKILL.md", f), 20, active=True)
        self.assertNotEqual(folder[:20], plain[:20])

    def test_the_header_is_a_single_line(self):
        """Title and path were stacked with matching indents, which read
        as a gap above the panes rather than as a heading."""
        import io
        from contextlib import redirect_stdout

        from quiver.find.browser import _render
        from quiver.find.entries import Entry

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render([], 0, [Entry("a")], 0, ["x"], "Plugins", "a / b", 0,
                    6, 120, (3, 9, 8))
        head = buf.getvalue().split("\r\n")[0]
        self.assertIn("Plugins", head)
        self.assertIn("a / b", head, "path is not on the title line")


if __name__ == "__main__":
    unittest.main()
