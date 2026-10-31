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
    """One hook in the inventory: link state, uses, and which tier owns it.

    ``tier`` is "harness" for a hub script linked per harness, "shared"
    for a ``hooks/shared/`` script deployed through a generated wrapper,
    and "foreign" for a script in a hooks root (or named by a settings
    command) that no ``~/.quiver`` source claims. ``enabled`` is False
    only for a quiver-owned script with a ``.disabled`` marker — foreign
    rows are not ours to switch, so they always read True.
    """

    script: str
    harness: str
    source: Path            # the hub copy (shared/foreign: the real file)
    dest: Path              # where it should land (== source when no root)
    state: str
    detail: str
    declared: list[Declaration] = field(default_factory=list)
    tier: str = "harness"   # shared | harness | foreign
    enabled: bool = True
    # The normalized event names this row's harness can fire (a shared
    # row lists the events the script declared instead).
    events_supported: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HookDrift:
    """One way hooks on this machine disagree with what they declare.

    ``script`` is the basename at issue when there is one, ``settings``
    the files the finding is about: the ones declaring a missing script,
    or the file that should declare a linked one. ``targets`` are the
    missing script tokens themselves (the resolved path when one could
    be computed, else the token as written): two commands can name the
    same basename while only one of them points at nothing, so a
    declared-missing drop matches tokens, never basenames. Doctor turns
    them into the finding's ``source`` and a remedy's undo target."""

    kind: str               # declared-missing | undeclared | unlinked | managed
    harness: str
    message: str
    script: str = ""
    settings: tuple = ()
    targets: tuple = ()
