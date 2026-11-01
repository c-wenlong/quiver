"""Reads status feeds into a ``Report``.

A port of the Mac app's ``QuiverCore/Status/StatusParser.swift``. Lenient
about missing fields, since incident.io and Google serve their own
variants; ``None`` only when the body is not the expected feed at all.
Times are Unix epoch seconds.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any

from swe.status.catalog import ComponentState, Health


@dataclass
class Component:
    name: str
    state: ComponentState

    def to_dict(self) -> dict:
        return {"name": self.name, "state": self.state.key}

    @classmethod
    def from_dict(cls, raw: dict) -> "Component | None":
        state = ComponentState.from_key(raw.get("state"))
        name = raw.get("name")
        return cls(name, state) if state is not None and isinstance(name, str) else None


@dataclass
class Incident:
    """An open incident, or a maintenance that is under way."""

    id: str
    name: str
    impact: Health
    # Where the provider says it is: "Investigating", "Monitoring", "In progress".
    stage: str | None = None
    started_at: float | None = None
    # The newest update's text, as the provider wrote it.
    latest_update: str | None = None
    updated_at: float | None = None
    url: str | None = None
    maintenance: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "impact": self.impact.key, "stage": self.stage,
            "started_at": self.started_at, "latest_update": self.latest_update,
            "updated_at": self.updated_at, "url": self.url, "maintenance": self.maintenance,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Incident | None":
        impact = Health.from_key(raw.get("impact"))
        if impact is None or not isinstance(raw.get("id"), str) or not isinstance(raw.get("name"), str):
            return None
        return cls(id=raw["id"], name=raw["name"], impact=impact, stage=raw.get("stage"),
                   started_at=raw.get("started_at"), latest_update=raw.get("latest_update"),
                   updated_at=raw.get("updated_at"), url=raw.get("url"),
                   maintenance=bool(raw.get("maintenance")))


@dataclass
class Report:
    """One reading of a status page."""

    health: Health
    # The page's own one-line summary ("Partially Degraded Service").
    summary: str | None = None
    # Components that are not operational, worst first.
    components: list[Component] = field(default_factory=list)
    # Open incidents and maintenances under way, worst first.
    incidents: list[Incident] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "health": self.health.key, "summary": self.summary,
            "components": [c.to_dict() for c in self.components],
            "incidents": [i.to_dict() for i in self.incidents],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "Report | None":
        if not isinstance(raw, dict):
            return None
        health = Health.from_key(raw.get("health"))
        if health is None:
            return None
        components = [c for c in (Component.from_dict(x) for x in _dicts(raw.get("components"))) if c]
        incidents = [i for i in (Incident.from_dict(x) for x in _dicts(raw.get("incidents"))) if i]
        summary = raw.get("summary")
        return cls(health, summary if isinstance(summary, str) else None, components, incidents)


# --------------------------------------------------------------- JSON helpers
# The Mac app's UsageJSON, as far as the parsers use it.

def _loads(data: bytes) -> Any:
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        return None


def _string(value: Any) -> str | None:
    """A non-empty string, trimmed; None for anything else."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _dicts(value: Any) -> list[dict]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _date(value: Any) -> float | None:
    """An ISO 8601 time with an offset, or epoch seconds (milliseconds
    when the number is that large), as epoch seconds."""
    text = _string(value)
    if text is not None:
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed.timestamp() if parsed.tzinfo is not None else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) / 1000 if value > 100_000_000_000 else float(value)


def _first(*values: float | None) -> float | None:
    """The first time that is known (``a ?? b`` in the Swift)."""
    return next((v for v in values if v is not None), None)


def _stage_title(raw: str) -> str:
    """"in_progress" -> "In progress"."""
    words = raw.replace("_", " ").lower()
    return words[:1].upper() + words[1:]


def _worst_first(incidents: list[Incident]) -> list[Incident]:
    # Worse impact first, then the newest start; stable among equals.
    return sorted(incidents, key=lambda i: (-i.impact, -(i.started_at if i.started_at is not None else float("-inf"))))


# ------------------------------------------------------------------ Statuspage

def statuspage(data: bytes) -> Report | None:
    """Atlassian Statuspage ``summary.json`` (incident.io serves the same)."""
    root = _loads(data)
    if not isinstance(root, dict) or not isinstance(root.get("status"), dict):
        return None
    status = root["status"]
    health = Health.from_indicator(_string(status.get("indicator")))
    if health is None:
        return None

    incidents = [
        incident for incident in (
            _incident(raw, maintenance=False) for raw in _dicts(root.get("incidents"))
            if (_string(raw.get("status")) or "") not in ("resolved", "postmortem", "completed"))
        if incident is not None
    ]
    # Only maintenance that has started; the feed also lists ones weeks out.
    maintenances = [
        incident for incident in (
            _incident(raw, maintenance=True) for raw in _dicts(root.get("scheduled_maintenances"))
            if (_string(raw.get("status")) or "") in ("in_progress", "verifying"))
        if incident is not None
    ]
    return Report(
        health=health,
        summary=_string(status.get("description")),
        components=_components(_dicts(root.get("components"))),
        incidents=_worst_first(incidents + maintenances),
    )


