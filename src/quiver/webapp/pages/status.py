"""Service status: ``GET /v1/app/status``.

The Mac app's Service status page, from ``quiver.status``: each built-in
status page's health, its affected components and open incidents, grouped
as the app groups them. Read only. Query parameters:

- ``cached=1``: the last readings, without touching the network (the page
  paints these first, as the app does, then asks again without it).
- ``force=1``: the Refresh button. Pages are still asked at most once a
  minute, and never during a provider's ``Retry-After``.

Without either, pages last asked five or more minutes ago are asked again;
the rest come from ``~/.quiver/cache/web-service-status.json``.
"""

from __future__ import annotations

import time

from quiver.status.catalog import GROUPS, SERVICES
from quiver.status.poller import ServiceStatus, poller_for


def get(ctx, args: dict) -> dict:
    poller = poller_for(ctx.home)
    if args.get("cached") == "1":
        statuses = poller.cached(SERVICES)
    else:
        statuses = poller.statuses(SERVICES, force=args.get("force") == "1")
    return payload(statuses, machine=(ctx.machine or {}).get("name"), cached=args.get("cached") == "1")


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
                "latest_update": i.latest_update, "url": _link(i.url), "maintenance": i.maintenance,
            }
            for i in (report.incidents if report else [])
        ],
        "checked_at": status.checked_at,
        "problem": status.problem,
        "problem_until": status.problem_until,
        "stale": status.stale,
    }


def _link(url: str | None) -> str | None:
    """A feed's incident link, only when it is a web address: the page puts
    it in an href, and a feed is someone else's text."""
    if url and url.lower().startswith(("https://", "http://")):
        return url
    return None
