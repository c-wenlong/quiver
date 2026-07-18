"""Session usage counters for harness sorting."""

import time

from quiver.sessions.aggregator import PARSER_REGISTRY, get_all_sessions
from quiver.sessions.identity import COUNT_TO_REGISTRY, registry_tool

# `_COUNT_TO_REGISTRY` is imported from identity.py for source-of-truth.


def tracked_tool_names() -> set[str]:
    """Tool names that have a session parser (should show 0, not —).

    Returns registry-facing names (after COUNT_TO_REGISTRY mapping).
    """
    names: set[str] = set()
    for name, _parser, _keys in PARSER_REGISTRY:
        names.add(registry_tool(name))
    return names


def session_counts_100d() -> dict[str, int]:
    """Return {registry_tool_name: count} for sessions in the past 100 days.

    Tools with a registered session parser are always present (count may be 0).
    Uses disk cache to avoid re-parsing on every `swe list` invocation.
    """
    cutoff = (time.time() - 100 * 86400) * 1000
    counts: dict[str, int] = {name: 0 for name in tracked_tool_names()}
    for session in get_all_sessions(limit=None, use_cache=True):
        if session.timestamp >= cutoff:
            counts[registry_tool(session.tool_name)] = (
                counts.get(registry_tool(session.tool_name), 0) + 1
            )
    return counts
