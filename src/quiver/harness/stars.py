"""Favourite / starred harnesses.

Compatibility shim. Star state used to live in its own file, stars.json, as
an ordered list of names; it now lives on each harness's own row in
config/harness.json as `state: "starred"` plus a `pin` (1 = top) — see
`quiver.harness.registry` for the schema and the one place that touches the
file. This module is kept, and every function below keeps its old signature
and return shape, because `swe star` and a handful of other callers still
import it by name.
"""

from __future__ import annotations

from quiver.harness import registry as _registry


def load_stars() -> list[str]:
    """Return starred harness names in pin order (first = top)."""
    return _registry.starred_names(_registry.load_registry())


def save_stars(stars: list[str]) -> None:
    """Replace the starred set and its pin order wholesale.

    De-dupes while preserving order, same as the old file-backed version.
    Anything currently starred but missing from ``stars`` drops back to
    active; a name with no other registry data left after that is removed
    outright rather than kept as an empty row.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for name in stars:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)

    reg = _registry.load_registry()
    wanted = set(ordered)
    for name in _registry.starred_names(reg):
        if name not in wanted:
            _unset_starred(reg, name)
    for pin, name in enumerate(ordered, start=1):
        row = reg.setdefault(name, {})
        row["state"] = "starred"
        row["pin"] = pin
        row.pop("archived", None)  # starring wins over a stale archive record
    _registry.save_registry(reg)


def is_starred(name: str, stars: list[str] | None = None) -> bool:
    if stars is None:
        stars = load_stars()
    return name in stars


def star(name: str) -> bool:
    """Add name to the front of the stars list. Returns True if newly starred."""
    stars = load_stars()
    was_starred = name in stars
    if was_starred:
        stars.remove(name)
    stars.insert(0, name)
    save_stars(stars)
    return not was_starred


def unstar(name: str) -> bool:
    """Remove name from stars. Returns True if it was starred."""
    stars = load_stars()
    if name not in stars:
        return False
    stars = [s for s in stars if s != name]
    save_stars(stars)
    return True


def toggle_star(name: str) -> bool:
    """Toggle star. Returns True if now starred, False if unstarred."""
    stars = load_stars()
    if name in stars:
        unstar(name)
        return False
    star(name)
    return True


def star_rank(name: str, stars: list[str] | None = None) -> int | None:
    """0-based rank among stars, or None if not starred."""
    if stars is None:
        stars = load_stars()
    try:
        return stars.index(name)
    except ValueError:
        return None


def _unset_starred(reg: dict, name: str) -> None:
    """Drop starred state from ``name``, removing the row entirely if that
    was the only thing it held — a bare ``{"state": ..., "pin": ...}`` row
    is nothing but star bookkeeping for a name outside the catalog."""
    row = reg.get(name)
    if row is None:
        return
    row.pop("state", None)
    row.pop("pin", None)
    if not row:
        del reg[name]
