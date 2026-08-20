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


# Tools whose parser failed on the run behind the current counts. A crashed
# parser yields zero sessions, which is indistinguishable from a harness you
# genuinely have not used, so the two must not render the same.
_CACHED_BROKEN: set[str] = set()


def broken_tools() -> set[str]:
    """Tools whose session count is unknown because their parser failed."""
    from quiver.sessions import failures

    return set(_CACHED_BROKEN) | set(failures.snapshot())


def _window_key(days: int | None) -> str:
    """Cache key for a window. None means every session ever."""
    return "all" if days is None else str(int(days))


def _read_cache() -> dict:
    try:
        data = json.loads(SESSION_COUNTS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_cached_counts(days: int | None) -> dict[str, int] | None:
    # Keyed by window: switching 100d to 30d must not read the old number.
    entry = (_read_cache().get("windows") or {}).get(_window_key(days))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("cached_at", 0)) > _counts_ttl():
        return None
    counts = entry.get("counts")
    if not isinstance(counts, dict):
        return None
    broken = entry.get("broken")
    if isinstance(broken, list):
        # A parser that crashed produced a zero that means "unknown", not
        # "none". Remembering which ones those were keeps a cached read as
        # honest as a fresh one for the whole day the entry lives.
        _CACHED_BROKEN.clear()
        _CACHED_BROKEN.update(str(b) for b in broken)
    return {str(k): int(v) for k, v in counts.items()}


def _save_cached_counts(days: int | None, counts: dict[str, int],
                        broken: set[str] | None = None) -> None:
    try:
        data = _read_cache()
        windows = dict(data.get("windows") or {})
        windows[_window_key(days)] = {
            "cached_at": time.time(),
            "counts": counts,
            "broken": sorted(broken or ()),
        }
        SESSION_COUNTS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SESSION_COUNTS_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"windows": windows}), encoding="utf-8")
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


def session_counts(days: int | None = 100, use_cache: bool = True) -> dict[str, int]:
    """{registry_tool_name: count} for sessions inside ``days``.

    ``days=None`` counts every session ever recorded. Tools with a registered
    session parser are always present, count may be 0. Cached per window for a
    day: computing it walks every transcript on the machine and costs ~500ms,
    for a figure that changes by a handful per day.
    """
    if use_cache:
        cached = _load_cached_counts(days)
        if cached is not None:
            # A harness added since the cache was written should read 0,
            # not vanish from the table.
            for name in tracked_tool_names():
                cached.setdefault(name, 0)
            return cached

    from quiver.sessions import failures

    failures.clear()
    cutoff = 0.0 if days is None else (time.time() - days * 86400) * 1000
    counts: dict[str, int] = {name: 0 for name in tracked_tool_names()}
    for session in get_all_sessions(limit=None, use_cache=True):
        if session.timestamp >= cutoff:
            counts[registry_tool(session.tool_name)] = (
                counts.get(registry_tool(session.tool_name), 0) + 1
            )
    broke = set(failures.snapshot())
    _CACHED_BROKEN.clear()
    _CACHED_BROKEN.update(broke)
    _save_cached_counts(days, counts, broke)
    return counts


def session_counts_100d(use_cache: bool = True) -> dict[str, int]:
    """Backwards-compatible alias for the default window."""
    return session_counts(100, use_cache=use_cache)
