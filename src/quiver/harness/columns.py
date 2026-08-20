"""Which columns `swe list` shows, and where that choice is stored.

The table was three hardcoded layouts chosen by flag. This turns the column
set into configuration so `swe list edit` can change it, while --usage and
--links still work as one-off overrides.
"""

from __future__ import annotations

from dataclasses import dataclass

from quiver.configuration import load_config, save_config


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    about: str
    locked: bool = False      # always shown, cannot be turned off
    costly: bool = False      # needs a network fetch


# Order here is the order they render in.
COLUMNS: tuple[Column, ...] = (
    Column("mark", "★", "Favourite marker", locked=True),
    Column("name", "NAME", "Harness name", locked=True),
    Column("command", "COMMAND", "The binary you actually run"),
    Column("version", "VERSION", "Installed version"),
    Column("aliases", "ALIASES", "Short aliases, e.g. cc for claude"),
    Column("inst", "INST", "Whether it is installed"),
    Column("sess", "100d", "Sessions in a window you can rotate"),
    Column("rate", "REMAINING", "Rate limit left", costly=True),
    Column("agents", "AGENTS.MD", "Is its instruction file synced to ~/.quiver"),
    Column("skills", "SKILLS", "Is its skills root synced to ~/.quiver"),
    Column("usage", "USAGE", "How much an archived harness got used"),
    Column("archived", "ARCHIVED", "When an archived harness was shelved"),
    Column("reason", "REASON", "Why an archived harness was shelved"),
    Column("desc", "DESCRIPTION", "One-line description"),
)

BY_KEY = {c.key: c for c in COLUMNS}
LOCKED = tuple(c.key for c in COLUMNS if c.locked)

# What you get before configuring anything: the old default view.
DEFAULT_COLUMNS: tuple[str, ...] = (
    "mark", "name", "command", "version", "aliases", "inst", "desc",
)

_CONFIG_SECTION = "list"
_CONFIG_KEY = "columns"
_WINDOW_KEY = "session_window"

# How far back the session-count column looks. None means every session ever.
SESSION_WINDOWS: tuple[int | None, ...] = (7, 30, 100, 365, None)
DEFAULT_WINDOW = 100


def window_label(days: int | None) -> str:
    return "All" if days is None else f"{days}d"


def load_window() -> int | None:
    """The configured session window, or the default when unset."""
    section = (load_config() or {}).get(_CONFIG_SECTION) or {}
    if _WINDOW_KEY not in section:
        return DEFAULT_WINDOW
    value = section[_WINDOW_KEY]
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW
    return value if value in SESSION_WINDOWS else DEFAULT_WINDOW


def save_window(days: int | None) -> int | None:
    """Persist the session window, ignoring values outside the rotation."""
    if days not in SESSION_WINDOWS:
        days = DEFAULT_WINDOW
    config = dict(load_config() or {})
    section = dict(config.get(_CONFIG_SECTION) or {})
    section[_WINDOW_KEY] = days
    config[_CONFIG_SECTION] = section
    save_config(config)
    return days


def next_window(days: int | None, step: int = 1) -> int | None:
    """The next window in the rotation, wrapping at both ends."""
    try:
        i = SESSION_WINDOWS.index(days)
    except ValueError:
        i = SESSION_WINDOWS.index(DEFAULT_WINDOW)
    return SESSION_WINDOWS[(i + step) % len(SESSION_WINDOWS)]


def normalise(keys) -> list[str]:
    """Drop unknown keys, force the locked ones in, keep declared order."""
    wanted = {k for k in (keys or []) if k in BY_KEY} | set(LOCKED)
    return [c.key for c in COLUMNS if c.key in wanted]


def load_columns() -> list[str]:
    """The configured column set, or the default when unset."""
    section = (load_config() or {}).get(_CONFIG_SECTION) or {}
    configured = section.get(_CONFIG_KEY)
    if not configured:
        return list(DEFAULT_COLUMNS)
    return normalise(configured)


def save_columns(keys) -> list[str]:
    """Persist a column set, returning what was actually written."""
    resolved = normalise(keys)
    config = dict(load_config() or {})
    section = dict(config.get(_CONFIG_SECTION) or {})
    section[_CONFIG_KEY] = resolved
    config[_CONFIG_SECTION] = section
    save_config(config)
    return resolved
