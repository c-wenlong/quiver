"""A read-only, two-pane browser for the entries `swe find` collects.

Yazi's layout without any of the verbs that write: the current level on the
left, a preview of whatever is highlighted on the right. Nothing here opens,
edits, moves or deletes, so a mis-keyed descent costs one keypress.

Levels below a root are read from the filesystem on descent rather than
walked up front. A skills library holds thousands of files, and paying for
all of them before drawing fifteen roots makes the command look hung.

The raw-mode discipline is lifted from quiver.multiselect: termios saved and
restored on every exit path including an exception, the caret hidden while
redrawing, explicit carriage returns on every line because tty.setraw clears
ONLCR, and redraws that rewind by exactly the number of lines last drawn.
"""

from __future__ import annotations

import sys
from itertools import islice
from pathlib import Path

from quiver.console import c, elide, strip_ansi, truncate, visible_len
from quiver.find.entries import HIDE_DIRS, TEXT_SUFFIXES, Entry

# Past this, reading a file to show a dozen lines costs more than the preview
# is worth, and a minified bundle or a checkpoint will happily be several MB.
MAX_PREVIEW_BYTES = 256 * 1024

FOOTER = ("  ↑↓ move · → open · ← back · g/G top, bottom · "
          "[ ] { } resize · q quit")

# Relative widths of parent, current and preview. Yazi's default shape:
# the parent is only there for orientation, the preview earns the most
# room because it is what you are actually reading.
DEFAULT_RATIO = (2, 3, 5)
MIN_PANE = 10


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
    if not ch:
        # stdin closed under us; treating that as a quit stops the loop from
        # spinning on an endless stream of empty reads.
        return "cancel"
    if ch == b"\x1b":                     # escape: maybe an arrow
        rest = os.read(fd, 2)
        return {b"[A": "up", b"[B": "down",
                b"[C": "open", b"[D": "back"}.get(rest, "escape")
    return {
        b"\r": "open", b"\n": "open",
        b"\x03": "cancel", b"q": "cancel",
        b"k": "up", b"j": "down",
        b"l": "open", b"h": "back",
        b"g": "top", b"G": "bottom",
        # Two dividers, so two pairs. Square brackets sit next to each
        # other and read as "push this edge left / right".
        b"[": "wider_parent", b"]": "narrower_parent",
        b"{": "wider_preview", b"}": "narrower_preview",
    }.get(ch, "")


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _visible_children(path: Path) -> list[Path] | None:
    """Children worth showing, directories first, or None if unreadable.

    Dotted names go along with HIDE_DIRS: between them they cover the plugin
    marker directories, which sit beside the real content and bury it.

    Returning None rather than an empty list keeps "you may not read this"
    distinguishable from "there is nothing in here", which is the difference
    between a preview that says so and one that lies.
    """
    try:
        kids = list(path.iterdir())
    except OSError:
        return None
    keep = [k for k in kids
            if k.name not in HIDE_DIRS and not k.name.startswith(".")]
    keep.sort(key=lambda k: (not k.is_dir(), k.name.lower()))
    return keep


def _child_entries(path: Path) -> list[Entry]:
    """One Entry per visible child, with a size or a count as its detail."""
    kids = _visible_children(path)
    if kids is None:
        return []
    out = []
    for k in kids:
        if k.is_dir():
            inner = _visible_children(k)
            if inner is None:
                detail = "unreadable"
            else:
                detail = "1 item" if len(inner) == 1 else f"{len(inner)} items"
        else:
            try:
                detail = _human_size(k.stat().st_size)
            except OSError:
                detail = "unreadable"
        out.append(Entry(label=k.name, path=k, detail=detail))
    return out


def _descend(entry: Entry) -> list[Entry]:
    """The level opening ``entry`` would show, or empty if it opens nothing.

    In-memory children win over the path so that a grouping row and a real
    directory navigate the same way, which is the whole point of Entry
    carrying both.
    """
    if entry.children:
        return list(entry.children)
    if entry.path is None:
        return []
    try:
        if not entry.path.is_dir():
            return []
    except OSError:
        return []
    return _child_entries(entry.path)


