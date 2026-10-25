"""Records the ``swe hooks`` views are built from.

``Declaration`` is one ``{type: command, command: ...}`` entry inside a
harness settings file's ``hooks`` map. ``HookRow`` joins one hub script's
``plan_hooks`` status with the declarations that name it. ``HookDrift`` is
one ``swe hooks check`` finding; ``harness/drift.py`` re-wraps it as a
``Finding`` for ``swe doctor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Declaration:
    """One command a harness will run when ``event`` fires on ``matcher``."""

    harness: str
    event: str
    matcher: str            # "" when the group carries no matcher
    timeout: int | float | None
    command: str
    settings: Path          # the file it was read from

    def label(self) -> str:
        """``PreToolUse:Bash``, or ``Stop`` when no matcher narrows it."""
        return f"{self.event}:{self.matcher}" if self.matcher else self.event


@dataclass
class HookRow:
    """One script under ~/.quiver/hooks/<harness>/, its link state, its uses."""

    script: str
    harness: str
    source: Path            # the hub copy
    dest: Path              # where it should land (== source when no root)
    state: str
    detail: str
    declared: list[Declaration] = field(default_factory=list)


@dataclass(frozen=True)
class HookDrift:
    """One way hooks on this machine disagree with what they declare."""

    kind: str               # declared-missing | undeclared | unlinked
    harness: str
    message: str
