"""Terminal output helpers (ANSI colors, padding, truncation)."""

import re

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    # Neon accents for favourited harnesses (xterm-256)
    "neon": "\033[38;5;51m",
    "neon_pink": "\033[38;5;201m",
    "neon_green": "\033[38;5;118m",
}


def c(color: str, text: str) -> str:
    """Wrap text in an ANSI colour, or return it plain if the name is unknown.

    A typo'd colour name used to raise KeyError from inside a print, which
    took down the command over something purely cosmetic.
    """
    code = COLORS.get(color)
    return text if code is None else f"{code}{text}{COLORS['reset']}"


def truncate(text: str, n: int) -> str:
    """Shorten from the right, never returning more than ``n`` characters.

    The ellipsis used to be appended to ``text[:n - 3]``, so any width
    below 3 produced a string longer than the column it had to fit, which
    then pushed every cell on the row out of alignment.
    """
    if n <= 0:
        return ""
    if len(text) <= n:
        return text
    if n <= 3:
        return text[:n]
    return text[: n - 3] + "..."


def elide(text: str, width: int) -> str:
    """Shorten from the middle, keeping both ends, never exceeding ``width``.

    Truncating from the left keeps the filename but throws away which
    harness a path belongs to, so every vendored hit looks the same. Both
    ends carry meaning: the head says whose directory it is, the tail says
    which file.

    Lives here rather than in the find package because two packages render
    paths this way, and reaching across for a private helper made the
    layering worse than the duplication would have.
    """
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "\u2026"
    keep = width - 1                     # one char for the ellipsis
    head = (keep + 1) // 2               # bias to the head on an odd split
    tail = keep - head
    return text[:head] + "\u2026" + (text[-tail:] if tail else "")


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def lpad(text: str, width: int) -> str:
    return strip_ansi(text) + " " * (width - visible_len(text))


def rpad(text: str, width: int) -> str:
    return " " * (width - visible_len(text)) + strip_ansi(text)


def cpad(color: str, text: str, width: int) -> str:
    plain = strip_ansi(text)
    return c(color, plain + " " * (width - len(plain)))


def terminal_width(default: int = 146) -> int:
    """Usable width of the terminal, falling back when it is not a tty.

    Piping to a file or a pager reports 80 from some shells and 0 from
    others, so anything implausible falls back to the default the tables
    were designed against.
    """
    import shutil

    try:
        width = shutil.get_terminal_size(fallback=(default, 24)).columns
    except Exception:
        return default
    return width if width >= 60 else default


def fit_widths(fixed: int, flex: dict[str, int], gap: int = 2,
               minimum: int = 12, cap: int | None = None) -> dict[str, int]:
    """Shrink long-text columns until the row fits the window.

    ``fixed`` is the total width of everything that cannot move (numbers,
    glyphs, pre-padded cells). ``flex`` maps each long-text column to the
    width it would like. Returns what each actually gets.

    Callers that pre-pad their cells have to know the width before they
    build a row, which the table cannot tell them in time, so the budget
    is worked out here and handed in. Room is taken from the widest
    column first, so one long free-text field gives way before several
    short ones, and nothing shrinks below ``minimum``: a column narrowed
    past that carries no information, and a wrapped row is worse than a
    truncated one because it breaks every row after it.
    """
    out = dict(flex)
    if not out:
        return out
    if cap is None:
        cap = terminal_width()
    n_cols = len(out) + (1 if fixed else 0)
    overhead = fixed + gap * max(0, n_cols - 1)
    while sum(out.values()) + overhead > cap:
        name = max(out, key=lambda k: out[k])
        if out[name] <= minimum:
            break
        out[name] -= 1
    return out
