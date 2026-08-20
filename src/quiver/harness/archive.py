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

# How much the harness actually got used before it was shelved. An
# enumeration rather than a raw number for three reasons: the number
# already exists in the session count, so storing it again would just go
# stale; eight of the archived harnesses have no session parser, so their
# count is unknown rather than zero, and a number cannot say that; and a
# free-text field would duplicate `reason`, which already carries the
# nuance and is where "might use in future" belongs.
#
# Ordered least to most, so a caller can compare them.
USAGE_LEVELS: tuple[str, ...] = ("unknown", "none", "trial", "used", "heavy")

USAGE_ABOUT = {
    "unknown": "no session parser, so quiver cannot tell",
    "none": "never actually ran it",
    "trial": "a handful of runs, not a real evaluation",
    "used": "used properly for a while",
    "heavy": "was a daily driver",
}

# Lifetime session counts to level. The boundaries are judgement calls, so
# they are one place rather than scattered through the renderer.
_USAGE_THRESHOLDS = ((50, "heavy"), (10, "used"), (1, "trial"), (0, "none"))


def usage_from_sessions(count: int | None) -> str:
    """Derive a level from lifetime sessions. None means no parser exists."""
    if count is None:
        return "unknown"
    for floor, level in _USAGE_THRESHOLDS:
        if count >= floor:
            return level
    return "none"


def normalise_usage(value: object, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    return text if text in USAGE_LEVELS else fallback


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
            out[name] = {"reason": entry, "archived_at": "", "usage": "unknown"}
        elif isinstance(entry, dict):
            # An absent key is not the same as a recorded "unknown": entries
            # written before this field existed get the level their session
            # history implies, rather than all reading as unknown forever.
            level = (normalise_usage(entry["usage"]) if "usage" in entry
                     else _derive_usage(name))
            out[name] = {
                "reason": str(entry.get("reason") or ""),
                "archived_at": str(entry.get("archived_at") or ""),
                "usage": level,
            }
    return out


def save_archive(entries: dict[str, dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        name: {
            "reason": str(e.get("reason") or ""),
            "archived_at": str(e.get("archived_at") or ""),
            "usage": normalise_usage(e.get("usage")),
        }
        for name, e in sorted(entries.items())
    }
    from quiver.paths import atomic_write_text

    atomic_write_text(ARCHIVE_FILE, json.dumps(payload, indent=2) + "\n")


def is_archived(name: str, entries: dict[str, dict] | None = None) -> bool:
    if entries is None:
        entries = load_archive()
    return name in entries


def archive(name: str, reason: str = "", when: str | None = None,
            usage: str | None = None) -> dict:
    """Archive ``name``, returning the stored entry.

    Re-archiving something already archived updates the reason and restamps
    it, so correcting a note does not need a restore first.

    ``usage`` defaults to whatever the lifetime session count implies, which
    keeps the field honest without asking for it on every archive. It is
    still settable, because the count can be wrong in both directions: a
    harness with no parser reads as unknown however much you used it, and a
    high count can come from one long evaluation rather than real adoption.
    """
    entries = load_archive()
    entry = {
        "reason": reason.strip(),
        "archived_at": when or datetime.now().isoformat(timespec="seconds"),
        "usage": normalise_usage(usage) if usage else "",
    }
    # Keep an existing reason when re-archiving without giving a new one.
    if not entry["reason"] and name in entries:
        entry["reason"] = entries[name]["reason"]
    if not entry["usage"]:
        entry["usage"] = (entries[name]["usage"] if name in entries
                          else _derive_usage(name))
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


def _derive_usage(name: str) -> str:
    """Best guess at how much ``name`` was used, from its session history."""
    try:
        from quiver.sessions.usage import session_counts

        return usage_from_sessions(session_counts(None).get(name))
    except Exception:
        # A failure to read history must not block archiving.
        return "unknown"
