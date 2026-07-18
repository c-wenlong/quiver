"""Favourite / starred harnesses (persisted separately from tools.json)."""

from __future__ import annotations

import json

from quiver.paths import CONFIG_DIR, STARS_FILE


def load_stars() -> list[str]:
    """Return starred harness names in pin order (first = top)."""
    if not STARS_FILE.exists():
        return []
    try:
        with open(STARS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [str(x) for x in data if isinstance(x, str) and x]
    if isinstance(data, dict) and isinstance(data.get("stars"), list):
        return [str(x) for x in data["stars"] if isinstance(x, str) and x]
    return []


def save_stars(stars: list[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # De-dupe while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for name in stars:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    with open(STARS_FILE, "w") as f:
        json.dump(ordered, f, indent=2)
        f.write("\n")


def is_starred(name: str, stars: list[str] | None = None) -> bool:
    if stars is None:
        stars = load_stars()
    return name in stars


def star(name: str) -> bool:
    """Add name to the front of the stars list. Returns True if newly starred."""
    stars = load_stars()
    if name in stars:
        stars.remove(name)
        stars.insert(0, name)
        save_stars(stars)
        return False
    stars.insert(0, name)
    save_stars(stars)
    return True


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
