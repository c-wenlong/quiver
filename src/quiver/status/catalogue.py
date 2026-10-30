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
    # Model API providers
    _statuspage("mistral", "Mistral AI", "https://status.mistral.ai", "providers", "mistral-vibe"),
    _statuspage("moonshot", "Moonshot AI", "https://status.moonshot.cn", "providers", "kimi"),
    _statuspage("nebius", "Nebius", "https://status.nebius.com", "providers", "nebius"),
    _statuspage("stability", "Stability AI", "https://status.stability.ai", "providers", "stability"),
    _statuspage("ai21", "AI21", "https://status.ai21.com", "providers", "ai21"),
    _statuspage("sambanova", "SambaNova", "https://status.sambanova.ai", "providers", "sambanova"),
    _statuspage("poe", "Poe", "https://status.poe.com", "providers", "poe"),
    _statuspage("writer", "Writer", "https://status.writer.com", "providers", "writer"),
    _statuspage("cartesia", "Cartesia", "https://status.cartesia.ai", "providers", "cartesia"),
    _statuspage("deepgram", "Deepgram", "https://status.deepgram.com", "providers", "deepgram"),
    # Agent products and coding platforms
    _statuspage("droid", "Factory", "https://status.factory.ai", "providers", "droid"),
    _statuspage("windsurf", "Windsurf", "https://status.windsurf.com", "providers", "windsurf"),
    _statuspage("replit", "Replit", "https://status.replit.com", "providers", "replit"),
    _statuspage("sourcegraph", "Sourcegraph", "https://sourcegraphstatus.com", "providers", "sourcegraph"),
    _statuspage("augment", "Augment Code", "https://status.augmentcode.com", "providers", "augment"),
    _statuspage("warp", "Warp", "https://status.warp.dev", "providers", "warp"),
    _statuspage("manus", "Manus", "https://status.manus.im", "providers", "manus"),
    _statuspage("lovable", "Lovable", "https://status.lovable.dev", "providers", "lovable"),
    _statuspage("blackbox", "Blackbox", "https://status.blackbox.ai", "providers", "blackbox"),
    _statuspage("kilo", "Kilo Code", "https://status.kilo.ai", "providers", "kilo"),
    _statuspage("ona", "Ona", "https://onastatus.com", "providers", "ona"),
    # Agent tooling and sandbox platforms
    _statuspage("e2b", "E2B", "https://status.e2b.dev", "providers", "e2b"),
    _statuspage("browserbase", "Browserbase", "https://status.browserbase.com", "providers", "browserbase"),
    _statuspage("tavily", "Tavily", "https://status.tavily.com", "providers", "tavily"),
    _statuspage("runloop", "Runloop", "https://status.runloop.ai", "providers", "runloop"),
    _statuspage("arcade", "Arcade", "https://status.arcade.dev", "providers", "arcade"),
    _statuspage("morph", "Morph", "https://status.morphllm.com", "providers", "morph"),
    _statuspage("supermemory", "Supermemory", "https://status.supermemory.ai", "providers", "supermemory"),
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
    # Package registries
    _statuspage("pypi", "PyPI", "https://status.python.org", "infrastructure", "pypi"),
    _statuspage("crates", "crates.io", "https://status.crates.io", "infrastructure", "crates"),
    _statuspage("rubygems", "RubyGems", "https://status.rubygems.org", "infrastructure", "rubygems"),
    _statuspage("maven", "Maven Central", "https://status.maven.org", "infrastructure", "maven"),
    # Source control and CI
    _statuspage("bitbucket", "Bitbucket", "https://bitbucket.status.atlassian.com", "infrastructure",
                "bitbucket"),
    _statuspage("circleci", "CircleCI", "https://status.circleci.com", "infrastructure", "circleci"),
    _statuspage("buildkite", "Buildkite", "https://www.buildkitestatus.com", "infrastructure",
                "buildkite"),
    _statuspage("travisci", "Travis CI", "https://www.traviscistatus.com", "infrastructure", "travisci"),
    # Hosting and app platforms
    _statuspage("netlify", "Netlify", "https://www.netlifystatus.com", "infrastructure", "netlify"),
    _statuspage("flyio", "Fly.io", "https://status.flyio.net", "infrastructure", "flyio"),
    _statuspage("linode", "Linode", "https://status.linode.com", "infrastructure", "linode"),
    _statuspage("expo", "Expo", "https://status.expo.dev", "infrastructure", "expo"),
    # Data platforms
    _statuspage("mongodb", "MongoDB Atlas", "https://status.mongodb.com", "infrastructure", "mongodb"),
    _statuspage("pinecone", "Pinecone", "https://status.pinecone.io", "infrastructure", "pinecone"),
    _statuspage("prisma", "Prisma", "https://www.prisma-status.com", "infrastructure", "prisma"),
    _statuspage("sanity", "Sanity", "https://www.sanity-status.com", "infrastructure", "sanity"),
    _statuspage("contentful", "Contentful", "https://www.contentfulstatus.com", "infrastructure",
                "contentful"),
    # Workflow and job platforms
    _statuspage("temporal", "Temporal", "https://status.temporal.io", "infrastructure", "temporal"),
    _statuspage("inngest", "Inngest", "https://status.inngest.com", "infrastructure", "inngest"),
    _statuspage("astronomer", "Astronomer", "https://status.astronomer.io", "infrastructure",
                "astronomer"),
    # Observability and incident management
    _statuspage("grafana", "Grafana Cloud", "https://status.grafana.com", "infrastructure", "grafana"),
    _statuspage("newrelic", "New Relic", "https://status.newrelic.com", "infrastructure", "newrelic"),
    _statuspage("honeycomb", "Honeycomb", "https://status.honeycomb.io", "infrastructure", "honeycomb"),
    _statuspage("incidentio", "incident.io", "https://status.incident.io", "infrastructure",
                "incidentio"),
    # Feature flags and code quality
    _statuspage("launchdarkly", "LaunchDarkly", "https://status.launchdarkly.com", "infrastructure",
                "launchdarkly"),
    _statuspage("statsig", "Statsig", "https://status.statsig.com", "infrastructure", "statsig"),
    _statuspage("snyk", "Snyk", "https://status.snyk.io", "infrastructure", "snyk"),
    _statuspage("codecov", "Codecov", "https://status.codecov.com", "infrastructure", "codecov"),
    # API tooling and misc platforms
    _statuspage("postman", "Postman", "https://status.postman.com", "infrastructure", "postman"),
    _statuspage("ngrok", "ngrok", "https://status.ngrok.com", "infrastructure", "ngrok"),
    _statuspage("hashicorp", "HashiCorp", "https://status.hashicorp.com", "infrastructure", "hashicorp"),
    _statuspage("figma", "Figma", "https://status.figma.com", "infrastructure", "figma"),
    _statuspage("atlassian", "Atlassian", "https://status.atlassian.com", "infrastructure", "atlassian"),
    _statuspage("workos", "WorkOS", "https://status.workos.com", "infrastructure", "workos"),
    _statuspage("mailgun", "Mailgun", "https://status.mailgun.com", "infrastructure", "mailgun"),
    _statuspage("discord", "Discord", "https://discordstatus.com", "infrastructure", "discord"),
    _statuspage("revenuecat", "RevenueCat", "https://status.revenuecat.com", "infrastructure",
                "revenuecat"),
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