def _file_preview(path: Path, limit: int) -> list[str]:
    try:
        size = path.stat().st_size
    except OSError:
        return ["(unreadable)"]
    if path.suffix.lower() not in TEXT_SUFFIXES or size >= MAX_PREVIEW_BYTES:
        return [path.name, _human_size(size), "binary or too large"]
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            rows = [line.rstrip("\r\n").expandtabs(4)
                    for line in islice(fh, limit)]
    except OSError:
        return ["(unreadable)"]
    return rows or ["(empty file)"]


def _preview(entry: Entry, limit: int) -> list[str]:
    """What the right pane shows for ``entry``, at most ``limit`` lines.

    A directory is previewed by name only. Building full entries for it would
    stat every child on every keypress, and the pane shows names anyway.
    """
    if entry.children:
        rows = [e.label + ("/" if e.can_descend else "")
                for e in entry.children[:limit]]
        return rows or ["(empty)"]
    if entry.path is None:
        return ["(empty)"]
    try:
        is_dir = entry.path.is_dir()
    except OSError:
        return ["(unreadable)"]
    if not is_dir:
        return _file_preview(entry.path, limit)

    kids = _visible_children(entry.path)
    if kids is None:
        return ["(unreadable)"]
    if not kids:
        return ["(empty)"]
    shown = kids[:limit - 1] if len(kids) > limit else kids
    rows = [k.name + ("/" if k.is_dir() else "") for k in shown]
    if len(kids) > len(rows):
        rows.append(f"... {len(kids) - len(rows)} more")
    return rows


# A filled bar rather than coloured text. A foreground-only highlight is
# easy to lose in a pane you are not driving, and impossible to see at a
# glance across three of them. 33 is a mid blue that stays legible under
# both light and dark terminal themes.
SELECT_BG = "\033[48;5;33m\033[38;5;16m"
SELECT_DIM_BG = "\033[48;5;24m\033[38;5;253m"
RESET = "\033[0m"


