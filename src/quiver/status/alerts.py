"""When a provider starts or stops being affected, for the hub's pushes.

A port of the Mac app's ``QuiverCore/Status/ServiceStatusAlerts.swift``;
keep the two in step. Only the two provider groups notify, from a minor
incident up (the menu line's bar for them), and scheduled maintenance
never does. Each ``service@incident`` key is announced once; a service
with no open incident is keyed ``service@status``, cleared on recovery.
A ``resolved`` alert follows only a ``started`` one. The first check only
records what is already open, and a service with no reading changes
nothing: an unreachable page is neither an outage nor a recovery.

The state lives in ``~/.quiver/cache/web-service-status-notified.json``,
apart from the Mac app's own file, so a hub restart repeats nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from quiver.paths import atomic_write_text, cache_dir_for
from quiver.status.catalog import Health

STATE_NAME = "web-service-status-notified.json"
INTERVAL = 300.0  # the poller's own floor: a shorter one only re-reads the cache
PROVIDER_GROUPS = frozenset({"modelProviders", "agentPlatforms"})
CAP = 200  # delivered keys kept, oldest dropped first


def state_file_for(home: Path | None = None) -> Path:
    return cache_dir_for(home) / STATE_NAME


@dataclass
class Alert:
    kind: str  # "started" or "resolved"
    service_id: str
    service_name: str
    health: Health
    # The new incident's name for "started", the announced one for
    # "resolved"; None when the page named none.
    incident: str | None
    page_url: str

    @property
    def title(self) -> str:
        if self.kind == "started":
            return f"{self.service_name}: {self.health.title.lower()}"
        return f"{self.service_name} is back to normal"

    @property
    def body(self) -> str:
        if self.kind == "started":
            return self.incident or "The status page reports a problem."
        if self.incident:
            return f"Resolved: {self.incident}"
        return "The status page reports no open incident."


@dataclass
class State:
    delivered: list[str] = field(default_factory=list)
    # Service id to the incident its "started" alert named ("" for none).
    down: dict[str, str] = field(default_factory=dict)
    primed: bool = False

    def to_dict(self) -> dict:
        return {"delivered": self.delivered, "down": self.down, "primed": self.primed}

    @classmethod
    def from_dict(cls, raw: Any) -> "State":
        if not isinstance(raw, dict):
            return cls()
        delivered = [k for k in raw.get("delivered") or [] if isinstance(k, str)] \
            if isinstance(raw.get("delivered"), list) else []
        down = {k: v for k, v in raw.get("down").items() if isinstance(k, str) and isinstance(v, str)} \
            if isinstance(raw.get("down"), dict) else {}
        return cls(delivered, down, raw.get("primed") is True)


def _threshold(group: str) -> Health:
    return Health.MINOR if group in PROVIDER_GROUPS else Health.MAJOR


def step(state: State, statuses: Iterable, notify: bool = True) -> list[Alert]:
    """Advance ``state`` by one poll of ``ServiceStatus`` rows and return
    what to announce. With ``notify`` false incidents are still recorded,
    so turning pushes on later doesn't announce old news."""
    statuses = list(statuses)
    announce = notify and state.primed
    alerts: list[Alert] = []
    for status in statuses:
        service = status.service
        if service.group not in PROVIDER_GROUPS or status.report is None:
            continue
        report = status.report
        generic = f"{service.id}@status"
        if report.health >= _threshold(service.group):
            incidents = [i for i in report.incidents if not i.maintenance]
            keyed = [(f"{service.id}@{i.id}", i.name) for i in incidents] \
                or [(generic, report.summary)]
            fresh = [(k, name) for k, name in keyed if k not in state.delivered]
            state.delivered.extend(k for k, _ in fresh)
            if not fresh or not announce:
                continue
            name = fresh[0][1]
            state.down[service.id] = name or ""
            alerts.append(Alert("started", service.id, service.name, report.health, name, service.page_url))
        else:
            # An incident-less blip has no id to remember, so its key
            # clears once the page is back to normal.
            if generic in state.delivered:
                state.delivered.remove(generic)
            incident = state.down.pop(service.id, None)
            if incident is None or not notify:
                continue
            alerts.append(Alert("resolved", service.id, service.name, report.health,
                                incident or None, service.page_url))
    # A page no longer tracked can't resolve; let it go quietly.
    tracked = {s.service.id for s in statuses}
    state.down = {k: v for k, v in state.down.items() if k in tracked}
    if len(state.delivered) > CAP:
        del state.delivered[:len(state.delivered) - CAP]
    state.primed = True
    return alerts


def load(home: Path | None = None) -> State:
    try:
        return State.from_dict(json.loads(state_file_for(home).read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError):
        return State()


def save(home: Path | None, state: State) -> None:
    """Best-effort: a failed write at worst repeats a push after a restart."""
    path = state_file_for(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(state.to_dict(), indent=1, sort_keys=True))
    except OSError:
        pass


def check(home: Path | None, statuses: Iterable) -> list[Alert]:
    """One poll's alerts, with the state loaded and saved around it."""
    state = load(home)
    # A copy: step() edits the lists in place.
    before = json.loads(json.dumps(state.to_dict()))
    alerts = step(state, statuses)
    if state.to_dict() != before:
        save(home, state)
    return alerts
