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
    Column("sess", "100d", "Sessions in the last 100 days"),
    Column("rate", "REMAINING", "Rate limit left", costly=True),
    Column("agents", "AGENTS.MD", "Is its instruction file synced to ~/.quiver"),
    Column("skills", "SKILLS", "Is its skills root synced to ~/.quiver"),
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
