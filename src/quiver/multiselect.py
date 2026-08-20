"""A spacebar-to-toggle, enter-to-confirm checklist for the terminal.

Deliberately small: raw mode, arrows or j/k to move, space to toggle, enter to
confirm, q or Ctrl-C to cancel. No dependency, and it restores the terminal on
every exit path including an exception, because leaving a shell in raw mode is
a much worse bug than anything this widget is used for.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from quiver.console import c


@dataclass
class Choice:
    key: str
    label: str
    about: str = ""
    locked: bool = False      # shown ticked, cannot be toggled


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
    """One keypress, with arrow escape sequences collapsed to a word."""
    import os

    ch = os.read(fd, 1)
    if ch == b"\x1b":                     # escape: maybe an arrow
        rest = os.read(fd, 2)
        return {b"[A": "up", b"[B": "down"}.get(rest, "escape")
    return {
        b" ": "space", b"\r": "enter", b"\n": "enter",
        b"\x03": "cancel", b"q": "cancel",
        b"k": "up", b"j": "down",
        b"a": "all", b"n": "none",
    }.get(ch, "")


FOOTER = "  space toggle · a all · n none · enter save · q cancel"


def _render(choices, selected, cursor, title, prev_lines: int) -> int:
    """Draw the checklist, returning how many lines it wrote.

    The caller feeds that count back as ``prev_lines`` so the next redraw
    rewinds by exactly what was drawn. Counting lines from the choice list
    instead was off by one, and once a redraw scrolls the terminal any fixed
    count drifts, which is how the footer ended up repeating on every keypress.

    Lines end with an explicit \r\n: tty.setraw clears ONLCR, so a bare \n
    moves down without returning to column 0.
    """
    out = []
    if prev_lines:
        out.append(f"\x1b[{prev_lines}A")   # back to the top of the widget
    out.append("\r\x1b[J")                  # and clear everything below it

    out.append(c("bold", title) + "\r\n")
    for i, ch in enumerate(choices):
        box = "[x]" if (ch.locked or ch.key in selected) else "[ ]"
        tail = c("dim", "  always shown") if ch.locked else ""
        pointer = c("cyan", ">") if i == cursor else " "
        body = f"{box} {ch.label.ljust(12)} {c('dim', ch.about)}{tail}"
        line = c("cyan", body) if i == cursor else (c("dim", body) if ch.locked else body)
        out.append(f" {pointer} {line}\r\n")
    out.append(c("dim", FOOTER) + "\r\n")

    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return len(choices) + 2      # title + one per choice + footer


def multiselect(choices: list[Choice], selected=None, title="Select") -> list[str] | None:
    """Return the chosen keys, or None if cancelled.

    Falls back to returning the current selection unchanged when there is no
    terminal, so piping `swe list edit` cannot hang a script.
    """
    chosen = set(selected or []) | {ch.key for ch in choices if ch.locked}
    if not _supported():
        print(c("dim", "  not a terminal, nothing changed"))
        return None

    import os
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    cursor, drawn = 0, 0
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")     # hide the caret while redrawing
        while True:
            drawn = _render(choices, chosen, cursor, title, drawn)
            key = _read_key(fd)
            if key == "cancel":
                return None
            if key == "enter":
                return [ch.key for ch in choices if ch.key in chosen]
            if key == "up":
                cursor = (cursor - 1) % len(choices)
            elif key == "down":
                cursor = (cursor + 1) % len(choices)
            elif key == "space":
                ch = choices[cursor]
                if not ch.locked:
                    chosen.symmetric_difference_update({ch.key})
            elif key == "all":
                chosen = {ch.key for ch in choices}
            elif key == "none":
                chosen = {ch.key for ch in choices if ch.locked}
    finally:
        # Every exit path, exception included: a shell left in raw mode is a
        # far worse outcome than a mis-picked column.
        sys.stdout.write("\x1b[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.flush()
