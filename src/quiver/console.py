"""Terminal output helpers (ANSI colors, padding, truncation)."""

import re

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
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


# --- ANSI-aware word wrapping -------------------------------------------
#
# A pager that wraps with textwrap has to choose between cutting any line
# that carries colour and slicing an escape sequence in half. Neither is
# acceptable once message bodies are rendered rather than printed raw, so
# the wrapper below measures in visible characters and treats an escape as
# a zero-width atom it will never split.

_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
_RESET = COLORS["reset"]


def _sgr_fold(state: tuple, seq: str) -> tuple:
    """The active codes after ``seq`` is applied to ``state``.

    A reset clears everything; anything else stacks, because a bold span
    and a colour span overlap rather than replace one another and both
    have to be re-opened when a line breaks between them.
    """
    if seq in ("\x1b[0m", "\x1b[m"):
        return ()
    return state + (seq,)


def _sgr_atoms(text: str):
    """``text`` as (is_escape, payload) pairs, one per escape or character."""
    out = []
    pos = 0
    for match in _SGR_RE.finditer(text):
        out.extend((False, ch) for ch in text[pos:match.start()])
        out.append((True, match.group(0)))
        pos = match.end()
    out.extend((False, ch) for ch in text[pos:])
    return out


class _Span:
    """A run of characters that wraps as a unit, with its colour context.

    ``before`` is the state in force just ahead of the span, so a line
    starting here can re-open it; ``after`` is the state once the span has
    been emitted, so a line ending here knows whether it needs a reset.
    """

    __slots__ = ("atoms", "space", "before", "after")

    def __init__(self, atoms, space, before, after):
        self.atoms = atoms
        self.space = space
        self.before = before
        self.after = after

    @property
    def text(self) -> str:
        return "".join(payload for _, payload in self.atoms)

    @property
    def width(self) -> int:
        return sum(1 for is_esc, _ in self.atoms if not is_esc)

    def split(self, n: int):
        """Two spans, the first holding ``n`` visible characters.

        Escapes sitting immediately after the cut go to the tail, so the
        head does not end on a code it never uses and the continuation
        line opens with it instead.
        """
        head, tail = [], []
        seen = 0
        state = self.before
        for atom in self.atoms:
            is_esc, payload = atom
            if seen < n:
                head.append(atom)
                if is_esc:
                    state = _sgr_fold(state, payload)
                else:
                    seen += 1
            else:
                tail.append(atom)
        return (_Span(head, self.space, self.before, state),
                _Span(tail, self.space, state, self.after))


def _spans(text: str) -> list:
    """Split a line into wrappable spans of word and whitespace.

    An escape carries no width, so it never opens or closes a span on its
    own: it joins the span it sits inside, and one arriving between spans
    is held over for the next. That matters because whitespace spans are
    dropped at a line break, and a dropped span must not take a colour
    change the following text depends on with it.
    """
    spans: list = []
    atoms: list = []
    pending: list = []
    pending_before: tuple = ()
    start: tuple = ()
    state: tuple = ()
    space = False
    for is_esc, payload in _sgr_atoms(text):
        if is_esc:
            if not pending:
                pending_before = state
            pending.append((True, payload))
            state = _sgr_fold(state, payload)
            continue
        is_space = payload.isspace()
        if atoms and is_space != space:
            spans.append(_Span(atoms, space, start,
                               pending_before if pending else state))
            atoms = []
        if not atoms:
            start = pending_before if pending else state
            atoms, pending = pending, []
            space = is_space
        elif pending:
            atoms.extend(pending)        # an escape inside a word stays put
            pending = []
        atoms.append((False, payload))
    if atoms:
        atoms.extend(pending)            # trailing escapes ride the last span
        spans.append(_Span(atoms, space, start, state))
    return spans


def wrap_ansi(text: str, width: int) -> list[str]:
    """Word-wrap one line to ``width`` visible characters, keeping colour.

    Every returned line satisfies ``visible_len(line) <= width``. Escapes
    cost nothing and are never cut in half. When a break lands inside a
    styled run the line is closed with a reset, so colour cannot bleed
    into whatever the pager draws next, and the run is re-opened at the
    head of the continuation line so a bold cyan sentence stays bold cyan
    across the break.

    Breaks fall on whitespace, following ``textwrap`` on the visible text:
    interior spacing survives, spaces at a break do not, and a word wider
    than the line is cut hard. Indentation is kept on the first line only.
    A blank line stays one blank line rather than vanishing, because the
    pager shows a document and a dropped blank changes its shape.
    """
    if width <= 0:
        return [text]
    if not strip_ansi(text).strip():
        return [""]
    spans = _spans(text)
    if not spans:
        return [""]

    lines: list[str] = []
    i = 0
    while i < len(spans):
        if lines and spans[i].space:
            i += 1                        # a break ate this gap
            continue
        line: list = []
        used = 0
        while i < len(spans) and used + spans[i].width <= width:
            line.append(spans[i])
            used += spans[i].width
            i += 1
        if i < len(spans) and spans[i].width > width and width - used >= 1:
            head, tail = spans[i].split(width - used)
            line.append(head)
            used += head.width
            spans[i] = tail
        if line and line[-1].space:
            line.pop()
        if not line:
            continue
        body = "".join(span.text for span in line)
        lines.append(
            "".join(line[0].before) + body + (_RESET if line[-1].after else "")
        )
    return lines or [""]
