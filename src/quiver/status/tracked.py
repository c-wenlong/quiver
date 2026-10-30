"""The status pages a user added in the web app, on the hub.

A port of the Mac app's ``QuiverCore/Status/CustomStatusServices.swift``,
against the hub's own file, ``~/.quiver/data/web-status-services.json``:
the app's customs live in its preferences, which the web app does not
read, and every browser the hub serves shares this one store, so a page
added in one shows in the rest. Records hold only what rebuilds a
``StatusService``; the web app adds pages from the catalogue
(``catalogue.py``), which all serve the Statuspage ``summary.json``
shape, so no feed kind is stored.

Reads see another writer's save; a write holds a lock file beside the
data file, the way the poller's cache does, so two hubs on one home
cannot lose each other's entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quiver.paths import atomic_write_text, data_dir_for, locked_beside
from quiver.status.catalog import LEGACY_GROUPS, StatusService

STORE_NAME = "web-status-services.json"


def tracked_file_for(home: Path | None = None) -> Path:
    return data_dir_for(home) / STORE_NAME


def _record(service: StatusService) -> dict:
    return {"id": service.id, "name": service.name, "page_url": service.page_url,
            "feed_url": service.feed_url, "group": service.group, "logo": service.logo}


def _service(raw: Any) -> StatusService | None:
    """One record back as a custom ``StatusService``; a record that does
    not decode is skipped, as the Mac's store reads bad data as empty."""
    if not isinstance(raw, dict):
        return None
    id_, name, page_url = raw.get("id"), raw.get("name"), raw.get("page_url")
    feed_url, group = raw.get("feed_url"), raw.get("group")
    if not all(isinstance(v, str) and v for v in (id_, name, page_url, feed_url, group)):
        return None
    if not page_url.startswith(("https://", "http://")) or not feed_url.startswith(("https://", "http://")):
        return None
    logo = raw.get("logo")
    return StatusService(id=id_, name=name, page_url=page_url, feed_url=feed_url,
                         group=_group(id_, feed_url, group),
                         logo=logo if isinstance(logo, str) else None,
                         custom=True)


def _group(id: str, feed_url: str, stored: str) -> str:
    """The group a stored record renders under.

    A record still carrying a pre-split group was last saved under the
    old layout, so a catalogue page it names follows the catalogue's
    group and anything else maps forward. A record saved since keeps
    its own group — a group the user picked is never rewritten on
    load."""
    if stored not in LEGACY_GROUPS:
        return stored
    from quiver.status import catalogue
    host = feed_url.split("://", 1)[-1].split("/", 1)[0]
    found = catalogue.entry(id) or next((e for e in catalogue.CATALOGUE if e.host == host), None)
    return found.group if found is not None else LEGACY_GROUPS[stored]


def _load(path: Path) -> list[StatusService]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return []
    records = raw.get("services") if isinstance(raw, dict) else None
    if not isinstance(records, list):
        return []
    return [s for s in (_service(r) for r in records) if s is not None]


def load(home: Path | None = None) -> list[StatusService]:
    """The tracked extras, in the order they were added."""
    return _load(tracked_file_for(home))


def _write(path: Path, services: list[StatusService]) -> None:
    """The whole store, atomically (callers hold the lock file)."""
    atomic_write_text(path, json.dumps(
        {"version": 1, "services": [_record(s) for s in services]},
        indent=2, sort_keys=True))


def _edit(path: Path, change) -> list[StatusService]:
    """Read, change, write under the lock file. ``change`` maps the current
    list to the new one; its result is both written and returned."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The lock keeps two hubs on one home from losing each other's entries.
    with locked_beside(path):
        services = change(_load(path))
        _write(path, services)
        return services


def add(home: Path | None, service: StatusService) -> StatusService:
    """Track a catalogue entry; tracking an id already held is a no-op, so
    the sheet's Add can never land a page twice. Returns the stored row
    (the already-tracked one when the add was a repeat)."""
    path = tracked_file_for(home)
    service = StatusService(id=service.id, name=service.name, page_url=service.page_url,
                            feed_url=service.feed_url, group=service.group, logo=service.logo,
                            custom=True)

    def change(services: list[StatusService]) -> list[StatusService]:
        if all(s.id != service.id for s in services):
            return services + [service]
        return services

    services = _edit(path, change)
    return next(s for s in services if s.id == service.id)


def remove(home: Path | None, id: str) -> bool:
    """Stop tracking a page. False when no tracked extra has the id.

    The check rides inside the locked edit: another hub sharing this home
    may add or drop a page between a pre-read and the write, and only the
    snapshot the lock guards can say the id was really there."""
    path = tracked_file_for(home)
    removed = False

    def change(services: list[StatusService]) -> list[StatusService]:
        nonlocal removed
        removed = any(s.id == id for s in services)
        return [s for s in services if s.id != id]

    _edit(path, change)
    return removed
