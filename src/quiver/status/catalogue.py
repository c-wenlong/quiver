"""The catalogue behind the "one-click add" sheet.

A port of the Mac app's ``QuiverCore/Status/StatusCatalogue.swift``:
common status pages, each verified to serve the Atlassian Statuspage
``api/v2/summary.json``. Every entry is a plain ``StatusService``, so a
row's logo resolves the same way an added card's does. Each URL is the
page's final one; pages checked and rejected (betterstack, status.io,
Cloudflare-challenged or custom feeds like AWS's) are not listed. Keep
the two in step.
"""

from __future__ import annotations

from quiver.status.catalog import StatusService, _statuspage


# Providers first, then infrastructure, in display order. Unlike the
# Mac's `model.add` (which gives every added entry a fresh custom id),
# the web store keeps the catalogue id, so tracking is idempotent. The
# `logo` on each entry is its resolved StatusService.logoID (a brand
# icon or the entry's own id), baked as `SERVICES` bakes its logos.
CATALOGUE: tuple[StatusService, ...] = (
    _statuspage("groq", "Groq", "https://groqstatus.com", "providers", "groq"),
    _statuspage("perplexity", "Perplexity", "https://status.perplexity.com", "providers", "perplexity"),
    _statuspage("fireworks", "Fireworks AI", "https://status.fireworks.ai", "providers", "fireworks"),
    _statuspage("cerebras", "Cerebras", "https://status.cerebras.ai", "providers", "cerebras"),
    _statuspage("cohere", "Cohere", "https://status.cohere.com", "providers", "cohere"),
    _statuspage("baseten", "Baseten", "https://status.baseten.co", "providers", "baseten"),
    _statuspage("minimax", "MiniMax", "https://status.minimax.io", "providers", "minimax"),
    _statuspage("elevenlabs", "ElevenLabs", "https://status.elevenlabs.io", "providers", "elevenlabs"),
    _statuspage("lambda", "Lambda", "https://status.lambda.ai", "providers", "lambda"),
    _statuspage("assemblyai", "AssemblyAI", "https://status.assemblyai.com", "providers", "assemblyai"),
    _statuspage("docker", "Docker Hub", "https://www.dockerstatus.com", "infrastructure", "docker"),
    _statuspage("tailscale", "Tailscale", "https://status.tailscale.com", "infrastructure", "tailscale"),
    _statuspage("sentry", "Sentry", "https://status.sentry.io", "infrastructure", "sentry"),
    _statuspage("datadog", "Datadog", "https://status.datadoghq.com", "infrastructure", "datadog"),
    _statuspage("supabase", "Supabase", "https://status.supabase.com", "infrastructure", "supabase"),
    _statuspage("digitalocean", "DigitalOcean", "https://status.digitalocean.com", "infrastructure",
                "digitalocean"),
    _statuspage("render", "Render", "https://status.render.com", "infrastructure", "render"),
    _statuspage("upstash", "Upstash", "https://status.upstash.com", "infrastructure", "upstash"),
    _statuspage("convex", "Convex", "https://status.convex.dev", "infrastructure", "convex"),
    _statuspage("linear", "Linear", "https://linearstatus.com", "infrastructure", "linear"),
    _statuspage("notion", "Notion", "https://www.notion-status.com", "infrastructure", "notion"),
    _statuspage("twilio", "Twilio", "https://status.twilio.com", "infrastructure", "twilio"),
    _statuspage("clerk", "Clerk", "https://status.clerk.com", "infrastructure", "clerk"),
)


def entry(id: str) -> StatusService | None:
    return next((s for s in CATALOGUE if s.id == id), None)


def filter_(entries=CATALOGUE, group: str | None = None, query: str = "") -> list[StatusService]:
    """``StatusCatalogue.filter``: the entries passing the chip pick and
    the search; the query matches the name and the feed's host ("docker"
    finds Docker Hub by name, "notion-status" by its page's host)."""
    needle = query.strip().lower()
    return [e for e in entries
            if (group is None or e.group == group)
            and (not needle or needle in e.name.lower() or needle in e.host.lower())]
