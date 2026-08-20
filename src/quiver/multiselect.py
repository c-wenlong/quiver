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
    # A row whose label carries a value you can rotate, e.g. the session
    # window on the counts column. cycle(value, step) -> new value.
    value: object = None
    cycle: object = None
    render_value: object = None


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
        return {b"[A": "up", b"[B": "down",
                b"[C": "next", b"[D": "prev"}.get(rest, "escape")
    return {
        b" ": "space", b"\r": "enter", b"\n": "enter",
        b"\x03": "cancel", b"q": "cancel",
        b"k": "up", b"j": "down",
        b"a": "all", b"n": "none",
        # A bare Shift is not something a terminal reports, so the rotation is
        # bound to left/right and to < / > which are themselves shifted keys.
        b"<": "prev", b",": "prev", b"h": "prev",
        b">": "next", b".": "next", b"l": "next",
    }.get(ch, "")


FOOTER = ("  space toggle · ← → change · a all · n none · "
          "enter save · q cancel")


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
        label = ch.label
        if ch.cycle is not None:
            shown = ch.render_value(ch.value) if ch.render_value else str(ch.value)
            label = shown
            tail = c("dim", "  ← → to change")
        pointer = c("cyan", ">") if i == cursor else " "
        body = f"{box} {label.ljust(12)} {c('dim', ch.about)}{tail}"
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
            elif key in ("prev", "next"):
                ch = choices[cursor]
                if ch.cycle is not None:
                    ch.value = ch.cycle(ch.value, 1 if key == "next" else -1)
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


# ---------------------------------------------------------------------------
# Tri-state picker
# ---------------------------------------------------------------------------

# A harness is in exactly one of these. Ordered so space cycles through them
# in the direction you would say them: it's fine → I like it → I'm done with it.
STATES = ("active", "starred", "archived")

STATE_GLYPH = {
    "active": ("dim", "·"),
    "starred": ("neon_pink", "★"),
    "archived": ("yellow", "▪"),
}


@dataclass
class StateChoice:
    key: str
    label: str
    state: str = "active"
    about: str = ""


STATE_FOOTER = ("  space cycle · ← → change · s star · x archive · c active · "
                "enter save · q cancel")


def _state_render(choices, cursor, title, prev_lines: int, height: int) -> int:
    """Draw a window onto the list, keeping the cursor inside it.

    The checkbox widget draws every row, which is fine for a fixed set of
    columns. A registry can hold thirty-odd harnesses, and once the drawing
    is taller than the terminal it scrolls, after which rewinding by a fixed
    count lands in the wrong place and the widget smears down the screen.
    So this one draws at most ``height`` rows and scrolls them itself.
    """
    total = len(choices)
    view = min(height, total)
    top = 0
    if total > view:
        top = max(0, min(cursor - view // 2, total - view))

    counts = {s: sum(1 for ch in choices if ch.state == s) for s in STATES}
    out = []
    if prev_lines:
        out.append(f"\x1b[{prev_lines}A")
    out.append("\r\x1b[J")

    tally = "  ".join(
        f"{c(STATE_GLYPH[s][0], STATE_GLYPH[s][1])} {c('dim', f'{counts[s]} {s}')}"
        for s in STATES
    )
    out.append(f"{c('bold', title)}   {tally}\r\n")

    width = max((len(ch.label) for ch in choices), default=10) + 2
    for i in range(top, top + view):
        ch = choices[i]
        colour, glyph = STATE_GLYPH[ch.state]
        pointer = c("cyan", ">") if i == cursor else " "
        body = (f"{c(colour, glyph)} {ch.label.ljust(width)}"
                f"{c('dim', ch.state.ljust(9))}{c('dim', ch.about)}")
        out.append(f" {pointer} {body}\r\n")

    if total > view:
        out.append(c("dim", f"  {top + 1}–{top + view} of {total}") + "\r\n")
    out.append(c("dim", STATE_FOOTER) + "\r\n")

    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return view + 2 + (1 if total > view else 0)


def _read_state_key(fd: int) -> str:
    import os

    ch = os.read(fd, 1)
    if ch == b"\x1b":
        rest = os.read(fd, 2)
        return {b"[A": "up", b"[B": "down",
                b"[C": "next", b"[D": "prev"}.get(rest, "escape")
    return {
        b" ": "next", b"\r": "enter", b"\n": "enter",
        b"\x03": "cancel", b"q": "cancel",
        b"k": "up", b"j": "down",
        b"s": "starred", b"x": "archived", b"c": "active",
        b"<": "prev", b",": "prev", b"h": "prev",
        b">": "next", b".": "next", b"l": "next",
    }.get(ch, "")


def statepicker(choices: list[StateChoice], title="Select",
                height: int | None = None) -> list[StateChoice] | None:
    """Cycle each row through STATES. Returns the choices, or None if cancelled.

    Mutates and returns the same objects, so the caller compares against the
    states it passed in to work out what actually changed.
    """
    if not choices:
        return []
    if not _supported():
        print(c("dim", "  not a terminal, nothing changed"))
        return None

    import shutil
    import termios
    import tty

    if height is None:
        # Leave room for the title, the footer, the range line, and the
        # shell prompt that follows.
        height = max(5, shutil.get_terminal_size(fallback=(80, 24)).lines - 5)

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    cursor, drawn = 0, 0
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        while True:
            drawn = _state_render(choices, cursor, title, drawn, height)
            key = _read_state_key(fd)
            if key == "cancel":
                return None
            if key == "enter":
                return choices
            if key == "up":
                cursor = (cursor - 1) % len(choices)
            elif key == "down":
                cursor = (cursor + 1) % len(choices)
            elif key in ("next", "prev"):
                ch = choices[cursor]
                step = 1 if key == "next" else -1
                ch.state = STATES[(STATES.index(ch.state) + step) % len(STATES)]
            elif key in STATES:
                choices[cursor].state = key
    finally:
        sys.stdout.write("\x1b[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.flush()
