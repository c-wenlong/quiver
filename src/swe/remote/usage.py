"""Usage and limits for ``GET /v1/usage``.

The rows are ``swe list --usage --json``'s, built in-process for the
hub's own machine: every active harness with its 100-day session count,
plus the quota reading for each starred one. Rate limits are the only
part that touches the network, and they come through
``get_all_rate_limits`` with its 300 s disk cache, so a phone polling the
page costs a provider call at most once per cache window.

Satellites run the same ``snapshot`` and post it every
``SATELLITE_PUSH_S`` (``POST /v1/agent/usage``). The hub keeps each
machine's last report on disk under ``satellite-usage/``, so a sleeping
MacBook still shows its last figures, dated, after a hub restart.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from swe.paths import atomic_write_text, remote_dir_for
from swe.remote.catalog import _safe_mid, is_superseded, satellite_files

# A cold fetch takes seconds per provider; two phones opening the page at
# once would otherwise both miss the cache and call every endpoint twice.
_FETCH_LOCK = threading.Lock()

# The hub and satellites poll on their own schedule, like the Mac app, so
# they pin swe's default TTL rather than inherit SWE_RATE_LIMITS_TTL from
# the shell that started them: ``inf`` there would serve one reading
# forever and the page would never move.
CACHE_TTL_S = 300.0

# ``?refresh=1`` refetches readings older than this, and failed ones.
# A full bypass would let a refresh button hammer endpoints that
# rate-limit (Claude's answers 429 with a long Retry-After).
REFRESH_TTL_S = 60.0

WINDOW_DAYS = 100

# How often a satellite reports. The rate-limit cache holds readings for
# 300 s, so pushing more often would only resend the same figures, and
# each push past the TTL costs one call per starred provider.
SATELLITE_PUSH_S = 300.0


def snapshot(refresh: bool = False) -> dict:
    """The ``/v1/usage`` body: ``{"harnesses": [...], "window_days": 100}``.

    Raises ``ValueError`` / ``OSError`` when the registry can't be read,
    the same failure ``swe list`` reports.
    """
    from swe.harness.archive import load_archive
    from swe.harness.models import HarnessRow
    from swe.harness.rate_limits import (
        _RATE_LIMIT_FETCH_DEADLINE_TTY,
        get_all_rate_limits,
    )
    from swe.harness.registry import load_registry_readonly
    from swe.harness.stars import load_stars
    from swe.sessions.usage import session_counts

    archived = load_archive()
    tools = {
        name: info for name, info in load_registry_readonly().items()
        if name not in archived
    }
    starred = set(load_stars())
    no_reading: dict[str, str] = {}
    issues: dict[str, str] = {}
    with _FETCH_LOCK:
        limits = get_all_rate_limits(
            tool_names={name for name in starred if name in tools},
            deadline=_RATE_LIMIT_FETCH_DEADLINE_TTY,
            refresh_failed=refresh,
            cache_ttl=REFRESH_TTL_S if refresh else CACHE_TTL_S,
            no_reading=no_reading,
            issues=issues,
        )
        counts = session_counts(WINDOW_DAYS)
    return {
        "harnesses": [
            HarnessRow.of(name, info, starred, archived, counts,
                          limits.get(name), no_reading.get(name),
                          issue_status=issues.get(name)).to_json()
            for name, info in tools.items()
        ],
        "window_days": WINDOW_DAYS,
        "server_time": time.time(),
    }


def daily_usage(home: Path | None = None) -> dict | None:
    """This machine's tokens and cost per day and harness, for the hub's
    Overview machine filter (#525). The ledger is scanned first (new lines
    only after the first run) and priced from the price list on disk; no
    network. None when the ledger can't be read, so the rest of the usage
    report still goes out."""
    import sqlite3

    from swe.ledger.overview import daily_totals
    from swe.ledger.pricing import load_catalog
    from swe.ledger.scan import scan
    from swe.ledger.store import LedgerStore, ledger_path_for

    try:
        store = LedgerStore(ledger_path_for(home))
    except (OSError, sqlite3.Error):
        return None
    try:
        try:
            scan(store, home)
        except sqlite3.Error:
            store.db.rollback()
        return daily_totals(store, load_catalog(home, download=False))
    except (OSError, sqlite3.Error, ValueError):
        return None
    finally:
        store.close()


# A report's daily rows the hub keeps: 400 days times a few dozen
# harnesses is far below this; a body past it is cut, not refused.
DAILY_ROWS_MAX = 20000


def _clean_daily(value) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("days"), list):
        return None
    rows = [r for r in value["days"] if isinstance(r, dict)][-DAILY_ROWS_MAX:]
    zone = value.get("time_zone")
    return {"time_zone": zone if isinstance(zone, str) else "", "days": rows}


def _satellite_file(home: Path | None, machine_id: str) -> Path:
    return remote_dir_for(home) / "satellite-usage" / f"{_safe_mid(machine_id)}.json"


def save_satellite(home: Path | None, machine_id: str, body: dict) -> dict:
    """Store one satellite's report; returns the stored record.

    ``reported_at`` is the satellite's clock at snapshot time and
    ``received_at`` the hub's, so a client can age the satellite's
    ``fetched_at`` stamps without trusting the two clocks to agree.
    Raises ``ValueError`` on a body without a ``harnesses`` list.
    """
    harnesses = body.get("harnesses")
    if not isinstance(harnesses, list):
        raise ValueError("harnesses must be a list")
    reported_at = body.get("server_time")
    record = {
        "machine_id": machine_id,
        "harnesses": [h for h in harnesses if isinstance(h, dict)],
        "window_days": body.get("window_days") or WINDOW_DAYS,
        "reported_at": (reported_at if isinstance(reported_at, (int, float))
                        and not isinstance(reported_at, bool) else None),
        "received_at": time.time(),
        # Daily token totals per harness (an older satellite sends none).
        "daily_usage": _clean_daily(body.get("daily_usage")),
    }
    path = _satellite_file(home, machine_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(record))
    return record


def load_satellites(home: Path | None) -> list[dict]:
    """Every stored satellite report, newest first."""
    out: list[dict] = []
    for f in satellite_files(remote_dir_for(home) / "satellite-usage"):
        try:
            record = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(record, dict) and record.get("machine_id")
                and isinstance(record.get("harnesses"), list)
                and not is_superseded(f, str(record["machine_id"]))):
            out.append(record)
    out.sort(key=lambda r: r.get("received_at") or 0, reverse=True)
    return out