def _left_cell(entry: Entry, width: int, active: bool, muted: bool = False) -> str:
    """One row of a list pane, exactly ``width`` columns wide.

    ``muted`` is the parent pane: it keeps a bar on the row you came
    through, in a quieter blue, so orientation survives without competing
    with the pane you are actually driving.
    """
    # No leading glyph. The bar already says which row is selected, and
    # colour already says which rows descend, so a triangle on every line
    # only narrows the column that holds the name.
    detail_w = min(len(entry.detail), width // 3) if entry.detail else 0
    name_w = max(1, width - 1 - (detail_w + 1 if detail_w else 0))
    name = elide(entry.label, name_w).ljust(name_w)
    detail = (" " + truncate(entry.detail, detail_w).rjust(detail_w)
              if detail_w else "")

    if active:
        # The bar spans the whole cell, so it has to be built from plain
        # text: nesting colours inside it would end the background early.
        bar = SELECT_DIM_BG if muted else SELECT_BG
        return bar + f" {name}{detail}".ljust(width) + RESET

    body = c("blue", f" {name}") if entry.can_descend else f" {name}"
    if detail:
        body += c("dim", detail)
    return body


def _pane_widths(width: int, ratio: tuple[int, int, int]) -> tuple[int, int, int]:
    """Split the row between parent, current and preview.

    Weights rather than fixed columns, so a resize keeps working when the
    window changes. Each pane is floored: a pane narrower than MIN_PANE
    shows nothing useful, and a reader would rather lose the parent than
    read three columns of ellipsis.
    """
    avail = max(3 * MIN_PANE, width - 7)   # gaps and the two separators
    total = sum(ratio) or 1
    parent = max(MIN_PANE, avail * ratio[0] // total)
    current = max(MIN_PANE, avail * ratio[1] // total)
    preview = max(MIN_PANE, avail - parent - current)
    # Give back any overshoot from the floors, taking it from the widest.
    over = parent + current + preview - avail
    while over > 0:
        widest = max((parent, "p"), (current, "c"), (preview, "v"))[1]
        if widest == "p" and parent > MIN_PANE:
            parent -= 1
        elif widest == "c" and current > MIN_PANE:
            current -= 1
        elif preview > MIN_PANE:
            preview -= 1
        else:
            break
        over -= 1
    return parent, current, preview


def _pane_cell(entry: Entry | None, width: int, active: bool, dim: bool) -> str:
    """One row of a list pane, padded to width."""
    if entry is None:
        return " " * width
    cell = _left_cell(entry, width, active, muted=dim)
    if dim and not active:
        # Only the unselected parent rows are flattened: the selected one
        # keeps its bar, which is the whole point of showing the parent.
        cell = c("dim", strip_ansi(cell))
    return cell + " " * max(0, width - visible_len(cell))


def _render(parent: list[Entry], parent_cursor: int,
            entries: list[Entry], cursor: int, preview: list[str],
            title: str, crumb: str, prev_lines: int, height: int,
            width: int, ratio) -> int:
    """Draw three panes, returning how many lines were written.

    Parent on the left, the level you are in next to it, and what the
    highlighted row contains on the right. The parent pane is what makes
    left and right feel like movement rather than a jump: you can see
    where back would take you before you press it.

    The caller feeds the returned count back as ``prev_lines`` so the next
    redraw rewinds by exactly what was drawn. Rows are budgeted one column
    short of the terminal: a line filling the last column leaves some
    terminals with a pending wrap, and one stray wrap puts every later
    rewind in the wrong place.
    """
    total = len(entries)
    view = min(height, max(total, len(preview), len(parent), 1))

    def window(n: int, cur: int) -> int:
        return 0 if n <= view else max(0, min(cur - view // 2, n - view))

    top = window(total, cursor)
    ptop = window(len(parent), parent_cursor)

    parent_w, current_w, preview_w = _pane_widths(width, ratio)

    out = []
    if prev_lines:
        out.append(f"\x1b[{prev_lines}A")
    out.append("\r\x1b[J")

    out.append(c("bold", truncate(title, width - 1)) + "\r\n")
    out.append(c("dim", elide(crumb, width - 1)) + "\r\n")

    sep = c("dim", "│")
    for row in range(view):
        i, pi = top + row, ptop + row

        # Parent: dimmed, with the row you came through still marked, so
        # the eye can find it without it competing with the live cursor.
        p_entry = parent[pi] if pi < len(parent) else None
        left = _pane_cell(p_entry, parent_w, pi == parent_cursor, dim=True)

        if i < total:
            mid = _pane_cell(entries[i], current_w, i == cursor, dim=False)
        elif not total and not row:
            mid = c("dim", "(empty)".ljust(current_w))
        else:
            mid = " " * current_w
        mid += " " * max(0, current_w - visible_len(mid))

        text = preview[row] if row < len(preview) else ""
        right = c("dim", truncate(text, preview_w)) if text else ""
        out.append(f"{left} {sep} {mid} {sep} {right}\r\n")

    tail = []
    if total > view:
        tail.append(f"{top + 1}–{top + view} of {total}")
    if tuple(ratio) != DEFAULT_RATIO:
        tail.append(f"panes {ratio[0]}:{ratio[1]}:{ratio[2]}")
    if tail:
        out.append(c("dim", "  " + "  ·  ".join(tail)) + "\r\n")
    out.append(c("dim", truncate(FOOTER, width - 1)) + "\r\n")

    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return view + 3 + (1 if tail else 0)


def browse(roots: list[Entry], title: str = "") -> int:
    """Walk ``roots`` in a two-pane browser. Always returns 0.

    The return value exists because callers are CLI commands that hand it
    straight back as an exit status. Browsing cannot fail in a way the shell
    should care about: an unreadable directory is reported in the pane, and
    quitting is the expected ending, not an error.

    Without a terminal it says so and returns rather than blocking, so
    `swe find --interactive` in a pipeline cannot hang a script.
    """
    if not _supported():
        print(c("dim", "  not a terminal, nothing to browse"))
        return 0
    if not roots:
        print(c("dim", "  nothing to browse"))
        return 0

    import os
    import select
    import shutil
    import signal
    import termios
    import tty

    def measure() -> tuple[int, int]:
        """Current window, re-read every frame rather than once at startup.

        Asks the terminal directly rather than going through
        shutil.get_terminal_size, which prefers the COLUMNS environment
        variable. A resize never updates COLUMNS, so a shell that exports
        it would pin the browser to the size it started at.

        Leaves room for the title, breadcrumb, range line, footer and the
        shell prompt that follows.
        """
        try:
            size = os.get_terminal_size(fd)
        except OSError:
            size = shutil.get_terminal_size(fallback=(80, 24))
        return max(5, size.lines - 6), max(40, size.columns)

    heading = title or "browse"
    fd = sys.stdin.fileno()

    ratio = list(DEFAULT_RATIO)
    levels: list[list[Entry]] = [list(roots)]
    cursors: list[int] = [0]
    trail: list[str] = []

    # A resize arrives as SIGWINCH, but PEP 475 retries the interrupted
    # read for us, so the handler would never be noticed by a blocking
    # os.read. Waiting on select instead lets the loop wake on its own and
    # notice the new size.
    resized = [True]

    def on_winch(_sig, _frame):
        resized[0] = True

    saved = termios.tcgetattr(fd)
    previous_winch = None
    try:
        previous_winch = signal.signal(signal.SIGWINCH, on_winch)
    except (AttributeError, ValueError, OSError):
        # No SIGWINCH on this platform, or not the main thread. Polling
        # still catches the resize, just on the next tick.
        previous_winch = None

    drawn = 0
    height, width = measure()
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")     # hide the caret while redrawing
        while True:
            entries = levels[-1]
            cursor = min(cursors[-1], max(0, len(entries) - 1))
            cursors[-1] = cursor
            crumb = "  " + (" / ".join(trail) if trail else "top level")
            preview = _preview(entries[cursor], height) if entries else []
            # levels[-2] is literally where back would take you, so the
            # parent pane needs no separate bookkeeping.
            parent = levels[-2] if len(levels) > 1 else []
            parent_cursor = cursors[-2] if len(cursors) > 1 else 0
            drawn = _render(parent, parent_cursor, entries, cursor, preview,
                            heading, crumb, drawn, height, width, ratio)

            # Block on input, but wake often enough that a resize is
            # visible immediately rather than on the next keypress.
            while not select.select([fd], [], [], 0.2)[0]:
                new = measure()
                if resized[0] or new != (height, width):
                    resized[0] = False
                    height, width = new
                    drawn = _render(parent, parent_cursor, entries, cursor,
                                    preview, heading, crumb, drawn, height,
                                    width, ratio)
            key = _read_key(fd)
            if key == "cancel":
                return 0
            if key == "back":
                # At the top level there is nowhere to go, so this is a no-op
                # rather than an exit: leaving on a stray left arrow is rude.
                if len(levels) > 1:
                    levels.pop()
                    cursors.pop()
                    trail.pop()
                continue
            if not entries:
                continue
            if key == "up":
                cursors[-1] = (cursor - 1) % len(entries)
            elif key == "down":
                cursors[-1] = (cursor + 1) % len(entries)
            elif key in ("wider_parent", "narrower_parent",
                         "wider_preview", "narrower_preview"):
                # Weights, not columns: one step is a noticeable move at
                # any window size, and the split survives a resize.
                if key == "wider_parent":
                    ratio[0], ratio[1] = ratio[0] + 1, max(1, ratio[1] - 1)
                elif key == "narrower_parent":
                    ratio[0], ratio[1] = max(1, ratio[0] - 1), ratio[1] + 1
                elif key == "wider_preview":
                    ratio[2], ratio[1] = ratio[2] + 1, max(1, ratio[1] - 1)
                else:
                    ratio[2], ratio[1] = max(1, ratio[2] - 1), ratio[1] + 1
            elif key == "top":
                cursors[-1] = 0
            elif key == "bottom":
                cursors[-1] = len(entries) - 1
            elif key == "open":
                entry = entries[cursor]
                level = _descend(entry)
                if level:
                    levels.append(level)
                    cursors.append(0)
                    trail.append(entry.label)
    finally:
        # Every exit path, exception included: a shell left in raw mode is a
        # far worse outcome than an unfinished browse.
        sys.stdout.write("\x1b[?25h")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        if previous_winch is not None:
            try:
                signal.signal(signal.SIGWINCH, previous_winch)
            except (ValueError, OSError):
                pass
        sys.stdout.flush()
