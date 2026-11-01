"""Service status: ``GET /v1/app/status``.

The Mac app's Service status page, from ``swe.status``: each tracked
status page's health, its affected components and open incidents, grouped
as the app groups them. Query parameters:

- ``view=catalogue``: the add sheet's catalogue (``catalogue.CATALOGUE``),
  each entry with ``added`` for the pages this hub already tracks.
- ``cached=1``: the last readings, without touching the network (the page
  paints these first, as the app does, then asks again without it).
- ``force=1``: the Refresh button. Pages are still asked at most once a
  minute, and never during a provider's ``Retry-After``.

Without either, pages last asked five or more minutes ago are asked again;
the rest come from ``~/.swe/cache/web-service-status.json``.

``POST /v1/app/status/track`` (``{"id"}``) is the catalogue sheet's Add:
the entry joins the tracked set in ``tracked.py``'s store, the web app's
own file under ``~/.swe/data`` (the Mac's customs live in its
preferences, which this does not read). ``POST /v1/app/status/untrack``
is the custom card's Remove: only a tracked extra goes; a built-in page
is refused, as ``ServiceStatusModel.remove`` refuses it. Both answer
with the page's fresh payload.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from swe.status import catalogue, tracked
from swe.status.catalog import GROUPS, SERVICES
from swe.status.poller import ServiceStatus, poller_for
from swe.webapp.pages import PageError, web_url

# One track/untrack at a time in this process; the store's own flock
# covers a second hub on the same home.
_WRITE_LOCK = threading.Lock()


def get(ctx, args: dict) -> dict:
    if args.get("view") == "catalogue":
        return catalogue_view(ctx.home)
    poller = poller_for(ctx.home)
    services = _services(ctx.home)
    if args.get("cached") == "1":
        statuses = poller.cached(services)
    else:
        statuses = poller.statuses(services, force=args.get("force") == "1")
    return payload(statuses, machine=(ctx.machine or {}).get("name"), cached=args.get("cached") == "1")


def _services(home: Path | None) -> list:
    """The built-ins plus the tracked extras, in card order, as the Mac's
    ``StatusService.all + store.load()``."""
    return list(SERVICES) + tracked.load(home)


def catalogue_view(home: Path | None, group: str | None = None, query: str = "") -> dict:
    """The add sheet's rows. ``added`` is the Mac's rule: a page on the
    grid, built-in or custom, marks the entry when its feed's host is
    already tracked, so the same page under another name still counts."""
    hosts = {s.host.lower() for s in _services(home)}
    entries = catalogue.filter_(group=group, query=query)
    return {"entries": [{"id": e.id, "name": e.name, "host": e.host,
                         "group": e.group, "logo": e.logo,
                         "added": e.host.lower() in hosts} for e in entries]}


def post(ctx, action: str, body: dict) -> dict:
    if action not in ("track", "untrack"):
        raise PageError(404, "unknown_action", f"unknown status action: {action}")
    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "the body must be a JSON object")
    id_ = body.get("id")
    if not isinstance(id_, str) or not id_:
        raise PageError(422, "bad_id", "id must name a status page")
    home = ctx.home
    with _WRITE_LOCK:
        if action == "track":
            _track(home, id_)
        else:
            _untrack(home, id_)
    poller = poller_for(home)
    statuses = poller.statuses(_services(home))
    return payload(statuses, machine=(ctx.machine or {}).get("name"))


def _track(home: Path | None, id: str) -> None:
    entry = catalogue.entry(id)
    if entry is None:
        raise PageError(404, "unknown_service", f"The catalogue has no status page {id}.")
    if any(s.id == id for s in SERVICES):
        raise PageError(409, "already_tracked", f"{entry.name} is already on the page.")
    # The Mac's sheet can only add what is not shown as Added; the same
    # rule here: a page whose feed host is already tracked cannot land
    # twice, under this id or another.
    if any(s.host.lower() == entry.host.lower() for s in _services(home)):
        raise PageError(409, "already_tracked", f"{entry.name} is already on the page.")
    tracked.add(home, entry)


def _untrack(home: Path | None, id: str) -> None:
    if any(s.id == id for s in SERVICES):
        raise PageError(422, "built_in",
                        f"{id} is a built-in page; only pages you added can be removed.")
    if not tracked.remove(home, id):
        raise PageError(404, "unknown_service", f"No tracked page has id {id}.")
    # Its poll cache goes too, so a same-named page added later cannot
    # inherit the old reading (the Mac's service.forget(id:)).
    poller_for(home).forget(id)


def payload(statuses: list[ServiceStatus], machine: str | None = None, cached: bool = False,
            now: float | None = None) -> dict:
    checked = [s.checked_at for s in statuses if s.checked_at is not None]
    groups = []
    for group, title in GROUPS:
        members = [_service(s) for s in statuses if s.service.group == group]
        if members:
            groups.append({"id": group, "title": title, "services": members})
    return {
        "machine": machine,
        # The most recent answer from any page, for "Checked … ago".
        "checked_at": max(checked) if checked else None,
        "cached": cached,
        # The hub's clock now, so the page can age kept readings without
        # trusting the browser's clock to agree.
        "server_time": time.time() if now is None else now,
        "groups": groups,
    }


def _service(status: ServiceStatus) -> dict:
    service, report = status.service, status.report
    return {
        "id": service.id,
        "name": service.name,
        "page_url": service.page_url,
        "group": service.group,
        "note": service.note,
        "logo": service.logo,
        "custom": service.custom,
        "health": report.health.key if report else None,
        "health_title": report.health.title if report else None,
        "summary": report.summary if report else None,
        "components": [
            {"name": c.name, "state": c.state.key, "state_title": c.state.title}
            for c in (report.components if report else [])
        ],
        "incidents": [
            {
                "id": i.id, "name": i.name, "impact": i.impact.key, "stage": i.stage,
                "started_at": i.started_at, "updated_at": i.updated_at,
                "latest_update": i.latest_update, "url": web_url(i.url), "maintenance": i.maintenance,
            }
            for i in (report.incidents if report else [])
        ],
        "checked_at": status.checked_at,
        "problem": status.problem,
        "problem_until": status.problem_until,
        "stale": status.stale,
    }
