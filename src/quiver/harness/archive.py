"""Archived harnesses: tried, judged a poor fit, kept out of the way.

Deliberately not a delete. `swe remove` forgets a harness, which loses the
fact that you evaluated it, so a month later it looks like something you
have never tried and gets reinstalled. Archiving keeps the verdict: what
you archived, when, and why.

Stored separately from tools.json for the same reason stars are. The
registry describes what a harness *is*; this records what you decided
about it.
"""

from __future__ import annotations

import json
from datetime import datetime

from quiver.paths import ARCHIVE_FILE, CONFIG_DIR


def load_archive() -> dict[str, dict]:
    """Map of harness name -> {"reason": str, "archived_at": iso8601}.

    A malformed file reads as empty rather than raising: an unreadable
    archive should hide nothing, which fails toward showing you more.
    """
    if not ARCHIVE_FILE.exists():
        return {}
    try:
        with open(ARCHIVE_FILE) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, dict] = {}
    for name, entry in data.items():
        if not isinstance(name, str) or not name:
            continue
        if isinstance(entry, str):
            # Tolerate a bare reason string, in case one is hand-edited in.
            out[name] = {"reason": entry, "archived_at": ""}
        elif isinstance(entry, dict):
            out[name] = {
                "reason": str(entry.get("reason") or ""),
                "archived_at": str(entry.get("archived_at") or ""),
            }
    return out


def save_archive(entries: dict[str, dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        name: {
            "reason": str(e.get("reason") or ""),
            "archived_at": str(e.get("archived_at") or ""),
        }
        for name, e in sorted(entries.items())
    }
    from quiver.paths import atomic_write_text

    atomic_write_text(ARCHIVE_FILE, json.dumps(payload, indent=2) + "\n")


def is_archived(name: str, entries: dict[str, dict] | None = None) -> bool:
    if entries is None:
        entries = load_archive()
    return name in entries


def archive(name: str, reason: str = "", when: str | None = None) -> dict:
    """Archive ``name``, returning the stored entry.

    Re-archiving something already archived updates the reason and restamps
    it, so correcting a note does not need a restore first.
    """
    entries = load_archive()
    entry = {
        "reason": reason.strip(),
        "archived_at": when or datetime.now().isoformat(timespec="seconds"),
    }
    # Keep an existing reason when re-archiving without giving a new one.
    if not entry["reason"] and name in entries:
        entry["reason"] = entries[name]["reason"]
    entries[name] = entry
    save_archive(entries)
    return entry


def unarchive(name: str) -> dict | None:
    """Restore ``name``, returning the entry it had, or None if not archived.

    The caller gets the old entry back so it can show what is being
    discarded. Dropping a reason and a date without saying so would make
    the record quietly unreliable.
    """
    entries = load_archive()
    entry = entries.pop(name, None)
    if entry is None:
        return None
    save_archive(entries)
    return entry
