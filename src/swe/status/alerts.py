"""When a provider starts or stops being affected, for the hub's pushes.

A port of the Mac app's ``QuiverCore/Status/ServiceStatusAlerts.swift``;
keep the two in step. Only the two provider groups notify, from a minor
incident up (the menu line's bar for them), one alert per new incident,
and scheduled maintenance never does. Each ``service@incident`` key is
announced once; a service with no open incident is keyed
``service@status``, cleared on recovery. A ``resolved`` alert follows
only a ``started`` one. A service's first reading only records what is
already open, and a service with no reading changes nothing: an
unreachable page is neither an outage nor a recovery.

The state lives in ``~/.swe/cache/web-service-status-notified.json``,
apart from the Mac app's own file, so a hub restart repeats nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from swe.paths import atomic_write_text, cache_dir_for, locked_beside
from swe.status.catalog import Health

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
    # Service id to the incident its last "started" alert named ("" for none).
    down: dict[str, str] = field(default_factory=dict)
    # Services that have had a first reading, which only seeds.
    seeded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"delivered": list(self.delivered), "down": dict(self.down), "seeded": list(self.seeded)}

    @classmethod
    def from_dict(cls, raw: Any) -> "State":
        if not isinstance(raw, dict):
            return cls()

        def strings(key: str) -> list[str]:
            value = raw.get(key)
            return [k for k in value if isinstance(k, str)] if isinstance(value, list) else []

        down = raw.get("down")
        down = {k: v for k, v in down.items() if isinstance(k, str) and isinstance(v, str)} \
            if isinstance(down, dict) else {}
        return cls(strings("delivered"), down, strings("seeded"))


def _threshold(group: str) -> Health:
    return Health.MINOR if group in PROVIDER_GROUPS else Health.MAJOR


def step(state: State, statuses: Iterable, notify: bool = True) -> list[Alert]:
    """Advance ``state`` by one poll of ``ServiceStatus`` rows and return
    what to announce. With ``notify`` false incidents are still recorded,
    so turning pushes on later doesn't announce old news."""
    statuses = list(statuses)
    alerts: list[Alert] = []
    for status in statuses:
        service = status.service
        if service.group not in PROVIDER_GROUPS or status.report is None:
            continue
        report = status.report
        first = service.id not in state.seeded
        if first:
            state.seeded.append(service.id)
        announce = notify and not first
        generic = f"{service.id}@status"
        if report.health >= _threshold(service.group):
            incidents = [i for i in report.incidents if not i.maintenance]
            keyed = [(f"{service.id}@{i.id}", i.name) for i in incidents] \
                or [(generic, report.summary)]
            fresh = [(k, name) for k, name in keyed if k not in state.delivered]
            state.delivered.extend(k for k, _ in fresh)
            if not announce:
                continue
            for _key, name in fresh:
                state.down[service.id] = name or ""
                alerts.append(Alert("started", service.id, service.name, report.health, name,
                                    service.page_url))
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
    state.seeded = [k for k in state.seeded if k in tracked]
    if len(state.delivered) > CAP:
        del state.delivered[:len(state.delivered) - CAP]
    return alerts


def _read(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return raw if isinstance(raw, dict) else None


class Tracker:
    """The hub's copy of the state. Memory is the truth within a process,
    so an unwritable cache never resets it; the file carries it across
    restarts and wins when another hub on this home wrote it since this
    one last saved. Read, step and save run under one lock file."""

    def __init__(self, home: Path | None = None) -> None:
        self.path = state_file_for(home)
        self._state: State | None = None
        self._seen: dict | None = None

    def check(self, statuses: Iterable, notify: bool = True) -> list[Alert]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            with locked_beside(self.path):
                return self._check(statuses, notify)
        except OSError:
            return self._check(statuses, notify)

    def _check(self, statuses: Iterable, notify: bool) -> list[Alert]:
        disk = _read(self.path)
        # Load at first use, and again only when the file changed under
        # us (another hub saved); a file left behind by a failed save is
        # the one already seen, so it never rolls memory back.
        if self._state is None or (disk is not None and disk != self._seen):
            self._state = State.from_dict(disk)
        self._seen = disk
        before = self._state.to_dict()
        alerts = step(self._state, statuses, notify)
        after = self._state.to_dict()
        if after != before or disk is None:
            try:
                atomic_write_text(self.path, json.dumps(after, indent=1, sort_keys=True))
                self._seen = after
            except OSError:
                pass  # memory keeps the state; the next check tries again
        return alerts
