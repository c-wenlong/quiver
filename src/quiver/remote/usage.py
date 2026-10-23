"""Usage and limits for ``GET /v1/usage``.

The rows are ``swe list --usage --json``'s, built in-process for the
hub's own machine: every active harness with its 100-day session count,
plus the quota reading for each starred one. Rate limits are the only
part that touches the network, and they come through
``get_all_rate_limits`` with its 300 s disk cache, so a phone polling the
page costs a provider call at most once per cache window.

Satellites don't report usage yet, so a MacBook's quota is not here.
"""

from __future__ import annotations

import threading
import time

# A cold fetch takes seconds per provider; two phones opening the page at
# once would otherwise both miss the cache and call every endpoint twice.
_FETCH_LOCK = threading.Lock()

# ``?refresh=1`` refetches readings older than this, and failed ones.
# A full bypass would let a refresh button hammer endpoints that
# rate-limit (Claude's answers 429 with a long Retry-After).
REFRESH_TTL_S = 60.0

WINDOW_DAYS = 100


def snapshot(refresh: bool = False) -> dict:
    """The ``/v1/usage`` body: ``{"harnesses": [...], "window_days": 100}``.

    Raises ``ValueError`` / ``OSError`` when the registry can't be read,
    the same failure ``swe list`` reports.
    """
    from quiver.harness.archive import load_archive
    from quiver.harness.models import HarnessRow
    from quiver.harness.rate_limits import (
        _RATE_LIMIT_FETCH_DEADLINE_TTY,
        get_all_rate_limits,
    )
    from quiver.harness.registry import load_registry_readonly
    from quiver.harness.stars import load_stars
    from quiver.sessions.usage import session_counts

    archived = load_archive()
    tools = {
        name: info for name, info in load_registry_readonly().items()
        if name not in archived
    }
    starred = set(load_stars())
    with _FETCH_LOCK:
        limits = get_all_rate_limits(
            tool_names={name for name in starred if name in tools},
            deadline=_RATE_LIMIT_FETCH_DEADLINE_TTY,
            refresh_failed=refresh,
            cache_ttl=REFRESH_TTL_S if refresh else None,
        )
        counts = session_counts(WINDOW_DAYS)
    return {
        "harnesses": [
            HarnessRow.of(name, info, starred, archived, counts,
                          limits.get(name)).to_json()
            for name, info in tools.items()
        ],
        "window_days": WINDOW_DAYS,
        "server_time": time.time(),
    }
