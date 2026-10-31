"""Per-device last-read cursors: the session rows' ``unread`` flag.

A client opening a chat posts ``POST /v1/sessions/<key>/read``; the hub
keeps ``device_id → key → at`` in ``session_reads.json`` beside
``devices.json`` (0600). Every session row served to that device then
carries ``unread: true`` when the row's ``last_active_at`` is newer than
the device's cursor for it.

``last_active_at`` is when the transcript last moved, which is the
agent's reply in practice: a user turn is followed by one. A session
this device never opened is unread only when it moved after the
device's baseline, the later of when it paired (``created_at``, or a
tailnet sign-in's ``first_seen_at``) and when this store began
(``since``, written on the first listing after the hub learned the
feature). So pairing, or upgrading the hub, doesn't light up every old
row.

Bounded: each write drops cursors for sessions the catalog no longer
lists and for devices that were revoked or unpaired, then keeps the
newest ``MAX_PER_DEVICE`` per device.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Iterable

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import pairing
from quiver.remote.devices import TAILNET_PREFIX

_BASENAME = "session_reads.json"

MAX_PER_DEVICE = 5000

# The hub is the only writer; this covers its concurrent handlers.
_lock = threading.Lock()


def reads_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(Path(home) if home is not None else None) / _BASENAME


def num(value: object) -> float:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _load(home: Path | str | None) -> dict:
    """``{"since": float | None, "devices": {did: {key: at}}}``."""
    try:
        data = json.loads(reads_file(home).read_text(encoding="utf-8"))
    except Exception:
        data = None
    if not isinstance(data, dict):
        return {"since": None, "devices": {}}
    devices = data.get("devices")
    out: dict[str, dict[str, float]] = {}
    if isinstance(devices, dict):
        for did, rows in devices.items():
            if isinstance(rows, dict):
                out[str(did)] = {
                    str(k): num(v) for k, v in rows.items()
                }
    since = data.get("since")
    return {"since": num(since) if since is not None else None,
            "devices": out}


def _save(home: Path | str | None, state: dict) -> None:
    atomic_write_text(
        reads_file(home),
        json.dumps({"since": state["since"], "devices": state["devices"]},
                   indent=1),
        private=True,
    )


def valid_at(value: object) -> bool:
    """Whether a read body's optional ``at`` is usable (absent is)."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        # JSON integers are unbounded; past a float's range they overflow.
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _since(home: Path | str | None, now: float) -> float:
    """When this store began; written once, on first use."""
    state = _load(home)
    if state["since"] is not None:
        return state["since"]
    with _lock:
        state = _load(home)
        if state["since"] is None:
            state["since"] = now
            try:
                _save(home, state)
            except OSError:
                pass  # nothing unread until it can be written
        return state["since"]


def _paired_at(home: Path | str | None, device_id: str) -> float:
    if device_id.startswith(TAILNET_PREFIX):
        row = pairing.load_signins(home).get(
            device_id[len(TAILNET_PREFIX):]) or {}
        return num(row.get("first_seen_at"))
    return num((pairing.load_devices(home).get(device_id) or {})
                .get("created_at"))


def baseline(home: Path | str | None, device_id: str,
             now: float | None = None) -> float:
    """The cursor a never-opened session is judged against."""
    now = time.time() if now is None else now
    return max(_since(home, now), _paired_at(home, device_id))


def cursors(home: Path | str | None, device_id: str) -> dict[str, float]:
    """``key → at`` this device has read."""
    return dict(_load(home)["devices"].get(device_id, {}))


def annotate(home: Path | str | None, device_id: str | None,
             rows: Iterable[dict], now: float | None = None) -> list[dict]:
    """Copies of ``rows`` with ``unread`` set for ``device_id``.

    Copies, because the catalog's own rows are shared across requests.
    """
    rows = list(rows)
    if not device_id:
        return [{**r, "unread": False} for r in rows]
    floor = baseline(home, device_id, now)
    seen = cursors(home, device_id)
    out = []
    for row in rows:
        cursor = seen.get(str(row.get("key") or ""), floor)
        out.append({
            **row,
            "unread": num(row.get("last_active_at")) > max(cursor, floor),
        })
    return out


def _live_devices(home: Path | str | None) -> set[str] | None:
    """Device ids still allowed in; None when nothing reads as paired
    (an empty or damaged devices.json), so pruning leaves devices be."""
    devices = pairing.load_devices(home)
    if not devices:
        return None
    return {did for did, row in devices.items()
            if isinstance(row, dict) and not row.get("revoked_at")}


def _revoked_nodes(home: Path | str | None) -> set[str]:
    return {node for node, row in pairing.load_signins(home).items()
            if row.get("revoked_at")}


def mark_read(
    home: Path | str | None,
    device_id: str,
    key: str,
    at: float,
    live_keys: Iterable[str],
) -> float:
    """Move ``device_id``'s cursor for ``key`` to ``at``; never back.

    ``live_keys`` is the catalog's current key set: cursors for any
    other key are pruned, unless it is empty (a hub that hasn't listed
    yet must not forget everything). Returns the stored cursor.
    """
    live = set(live_keys)
    live.add(key)
    with _lock:
        state = _load(home)
        if state["since"] is None:
            state["since"] = time.time()
        devices = state["devices"]
        rows = devices.setdefault(device_id, {})
        stored = max(float(at), rows.get(key, 0.0))
        rows[key] = stored
        paired = _live_devices(home)
        revoked = _revoked_nodes(home)
        kept: dict[str, dict[str, float]] = {}
        for did, cur in devices.items():
            if did.startswith(TAILNET_PREFIX):
                if did[len(TAILNET_PREFIX):] in revoked:
                    continue
            elif paired is not None and did not in paired:
                continue
            if len(live) > 1:
                cur = {k: v for k, v in cur.items() if k in live}
            if len(cur) > MAX_PER_DEVICE:
                cur = dict(sorted(cur.items(), key=lambda kv: kv[1],
                                  reverse=True)[:MAX_PER_DEVICE])
            if cur:
                kept[did] = cur
        state["devices"] = kept
        _save(home, state)
        return stored
