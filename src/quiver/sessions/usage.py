"""Session usage counters for harness sorting."""

import json
import os
import time

from quiver.paths import SESSION_COUNTS_CACHE_FILE
from quiver.sessions.aggregator import PARSER_REGISTRY, get_all_sessions
from quiver.sessions.identity import COUNT_TO_REGISTRY, registry_tool

# A day. The session cache underneath is 60 seconds, which is right for
# `swe session` where you want to see the run you just finished, but it made
# `swe list` re-parse every transcript on the machine once a minute for a
# number that moves by one or two a day. Override with SWE_SESSION_COUNTS_TTL.
_COUNTS_TTL_DEFAULT = 24 * 60 * 60


def _counts_ttl() -> float:
    raw = os.environ.get("SWE_SESSION_COUNTS_TTL")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return float(_COUNTS_TTL_DEFAULT)


def _load_cached_counts() -> dict[str, int] | None:
    try:
        data = json.loads(SESSION_COUNTS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - float(data.get("cached_at", 0)) > _counts_ttl():
        return None
    counts = data.get("counts")
    if not isinstance(counts, dict):
        return None
    return {str(k): int(v) for k, v in counts.items()}


def _save_cached_counts(counts: dict[str, int]) -> None:
    try:
        SESSION_COUNTS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSION_COUNTS_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"cached_at": time.time(), "counts": counts}),
            encoding="utf-8")
        tmp.replace(SESSION_COUNTS_CACHE_FILE)
    except OSError:
        pass


def invalidate_counts_cache() -> None:
    """Drop the cached counts so the next call re-parses."""
    try:
        SESSION_COUNTS_CACHE_FILE.unlink()
    except OSError:
        pass

# `_COUNT_TO_REGISTRY` is imported from identity.py for source-of-truth.


def tracked_tool_names() -> set[str]:
    """Tool names that have a session parser (should show 0, not —).

    Returns registry-facing names (after COUNT_TO_REGISTRY mapping).
    """
    names: set[str] = set()
    for name, _parser, _keys in PARSER_REGISTRY:
        names.add(registry_tool(name))
    return names


def session_counts_100d(use_cache: bool = True) -> dict[str, int]:
    """Return {registry_tool_name: count} for sessions in the past 100 days.

    Tools with a registered session parser are always present (count may be 0).
    Cached for a day: computing it walks every transcript on the machine and
    costs ~570ms, for a figure that changes by a handful per day.
    """
    if use_cache:
        cached = _load_cached_counts()
        if cached is not None:
            # A harness added since the cache was written should read 0,
            # not vanish from the table.
            for name in tracked_tool_names():
                cached.setdefault(name, 0)
            return cached

    cutoff = (time.time() - 100 * 86400) * 1000
    counts: dict[str, int] = {name: 0 for name in tracked_tool_names()}
    for session in get_all_sessions(limit=None, use_cache=True):
        if session.timestamp >= cutoff:
            counts[registry_tool(session.tool_name)] = (
                counts.get(registry_tool(session.tool_name), 0) + 1
            )
    _save_cached_counts(counts)
    return counts
