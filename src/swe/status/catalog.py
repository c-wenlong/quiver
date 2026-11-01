"""The status pages SWE reads, and the vocabulary of a reading.

A port of the Mac app's ``SWECore/Status/ServiceStatusModels.swift``:
the same services in the same order, with the same names, page URLs and
feeds. Keep the two in step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Health(IntEnum):
    """How a service is doing overall, best to worst."""

    OPERATIONAL = 0
    MAINTENANCE = 1
    MINOR = 2
    MAJOR = 3
    CRITICAL = 4

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def title(self) -> str:
        return _HEALTH_TITLES[self]

    @classmethod
    def from_indicator(cls, indicator: str | None) -> "Health | None":
        """Statuspage's ``indicator`` and incident ``impact`` vocabulary."""
        return _INDICATORS.get(indicator or "")

    @classmethod
    def from_key(cls, key: str | None) -> "Health | None":
        return next((h for h in cls if h.key == key), None)


_HEALTH_TITLES = {
    Health.OPERATIONAL: "Operational",
    Health.MAINTENANCE: "Under maintenance",
    Health.MINOR: "Minor incident",
    Health.MAJOR: "Major outage",
    Health.CRITICAL: "Critical outage",
}

_INDICATORS = {
    "none": Health.OPERATIONAL,
    "maintenance": Health.MAINTENANCE,
    "minor": Health.MINOR,
    "major": Health.MAJOR,
    "critical": Health.CRITICAL,
}


class ComponentState(IntEnum):
    """A component that is not fully up, worst last."""

    MAINTENANCE = 0
    DEGRADED = 1
    PARTIAL_OUTAGE = 2
    MAJOR_OUTAGE = 3

    @property
    def key(self) -> str:
        return _STATE_KEYS[self]

    @property
    def title(self) -> str:
        return _STATE_TITLES[self]

    @classmethod
    def from_statuspage(cls, status: str | None) -> "ComponentState | None":
        """Statuspage's component ``status``; None for operational or unknown."""
        return _STATUSPAGE_STATES.get(status or "")

    @classmethod
    def from_key(cls, key: str | None) -> "ComponentState | None":
        return next((s for s in cls if s.key == key), None)


_STATE_KEYS = {
    ComponentState.MAINTENANCE: "maintenance",
    ComponentState.DEGRADED: "degraded",
    ComponentState.PARTIAL_OUTAGE: "partial_outage",
    ComponentState.MAJOR_OUTAGE: "major_outage",
}

_STATE_TITLES = {
    ComponentState.MAINTENANCE: "Under maintenance",
    ComponentState.DEGRADED: "Degraded performance",
    ComponentState.PARTIAL_OUTAGE: "Partial outage",
    ComponentState.MAJOR_OUTAGE: "Major outage",
}

_STATUSPAGE_STATES = {
    "under_maintenance": ComponentState.MAINTENANCE,
    "degraded_performance": ComponentState.DEGRADED,
    "partial_outage": ComponentState.PARTIAL_OUTAGE,
    "major_outage": ComponentState.MAJOR_OUTAGE,
}


# The groups the page shows, in order, with their headings — the same
# ids and titles as the app's `StatusService.Group` cases. The first
# two are the "provider" groups: only they reach the menu bar on a
# minor incident.
GROUPS: tuple[tuple[str, str], ...] = (
    # The model APIs the harnesses run on.
    ("modelProviders", "Model providers"),
    # The agent products and coding platforms built on them.
    ("agentPlatforms", "Agent platforms"),
    # Hosting, edge and connectivity.
    ("cloud", "Cloud & network"),
    # Databases and data platforms.
    ("data", "Data"),
    # Source control, CI, quality and API tooling.
    ("devtools", "Dev tools & quality"),
    # Package registries.
    ("registries", "Registries"),
    # Monitoring, tracing and incident management.
    ("observability", "Observability"),
    # Workflow, identity, comms and team tools.
    ("services", "Services & productivity"),
)


# Group strings in stores written before the split: a custom page that
# read "providers" lands under model providers, one that read
# "infrastructure" under services. Catalogue ids and hosts still win —
# `tracked.py` re-derives a catalogue page's group at load.
LEGACY_GROUPS = {"providers": "modelProviders", "infrastructure": "services"}


@dataclass(frozen=True)
class StatusService:
    """A status page SWE reads.

    ``feed`` is ``"statuspage"`` (Atlassian Statuspage's
    ``/api/v2/summary.json``; incident.io serves the same shape) or
    ``"google_cloud"`` (Google Cloud's ``incidents.json``, narrowed to the
    products matched by id, title or current title).
    """

    id: str
    name: str
    page_url: str
    feed_url: str
    group: str
    feed: str = "statuspage"
    products: frozenset[str] = field(default_factory=frozenset)
    # Where the reading comes from, when that is not obvious from the name.
    note: str | None = None
    # The bundled logo the Mac app draws for it (StatusService.logoID).
    logo: str | None = None
    # StatusService.isCustom: a page the user added rather than a built-in
    # entry. Customs are the only ones that can be removed, and they
    # persist in the hub's own store (tracked.py).
    custom: bool = False

    @property
    def host(self) -> str:
        """The feed's host, for messages ("Couldn't reach status.claude.com")."""
        rest = self.feed_url.split("://", 1)[-1]
        return rest.split("/", 1)[0] or self.feed_url


def _statuspage(id: str, name: str, base: str, group: str, logo: str | None) -> StatusService:
    return StatusService(id=id, name=name, page_url=base,
                         feed_url=base.rstrip("/") + "/api/v2/summary.json",
                         group=group, logo=logo)


# Every service on the page, in order. Each URL is the final one, as in the
# app (status.anthropic.com redirects to status.claude.com). Logos follow
# the app's StatusService.logoID: a company's brand mark ("openai"), and
# GitHub's page under Copilot's.
SERVICES: tuple[StatusService, ...] = (
    _statuspage("claude", "Claude", "https://status.claude.com", "modelProviders", "claude"),
    _statuspage("openai", "OpenAI / Codex", "https://status.openai.com", "modelProviders", "openai"),
    _statuspage("github", "GitHub / Copilot", "https://www.githubstatus.com", "agentPlatforms", "copilot"),
    # Cursor's page moved to status.x.ai, which serves the Statuspage
    # shape under /api/compat rather than /api/v2.
    StatusService(
        id="cursor", name="Cursor", page_url="https://status.x.ai",
        feed_url="https://status.x.ai/api/compat/summary.json",
        group="agentPlatforms", logo="cursor"),
    # AI Studio's status page has no JSON behind it; Google Cloud's feed
    # covers the Gemini API on Vertex (renamed "Gemini on Agent Platform").
    StatusService(
        id="gemini", name="Gemini", page_url="https://aistudio.google.com/status",
        feed_url="https://status.cloud.google.com/incidents.json", group="modelProviders",
        feed="google_cloud",
        products=frozenset({"Z0FZJAMvEB4j3NbCJs6B", "Vertex Gemini API", "Gemini on Agent Platform"}),
        note="Gemini API incidents from Google Cloud's status feed", logo="gemini"),
    _statuspage("devin", "Devin", "https://www.devinstatus.com", "agentPlatforms", "devin"),
    _statuspage("npm", "npm", "https://status.npmjs.org", "registries", "npm"),
    _statuspage("vercel", "Vercel", "https://www.vercel-status.com", "cloud", "vercel"),
    _statuspage("cloudflare", "Cloudflare", "https://www.cloudflarestatus.com", "cloud", "cloudflare"),
)


def service(id: str) -> StatusService | None:
    return next((s for s in SERVICES if s.id == id), None)
