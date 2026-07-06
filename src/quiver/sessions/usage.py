"""Session usage counters for harness sorting."""

import time

from quiver.sessions.aggregator import get_all_sessions


def session_counts_100d() -> dict[str, int]:
    """Return {tool_name: count} for sessions in the past 100 days."""
    cutoff = (time.time() - 100 * 86400) * 1000
    counts: dict[str, int] = {}
    for session in get_all_sessions(limit=None):
        if session.timestamp >= cutoff:
            counts[session.tool_name] = counts.get(session.tool_name, 0) + 1
    return counts