def _components(raw: list[dict]) -> list[Component]:
    """Non-operational components, worst first and otherwise in page order.
    A group's state only echoes its members, so groups are listed only when
    no member is."""
    leaves: list[Component] = []
    groups: list[Component] = []
    for component in raw:
        name = _string(component.get("name"))
        state = ComponentState.from_statuspage(_string(component.get("status")))
        if name is None or state is None:
            continue
        (groups if component.get("group") is True else leaves).append(Component(name, state))
    listed = leaves or groups
    return sorted(listed, key=lambda c: -c.state)


def _incident(raw: dict, maintenance: bool) -> Incident | None:
    id, name = _string(raw.get("id")), _string(raw.get("name"))
    if id is None or name is None:
        return None
    updates = [(u, _first(_date(u.get("display_at")), _date(u.get("created_at"))))
               for u in _dicts(raw.get("incident_updates"))]
    # The newest update; the first of equals, as Swift's max(by:) keeps.
    latest = None
    for update in updates:
        if latest is None or _first(update[1], float("-inf")) > _first(latest[1], float("-inf")):
            latest = update
    impact = Health.from_indicator(_string(raw.get("impact")))
    status = _string(raw.get("status"))
    # A maintenance's `started_at` is when it was announced; it begins at
    # `scheduled_for`.
    if maintenance:
        started = _first(_date(raw.get("scheduled_for")), _date(raw.get("started_at")))
    else:
        started = _first(_date(raw.get("started_at")), _date(raw.get("scheduled_for")))
    return Incident(
        id=id,
        name=name,
        impact=Health.MAINTENANCE if maintenance else max(impact if impact is not None else Health.MINOR, Health.MINOR),
        stage=_stage_title(status) if status else None,
        started_at=_first(started, _date(raw.get("created_at"))),
        latest_update=_string(latest[0].get("body")) if latest else None,
        updated_at=_first(latest[1] if latest else None, _date(raw.get("updated_at"))),
        url=_string(raw.get("shortlink")),
        maintenance=maintenance,
    )


# ---------------------------------------------------------------- Google Cloud

def google_cloud(data: bytes, products: frozenset[str] | set[str], now: float) -> Report | None:
    """Google Cloud's ``incidents.json``: every incident of the last year,
    open ones with no ``end`` (or an end still ahead), narrowed to
    ``products``."""
    every = _loads(data)
    if not isinstance(every, list):
        return None
    incidents: list[Incident] = []
    components: list[Component] = []
    for raw in _dicts(every):
        end = _date(raw.get("end"))
        if end is not None and end <= now:
            continue
        matched = [
            product for product in _dicts(raw.get("affected_products"))
            if any(_string(product.get(key)) in products for key in ("id", "title", "current_title"))
        ]
        id = _string(raw.get("id"))
        if not matched or id is None:
            continue
        severity = _string(raw.get("severity"))
        impact = {"high": Health.CRITICAL, "medium": Health.MAJOR}.get(severity or "", Health.MINOR)
        state = {"high": ComponentState.MAJOR_OUTAGE,
                 "medium": ComponentState.PARTIAL_OUTAGE}.get(severity or "", ComponentState.DEGRADED)
        # Two open incidents can hit the same product; keep the worse state.
        for product in matched:
            name = _string(product.get("current_title")) or _string(product.get("title"))
            if name is None:
                continue
            existing = next((c for c in components if c.name == name), None)
            if existing is not None:
                existing.state = max(existing.state, state)
            else:
                components.append(Component(name, state))
        update = raw.get("most_recent_update")
        update = update if isinstance(update, dict) else {}
        stage = _string(update.get("status"))
        text = _string(update.get("text"))
        uri = _string(raw.get("uri"))
        incidents.append(Incident(
            id=id,
            name=_string(raw.get("external_desc")) or "Incident",
            impact=impact,
            stage=_stage_title(stage) if stage else None,
            started_at=_date(raw.get("begin")),
            latest_update=_plain_text(text) if text else None,
            updated_at=_first(_date(update.get("when")), _date(raw.get("modified"))),
            url=f"https://status.cloud.google.com/{uri}" if uri else None,
        ))
    ranked = _worst_first(incidents)
    return Report(
        health=ranked[0].impact if ranked else Health.OPERATIONAL,
        summary=None,
        components=sorted(components, key=lambda c: -c.state),
        incidents=ranked,
    )


def _plain_text(markdown: str) -> str:
    """Google writes updates in Markdown. Headings go, emphasis markers go,
    and the rest is kept as lines."""
    lines = [line.strip() for line in markdown.split("\n")]
    kept = [line.replace("**", "").replace("__", "") for line in lines if not line.startswith("#")]
    return "\n".join(kept).strip()
