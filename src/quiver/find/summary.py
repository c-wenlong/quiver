"""The short form every `swe find` view prints unless `--full` is given.

One bold title per section, padded to a shared width, then a comma-separated
tally of how many rows sit in each state and which harnesses those are:

    Harness roots  8 synced (claude, codex, cursor, …), 1 unregistered (windsurf)

Same look as `swe init`'s summary. Counts are always printed; the name lists
are what gives way when the line would overflow the terminal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quiver import console
from quiver.console import c, elide

ELLIPSIS = "…"

# A project path can be longer than the whole line budget. Eliding each name
# from the middle keeps both ends readable and leaves room for its neighbours.
NAME_WIDTH = 32


@dataclass
class Group:
    """One clause of a tally: `8 synced (claude, codex, …)`."""

    count: int
    word: str
    colour: str = "dim"
    names: list[str] = field(default_factory=list)


def _dedupe(names) -> list[str]:
    """Names in first-seen order, each once: two files under ~/.claude are
    both "claude", and the count already says there are two."""
    seen: dict[str, None] = {}
    for name in names:
        if name:
            seen.setdefault(elide(name, NAME_WIDTH), None)
    return list(seen)


def _names_text(names: list[str], keep: int) -> str:
    if keep <= 0 or not names:
        return ""
    shown = names[:keep]
    if keep < len(names):
        shown = shown + [ELLIPSIS]
    return " (" + ", ".join(shown) + ")"


def tally(groups: list[Group], width: int, empty: str = "none") -> str:
    """Render groups as one line no wider than ``width`` visible columns.

    Empty groups are dropped. When the line is too long, the longest name
    list loses a name first, so the long "synced" run is trimmed before the
    one row that is actually in the way loses its name. A list trimmed to
    nothing drops its parentheses; the counts are never cut.
    """
    groups = [g for g in groups if g.count]
    if not groups:
        return c("dim", empty)
    names = [_dedupe(g.names) for g in groups]
    keep = [len(n) for n in names]

    def plain() -> int:
        return len(", ".join(
            f"{g.count} {g.word}{_names_text(n, k)}"
            for g, n, k in zip(groups, names, keep)
        ))

    while plain() > width and any(keep):
        # Ties go to the earlier group: settled states come first and are
        # the ones whose names matter least.
        i = max(range(len(keep)), key=lambda j: (keep[j], -j))
        keep[i] -= 1

    parts = []
    for g, n, k in zip(groups, names, keep):
        text = _names_text(n, k)
        parts.append(c(g.colour, f"{g.count} {g.word}") + (c("dim", text) if text else ""))
    return ", ".join(parts)


def print_sections(sections: list[tuple[str, list[Group], str]],
                   indent: str = "  ") -> None:
    """Print `Title  tally` per section, titles padded to the longest one."""
    if not sections:
        return
    width = max(len(title) for title, _, _ in sections)
    budget = max(20, console.terminal_width() - len(indent) - width - 2)
    for title, groups, empty in sections:
        print(f"{indent}{c('bold', title.ljust(width))}  {tally(groups, budget, empty)}")


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
