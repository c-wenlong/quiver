"""Pins and display names for sessions, kept on the hub.

``POST /v1/sessions/{key}/pin`` and ``/title`` write
``session_meta.json`` beside ``devices.json`` (0600): ``key → {pinned,
pinned_at, title}``. The store is keyed by session key and lives on the
hub, so every device, the web app and satellite sessions agree.

``apply`` overlays a stored entry onto a catalog row: ``title`` becomes
the override, ``harness_title`` keeps the harness's own title, and
``pinned`` / ``pinned_at`` say whether and since when the row is pinned.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from swe.paths import atomic_write_text, remote_dir_for

_BASENAME = "session_meta.json"

MAX_TITLE_LEN = 120
# Bounds the file against a client that renames in a loop.
MAX_ENTRIES = 5000

ACTIONS = ("pin", "title")

# The hub is the only writer; this covers its concurrent handlers.
_lock = threading.Lock()


def meta_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(Path(home) if home is not None else None) / _BASENAME


def _blank() -> dict:
    return {"pinned": False, "pinned_at": None, "title": ""}


def _load(home: Path | str | None) -> dict[str, dict]:
    try:
        data = json.loads(meta_file(home).read_text(encoding="utf-8"))
        rows = data.get("sessions") if isinstance(data, dict) else None
    except Exception:
        return {}
    if not isinstance(rows, dict):
        return {}
    out: dict[str, dict] = {}
    for key, entry in rows.items():
        if not isinstance(entry, dict):
            continue
        title = entry.get("title")
        pinned_at = entry.get("pinned_at")
        out[str(key)] = {
            "pinned": entry.get("pinned") is True,
            "pinned_at": (
                float(pinned_at)
                if isinstance(pinned_at, (int, float))
                and not isinstance(pinned_at, bool) else None
            ),
            "title": title if isinstance(title, str) else "",
        }
    return out


class TooManyEntries(Exception):
    """The store is full of pins, so there is no room for this write."""


def _save(
    home: Path | str | None, rows: dict[str, dict], writing: str,
) -> None:
    # An entry with nothing set is dropped, so unpin + clear leaves no row.
    keep = {k: v for k, v in rows.items() if v["pinned"] or v["title"]}
    if len(keep) > MAX_ENTRIES:
        # Drop the oldest other renames; pins and the entry being
        # written always stay.
        extra = len(keep) - MAX_ENTRIES
        victims = [
            k for k, v in keep.items() if not v["pinned"] and k != writing
        ]
        if len(victims) < extra:
            raise TooManyEntries(
                "too many pinned sessions; unpin one first"
            )
        for k in victims[:extra]:
            del keep[k]
    atomic_write_text(
        meta_file(home), json.dumps({"sessions": keep}, indent=1),
        private=True,
    )


def load(home: Path | str | None) -> dict[str, dict]:
    """``key → {pinned, pinned_at, title}`` for every session with one."""
    return {k: v for k, v in _load(home).items() if v["pinned"] or v["title"]}


def get(home: Path | str | None, key: str) -> dict:
    return _load(home).get(key) or _blank()


def parse_pin(body: dict) -> bool:
    pinned = body.get("pinned")
    if not isinstance(pinned, bool):
        raise ValueError("pinned must be true or false")
    return pinned


def parse_title(body: dict) -> str:
    title = body.get("title")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    # One line: a newline in a row title breaks every list layout.
    title = " ".join(title.split())
    return title[:MAX_TITLE_LEN].rstrip()


def set_pinned(
    home: Path | str | None, key: str, pinned: bool,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    with _lock:
        rows = _load(home)
        entry = rows.get(key) or _blank()
        if pinned and not entry["pinned"]:
            entry["pinned"], entry["pinned_at"] = True, now
        elif not pinned:
            entry["pinned"], entry["pinned_at"] = False, None
        rows[key] = entry
        _save(home, rows, key)
        return dict(entry)


def set_title(home: Path | str | None, key: str, title: str) -> dict:
    """``title`` is already parsed; an empty one clears the override."""
    with _lock:
        rows = _load(home)
        entry = rows.get(key) or _blank()
        entry["title"] = title
        rows[key] = entry
        _save(home, rows, key)
        return dict(entry)


def update(
    home: Path | str | None, key: str, action: str, body: dict,
) -> dict:
    """Run one ``pin`` / ``title`` request; ``ValueError`` on a bad body."""
    if action == "pin":
        return set_pinned(home, key, parse_pin(body))
    return set_title(home, key, parse_title(body))


def apply(row: dict, entry: dict | None) -> None:
    """Overlay ``entry`` onto a catalog row, in place and repeatable."""
    entry = entry or {}
    if "harness_title" not in row:
        row["harness_title"] = row.get("title") or ""
    override = entry.get("title") or ""
    row["title"] = override or row["harness_title"]
    row["pinned"] = bool(entry.get("pinned"))
    row["pinned_at"] = entry.get("pinned_at") if row["pinned"] else None


def fields(row: dict) -> dict:
    """What ``session.meta`` carries and the pin/title routes answer."""
    return {
        "title": row.get("title") or "",
        "harness_title": row.get("harness_title") or "",
        "pinned": bool(row.get("pinned")),
        "pinned_at": row.get("pinned_at"),
    }
