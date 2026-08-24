"""Archived harnesses: tried, judged a poor fit, kept out of the way.

Deliberately not a delete. `swe remove` forgets a harness, which loses the
fact that you evaluated it, so a month later it looks like something you
have never tried and gets reinstalled. Archiving keeps the verdict: what
you archived, when, and why.

Compatibility shim. Archive state used to live in its own file,
archived.json, keyed by name; it now lives on each harness's own row in
config/harness.json as `state: "archived"` plus an `archived` object holding
reason/archived_at/usage — see `quiver.harness.registry` for the schema and
the one place that touches the file. This module is kept, and every
function below keeps its old signature and return shape, because `swe hs
archive` and a handful of other callers still import it by name.
"""

from __future__ import annotations

from datetime import datetime

from quiver.harness import registry as _registry

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
    """Map of harness name -> {"reason": str, "archived_at": iso8601, "usage": str}.

    A malformed registry reads as empty rather than raising: an unreadable
    archive should hide nothing, which fails toward showing you more.
    """
    try:
        reg = _registry.load_registry()
    except (OSError, ValueError):
        return {}
    if not isinstance(reg, dict):
        return {}

    out: dict[str, dict] = {}
    for name in _registry.archived_names(reg):
        entry = reg[name].get("archived")
        if isinstance(entry, str):
            # Tolerate a bare reason string, in case one is hand-edited in.
            entry = {"reason": entry}
        entry = entry if isinstance(entry, dict) else {}
        out[name] = {
            "reason": str(entry.get("reason") or ""),
            "archived_at": str(entry.get("archived_at") or ""),
            "usage": normalise_usage(entry.get("usage")) if "usage" in entry
                     else _derive_usage(name),
        }
    return out


def save_archive(entries: dict[str, dict]) -> None:
    """Replace the archived set wholesale.

    Anything currently archived but missing from ``entries`` is restored to
    active; a name with no other registry data left after that is removed
    outright rather than kept as an empty row.
    """
    reg = _registry.load_registry()
    wanted = set(entries)
    for name in _registry.archived_names(reg):
        if name not in wanted:
            _unset_archived(reg, name)
    for name, entry in sorted(entries.items()):
        row = reg.setdefault(name, {})
        row["state"] = "archived"
        row.pop("pin", None)  # archived and starred are exclusive
        row["archived"] = {
            "reason": str(entry.get("reason") or ""),
            "archived_at": str(entry.get("archived_at") or ""),
            "usage": normalise_usage(entry.get("usage")),
        }
    _registry.save_registry(reg)


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
    reg = _registry.load_registry()
    existing = reg.get(name, {}).get("archived") or {}
    entry = {
        "reason": reason.strip(),
        "archived_at": when or datetime.now().isoformat(timespec="seconds"),
        "usage": normalise_usage(usage) if usage else "",
    }
    # Keep an existing reason when re-archiving without giving a new one.
    if not entry["reason"]:
        entry["reason"] = existing.get("reason", "")
    if not entry["usage"]:
        entry["usage"] = existing.get("usage") or _derive_usage(name)

    row = dict(reg.get(name, {}))
    row["state"] = "archived"
    row.pop("pin", None)  # archived and starred are exclusive
    row["archived"] = entry
    reg[name] = row
    _registry.save_registry(reg)
    return entry


def unarchive(name: str) -> dict | None:
    """Restore ``name``, returning the entry it had, or None if not archived.

    The caller gets the old entry back so it can show what is being
    discarded. Dropping a reason and a date without saying so would make
    the record quietly unreliable.
    """
    reg = _registry.load_registry()
    row = reg.get(name)
    if row is None or _registry.state_of(row) != "archived":
        return None
    entry = row.get("archived") or {}
    _unset_archived(reg, name)
    _registry.save_registry(reg)
    return {
        "reason": str(entry.get("reason") or ""),
        "archived_at": str(entry.get("archived_at") or ""),
        "usage": normalise_usage(entry.get("usage")),
    }


def _unset_archived(reg: dict, name: str) -> None:
    """Drop archived state from ``name``, removing the row entirely if that
    was the only thing it held — a bare ``{"state": ..., "archived": ...}``
    row is nothing but archive bookkeeping for a name outside the catalog."""
    row = reg.get(name)
    if row is None:
        return
    row.pop("state", None)
    row.pop("archived", None)
    if not row:
        del reg[name]


def _derive_usage(name: str) -> str:
    """Best guess at how much ``name`` was used, from its session history."""
    try:
        from quiver.sessions.usage import session_counts

        return usage_from_sessions(session_counts(None).get(name))
    except Exception:
        # A failure to read history must not block archiving.
        return "unknown"
