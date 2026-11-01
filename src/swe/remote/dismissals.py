"""Inbox dismissals, one set per paired device.

A phone clears an inbox row with ``POST /v1/inbox/dismiss``; the hub
keeps ``device_id → key → {last_active_at, dismissed_at}`` in
``inbox_dismissals.json`` beside ``devices.json`` (0600). A dismissal
hides the item only while its ``last_active_at`` is no newer than the
one the phone saw, so new activity brings it back.

Entries prune by when they were dismissed, not by the session's age:
once an item is past the inbox cutoff it can't show anyway, so a month
of dismissals is more than enough.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

from swe.paths import atomic_write_text, remote_dir_for

_BASENAME = "inbox_dismissals.json"

# Kept this long after the dismissal; far past the inbox's own cutoff.
KEEP_S = 30 * 86_400
# Per-device cap, newest dismissals win: bounds the file against a
# client that dismisses in a loop.
MAX_PER_DEVICE = 2000
MAX_KEY_LEN = 512

# The hub is the only writer; this covers its concurrent handlers.
_lock = threading.Lock()


def dismissals_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(Path(home) if home is not None else None) / _BASENAME


def _load_all(home: Path | str | None) -> dict[str, dict[str, dict]]:
    try:
        data = json.loads(dismissals_file(home).read_text(encoding="utf-8"))
        devices = data.get("devices") if isinstance(data, dict) else None
    except Exception:
        return {}
    if not isinstance(devices, dict):
        return {}
    return {
        str(did): rows for did, rows in devices.items()
        if isinstance(rows, dict)
    }


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _prune(rows: dict[str, dict], now: float) -> dict[str, dict]:
    live = {
        k: v for k, v in rows.items()
        if isinstance(v, dict)
        and now - _num(v.get("dismissed_at")) < KEEP_S
    }
    if len(live) > MAX_PER_DEVICE:
        newest = sorted(
            live.items(), key=lambda kv: _num(kv[1].get("dismissed_at")),
            reverse=True,
        )[:MAX_PER_DEVICE]
        live = dict(newest)
    return live


def valid(key: object, last_active_at: object) -> bool:
    """Whether a dismiss body's fields are usable."""
    if not (isinstance(key, str) and 0 < len(key) <= MAX_KEY_LEN):
        return False
    if isinstance(last_active_at, bool) or not isinstance(
        last_active_at, (int, float)
    ):
        return False
    try:
        # JSON integers are unbounded; past a float's range they overflow.
        return math.isfinite(float(last_active_at))
    except OverflowError:
        return False


def dismiss(
    home: Path | str | None,
    device_id: str,
    key: str,
    last_active_at: float,
    now: float | None = None,
) -> None:
    """Record that ``device_id`` cleared ``key`` as of ``last_active_at``."""
    now = time.time() if now is None else now
    with _lock:
        devices = _load_all(home)
        rows = devices.get(device_id, {})
        prev = rows.get(key)
        if not isinstance(prev, dict):
            prev = {}
        # A stale retry never un-hides activity a newer dismiss covered.
        seen = max(float(last_active_at), _num(prev.get("last_active_at")))
        rows[key] = {"last_active_at": seen, "dismissed_at": now}
        devices[device_id] = rows
        # Every device prunes on each write, so an idle or revoked one's
        # rows don't outlive KEEP_S just because nobody touched them.
        devices = {d: _prune(r, now) for d, r in devices.items()}
        devices = {d: r for d, r in devices.items() if r}
        atomic_write_text(
            dismissals_file(home),
            json.dumps({"devices": devices}, indent=1),
            private=True,
        )


def for_device(
    home: Path | str | None, device_id: str, now: float | None = None
) -> dict[str, float]:
    """``key → last_active_at`` this device has dismissed."""
    now = time.time() if now is None else now
    rows = _prune(_load_all(home).get(device_id, {}), now)
    return {k: _num(v.get("last_active_at")) for k, v in rows.items()}
