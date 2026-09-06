"""Arrow-key picker over already-rendered session rows, with a transcript view.

Rows arrive pre-formatted: the table has already coloured and padded them, so
this module never rewrites a row, it only draws a pointer in front of one,
scrolls a window over the list, and reports which index was chosen.

Modelled on ``multiselect.statepicker``: raw mode, an explicit \r\n on every
line because tty.setraw clears ONLCR, a redraw that rewinds by exactly the
number of lines the last draw wrote, and a try/finally that restores termios
and the caret on every exit path including an exception. A shell left in raw
mode is a much worse bug than resuming the wrong session.

Space opens the highlighted session's transcript in the terminal's alternate
screen, the way a pager does, so the list underneath is untouched when the
view closes and the picker's rewind-by-lines-drawn redraw still lands.

Mouse tracking is on for the whole session so the wheel arrives as an escape
sequence the reader can turn into a move. That is also why an unrecognised
sequence is dropped instead of read as a cancel: a terminal sends far more
kinds of them than this picker knows, and none of them mean "quit". The
parsing itself lives in ``quiver.keys``, shared with every other widget.
"""

from __future__ import annotations

import shutil
import sys
from typing import Callable

from quiver import keys
from quiver.console import c, strip_ansi, truncate, wrap_ansi

FOOTER = "  ↑↓ move · enter resume · q quit"
PREVIEW_HINT = " · space preview"
VIEW_FOOTER = "  ↑↓ scroll · space/b page · g/G top/bottom · enter resume · esc back"

ALT_SCREEN_ON = "\x1b[?1049h"
ALT_SCREEN_OFF = "\x1b[?1049l"

# Re-exported so a caller (and the tests) can reach the tracking switches
# through the widget that writes them.
MOUSE_ON = keys.MOUSE_ON
MOUSE_OFF = keys.MOUSE_OFF

# On top of the shared defaults: paging and the ends of the list, plus the
# digits, which only this widget jumps on. "0" stays unbound so it cannot
# land on a row that is not there.
LETTERS = {
    b"b": "pageup", b"f": "pagedown",
    b"g": "top", b"G": "bottom",
}
LETTERS.update({str(n).encode(): str(n) for n in range(1, 10)})


def _supported() -> bool:
    """Raw mode needs a real terminal on both ends."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return True


def _read_key(fd: int) -> str:
    """One keypress, with escape sequences collapsed to a word.

    Thin over ``keys.read_key``; the only vocabulary this widget adds is in
    LETTERS. A digit comes back as itself so the loop can jump to that
    1-based row.
    """
    return keys.read_key(fd, LETTERS)


def _preview_lines(preview: Callable[[int], list[str]], index: int) -> list[str]:
    """Ask for a row's transcript, never letting it take the picker down."""
    try:
        return [str(line) for line in preview(index)]
    except Exception:
        return ["(preview unavailable)"]


