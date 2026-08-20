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

from quiver.console import c, elide, terminal_width, truncate, visible_len
from quiver.find.entries import HIDE_DIRS, TEXT_SUFFIXES, Entry

# Past this, reading a file to show a dozen lines costs more than the preview
# is worth, and a minified bundle or a checkpoint will happily be several MB.
MAX_PREVIEW_BYTES = 256 * 1024

FOOTER = "  ↑↓ move · → open · ← back · g/G top, bottom · q quit"


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


def _left_cell(entry: Entry, width: int, active: bool) -> str:
    """One row of the list pane, coloured, exactly ``width`` columns wide."""
    glyph = "▸" if entry.can_descend else "·"
    detail_w = min(len(entry.detail), width // 3) if entry.detail else 0
    name_w = max(1, width - 2 - (detail_w + 1 if detail_w else 0))
    name = elide(entry.label, name_w).ljust(name_w)

    head = f"{glyph} {name}"
    if active:
        body = c("cyan", head)
    elif entry.can_descend:
        body = c("blue", head)
    else:
        body = head
    if detail_w:
        body += " " + c("dim", truncate(entry.detail, detail_w).rjust(detail_w))
    return body


def _render(entries: list[Entry], cursor: int, preview: list[str], title: str,
            crumb: str, prev_lines: int, height: int, width: int) -> int:
    """Draw both panes, returning how many lines were written.

    The caller feeds that count back as ``prev_lines`` so the next redraw
    rewinds by exactly what was drawn. Rows are budgeted one column short of
    the terminal: a line filling the last column leaves some terminals with a
    pending wrap, and one stray wrap puts every later rewind in the wrong place.
    """
    total = len(entries)
    # The panes share their rows, so the taller one sets the count. Sizing to
    # the list alone let a two-row level cut a file preview down to two lines.
    view = min(height, max(total, len(preview), 1))
    top = 0
    if total > view:
        top = max(0, min(cursor - view // 2, total - view))

    avail = max(20, width - 7)           # pointer, two gaps, separator, margin
    left_w = max(20, min(46, avail // 3))
    right_w = max(0, avail - left_w)

    out = []
    if prev_lines:
        out.append(f"\x1b[{prev_lines}A")   # back to the top of the widget
    out.append("\r\x1b[J")                  # and clear everything below it

    out.append(c("bold", truncate(title, width - 1)) + "\r\n")
    out.append(c("dim", elide(crumb, width - 1)) + "\r\n")

    sep = c("dim", "│")
    for row in range(view):
        i = top + row
        pointer = " "
        if i < total:
            pointer = c("cyan", ">") if i == cursor else " "
            left = _left_cell(entries[i], left_w, i == cursor)
        elif not total and not row:
            left = c("dim", "(empty)".ljust(left_w))
        else:
            left = " " * left_w      # a row the preview needs and the list does not
        # The cells are built to width, but padding by what is actually
        # visible keeps the separator column straight if any of them is not.
        left += " " * max(0, left_w - visible_len(left))
        text = preview[row] if row < len(preview) else ""
        right = c("dim", truncate(text, right_w)) if text else ""
        out.append(f" {pointer} {left} {sep} {right}\r\n")

    if total > view:
        out.append(c("dim", f"  {top + 1}–{top + view} of {total}") + "\r\n")
    out.append(c("dim", FOOTER) + "\r\n")

    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return view + 3 + (1 if total > view else 0)


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

    import shutil
    import termios
    import tty

    # Room for the title, breadcrumb, range line, footer, and the shell
    # prompt that follows.
    height = max(5, shutil.get_terminal_size(fallback=(80, 24)).lines - 6)
    width = terminal_width()
    heading = title or "browse"

    levels: list[list[Entry]] = [list(roots)]
    cursors: list[int] = [0]
    trail: list[str] = []

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    drawn = 0
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")     # hide the caret while redrawing
        while True:
            entries = levels[-1]
            cursor = min(cursors[-1], max(0, len(entries) - 1))
            cursors[-1] = cursor
            crumb = "  " + (" / ".join(trail) if trail else "top level")
            preview = _preview(entries[cursor], height) if entries else []
            drawn = _render(entries, cursor, preview, heading, crumb,
                            drawn, height, width)

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
        sys.stdout.flush()
