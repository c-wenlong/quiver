"""Markdown to ANSI, for reading an assistant's answer in the pager.

Assistant turns are written as markdown, so a transcript shown verbatim is
a wall of hashes, asterisks and backticks. This renders the constructs a
reply actually uses and leaves everything else alone: a line with no
markup in it comes back byte for byte.

Deliberately regex-based and small, in the spirit of ``find/highlight``.
The CLI is stdlib-only and a preview is not a renderer; it has to let a
reader pick out a heading, a bullet or a code span at a glance, not parse
CommonMark. Nothing here wraps: lines go out at their natural length and
the pager breaks them with ``console.wrap_ansi``, which knows how to
carry a style across a break.
"""

from __future__ import annotations

import re

from quiver.console import COLORS, c

_RESET = COLORS["reset"]
_RULE = "─" * 40

# Block shapes. Each is anchored, so a construct only counts at the head
# of a line and prose that merely mentions one is left as prose.
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_HR = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_QUOTE = re.compile(r"^(\s*)>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^:?-{2,}:?$")

# Inline shapes, applied in the order they are listed below.
_CODE = re.compile(r"(`+)([^`]+?)\1")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_STRIKE = re.compile(r"~~(.+?)~~")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_STAR = re.compile(r"\*(\S(?:[^*]*\S)?)\*")
# Only at a word boundary, so file_name and snake_case survive intact.
_ITALIC_UNDER = re.compile(r"(?<!\w)_(\S(?:[^_]*\S)?)_(?!\w)")


def _style(name: str, text: str) -> str:
    """Colour a whole run, re-opening it after any nested reset.

    ``c()`` closes with a reset, so a code span inside a heading would end
    the heading's bold halfway along the line and the rest would render
    light. Re-opening the style after every inner reset keeps the run
    whole without having to know what the inner span turned on.
    """
    code = COLORS.get(name)
    if code is None:
        return text
    body = text.replace(_RESET, _RESET + code)
    if body.endswith(code):
        body = body[: -len(code)]        # nothing left to re-open
    return c(name, body)


def _inline(text: str) -> str:
    """Render the inline markup in one line of prose.

    Code spans are pulled out and held aside before anything else runs.
    Backticks are where people put the markup they mean literally, and a
    ``**`` or an ``_`` inside them has to survive as itself. Link targets
    are held for the same reason: an underscore in a URL is not emphasis.
    """
    if not text:
        return text
    held: list[str] = []

    def hold(rendered: str) -> str:
        held.append(rendered)
        return "\x00%d\x00" % (len(held) - 1)

    out = _CODE.sub(lambda m: hold(c("cyan", m.group(2))), text)
    out = _LINK.sub(
        lambda m: m.group(1) + hold(c("dim", " (%s)" % m.group(2))), out
    )
    out = _STRIKE.sub(lambda m: _style("dim", m.group(1)), out)
    out = _BOLD.sub(lambda m: _style("bold", m.group(1) or m.group(2) or ""), out)
    out = _ITALIC_STAR.sub(lambda m: _style("italic", m.group(1)), out)
    out = _ITALIC_UNDER.sub(lambda m: _style("italic", m.group(1)), out)
    for index, rendered in enumerate(held):
        out = out.replace("\x00%d\x00" % index, rendered)
    return out


def _table_row(line: str) -> str:
    """One pipe row, or a rule when the row is the header separator.

    Columns are not aligned. Doing it properly needs the whole table in
    hand and a width the renderer does not know, and a preview that
    reflows every table is worse than one that keeps the cells readable.
    """
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if cells and all(_TABLE_SEP.match(cell) for cell in cells if cell):
        return c("dim", _RULE)
    return c("dim", " │ ").join(_inline(cell) for cell in cells)


def _block(line: str, stripped: str) -> str:
    """One document line, rendered by whichever block shape it matches."""
    if not stripped:
        return line
    if _HR.match(line):
        return c("dim", _RULE)
    match = _HEADING.match(stripped)
    if match:
        head = _inline(match.group(2))
        if len(match.group(1)) == 1:
            return _style("bold", _style("cyan", head))
        return _style("bold", head)
    match = _QUOTE.match(line)
    if match:
        return match.group(1) + _style("dim", "│ " + _inline(match.group(2)))
    if stripped.startswith("|"):
        return _table_row(line)
    match = _BULLET.match(line)
    if match:
        return match.group(1) + "• " + _inline(match.group(2))
    match = _NUMBER.match(line)
    if match:
        return match.group(1) + match.group(2) + " " + _inline(match.group(3))
    return _inline(line)


def render_markdown(text: str) -> list[str]:
    """``text`` as display lines, one per source line, blanks included.

    Fence lines are the only thing dropped: they are markup with nothing
    to show. Everything inside a fence is passed through untouched and
    dimmed, because the whole point of a code block is that the asterisks
    and underscores in it are code.
    """
    lines: list[str] = []
    fence = ""
    for line in text.splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence) and not stripped.strip(fence[0]):
                fence = ""
            else:
                lines.append("  " + c("dim", line))
            continue
        opener = _FENCE.match(stripped)
        if opener:
            fence = opener.group(1)
            continue
        lines.append(_block(line, stripped))
    return lines