def _render(rows, header, cursor, prev_lines: int, height: int,
            footer: str = FOOTER) -> int:
    """Draw one frame of the list, returning how many lines it wrote.

    The caller feeds that count back as ``prev_lines`` so the next frame
    rewinds by exactly what was drawn.
    """
    total = len(rows)
    view = min(height, total)
    top = 0
    if total > view:
        top = max(0, min(cursor - view // 2, total - view))

    out = []
    if prev_lines:
        out.append(f"\x1b[{prev_lines}A")   # back to the top of the widget
    out.append("\r\x1b[J")                  # and clear everything below it

    drawn = 0
    # Two columns of prefix on every line, so the header keeps sitting over
    # the columns it names once the pointer is in front of the rows.
    for line in header or ():
        out.append(f"  {line}\r\n")
        drawn += 1

    for i in range(top, top + view):
        pointer = c("cyan", ">") if i == cursor else " "
        out.append(f"{pointer} {rows[i]}\r\n")
        drawn += 1

    if total > view:
        out.append(c("dim", f"  {top + 1}–{top + view} of {total}") + "\r\n")
        drawn += 1
    out.append(c("dim", footer) + "\r\n")
    drawn += 1

    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return drawn


# ---------------------------------------------------------------------------
# Transcript view
# ---------------------------------------------------------------------------

def _wrap(line: str, width: int) -> list[str]:
    """Fit one document line into ``width`` columns without losing anything.

    The whole point of the view is to read what the list had to truncate,
    so prose and labels alike wrap. ``wrap_ansi`` measures visible width,
    closes any open colour at a break and re-opens it on the continuation
    line, so a bold heading or a cyan code span that wraps keeps its style
    without bleeding into the row below.
    """
    if not line.strip():
        return [""]
    return wrap_ansi(line, width) or [""]


def _view_render(lines: list[str], top: int, height: int, title: str,
                 width: int) -> None:
    """Draw one frame of the transcript on the alternate screen."""
    out = ["\x1b[H\x1b[2J"]                  # home, then clear the whole screen
    out.append(c("bold", truncate(title, width)) + "\r\n")
    for line in lines[top:top + height]:
        out.append(line + "\r\n")
    for _ in range(height - len(lines[top:top + height])):
        out.append("\r\n")
    last = min(len(lines), top + height)
    where = f"  {top + 1}–{last} of {len(lines)}" if lines else "  empty"
    out.append(c("dim", where + " ·" + VIEW_FOOTER))
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def _view(fd: int, document: list[str], title: str) -> str:
    """Page through ``document`` until the user backs out or picks it.

    Returns "enter" when the user chose this session from inside the view,
    "close" otherwise. Opens scrolled to the end because where a session
    stopped is what tells two forks of the same title apart.
    """
    columns, rows = shutil.get_terminal_size(fallback=(80, 24))
    width = max(20, columns - 1)
    height = max(3, rows - 2)             # title line + footer line
    lines = [w for line in document for w in _wrap(line, width)]
    top = max(0, len(lines) - height)
    while True:
        _view_render(lines, top, height, title, width)
        key = _read_key(fd)
        if key in ("cancel", "escape"):
            return "close"
        if key == "enter":
            return "enter"
        if key == "up":
            top -= 1
        elif key == "down":
            top += 1
        elif key in ("pagedown", "space"):
            top += height
        elif key == "pageup":
            top -= height
        elif key == "top":
            top = 0
        elif key == "bottom":
            top = len(lines)
        top = max(0, min(top, max(0, len(lines) - height)))


def pick_session(
    rows: list[str],
    header: list[str] | None = None,
    preview: Callable[[int], list[str]] | None = None,
    height: int | None = None,
) -> int | None:
    """Return the 0-based index of the picked row, or None if nothing was.

    None also covers an empty list and a non-terminal stdin, so piping
    `swe session -i` cannot hang a script.

    ``preview(index)`` returns the session's transcript as document lines:
    plain text lines wrap in the view, lines carrying ANSI are treated as
    labels and cut to the width. Space opens it; Esc or q comes back; Enter
    inside the view resumes that session without a second keypress.
    """
    if not rows:
        return None
    if not _supported():
        print(c("dim", "  not a terminal, nothing selected"))
        return None

    import termios
    import tty

    if height is None:
        # Leave room for the header, the footer, the range line, and the
        # shell prompt that follows.
        lines = shutil.get_terminal_size(fallback=(80, 24)).lines
        height = max(3, lines - len(header or ()) - 4)

    footer = FOOTER + (PREVIEW_HINT if preview is not None else "")
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    cursor, drawn = 0, 0
    try:
        tty.setraw(fd)
        # Caret off while redrawing, wheel on for the whole session: tracking
        # stays enabled across the alternate screen, so the pager scrolls on
        # the same reports the list moves on.
        sys.stdout.write("\x1b[?25l" + MOUSE_ON)
        while True:
            drawn = _render(rows, header, cursor, drawn, height, footer)
            key = _read_key(fd)
            if key in ("cancel", "escape"):
                return None
            if key == "enter":
                return cursor
            if key == "up":
                cursor = (cursor - 1) % len(rows)
            elif key == "down":
                cursor = (cursor + 1) % len(rows)
            elif key == "top":
                cursor = 0
            elif key == "bottom":
                cursor = len(rows) - 1
            elif key.isdigit():
                target = int(key) - 1
                if target < len(rows):
                    cursor = target
            elif key == "space" and preview is not None:
                document = _preview_lines(preview, cursor)
                title = strip_ansi(rows[cursor]).strip()
                sys.stdout.write(ALT_SCREEN_ON)
                try:
                    outcome = _view(fd, document, title)
                finally:
                    # Back to the primary screen, whose contents and caret
                    # position the terminal kept, so the next _render's
                    # rewind still lands on the top of the list.
                    sys.stdout.write(ALT_SCREEN_OFF)
                    sys.stdout.flush()
                if outcome == "enter":
                    return cursor
    finally:
        # Every exit path, exception included. Tracking goes off before the
        # termios restore, because a shell left reporting the wheel spews
        # escape sequences at the prompt on every scroll.
        sys.stdout.write(MOUSE_OFF + "\x1b[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.flush()
