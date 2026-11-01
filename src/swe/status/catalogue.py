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

from swe.status.catalog import StatusService, _statuspage


# Entries sit under their group's heading, in `GROUPS` order. Unlike the
# Mac's `model.add` (which gives every added entry a fresh custom id),
# the web store keeps the catalogue id, so tracking is idempotent. The
# `logo` on each entry is its resolved StatusService.logoID (a brand
# icon or the entry's own id), baked as `SERVICES` bakes its logos.
CATALOGUE: tuple[StatusService, ...] = (
    # Model API providers
    _statuspage("groq", "Groq", "https://groqstatus.com", "modelProviders", "groq"),
    _statuspage("perplexity", "Perplexity", "https://status.perplexity.com", "modelProviders",
                "perplexity"),
    _statuspage("fireworks", "Fireworks AI", "https://status.fireworks.ai", "modelProviders",
                "fireworks"),
    _statuspage("cerebras", "Cerebras", "https://status.cerebras.ai", "modelProviders", "cerebras"),
    _statuspage("cohere", "Cohere", "https://status.cohere.com", "modelProviders", "cohere"),
    _statuspage("baseten", "Baseten", "https://status.baseten.co", "modelProviders", "baseten"),
    _statuspage("minimax", "MiniMax", "https://status.minimax.io", "modelProviders", "minimax"),
    _statuspage("elevenlabs", "ElevenLabs", "https://status.elevenlabs.io", "modelProviders",
                "elevenlabs"),
    _statuspage("lambda", "Lambda", "https://status.lambda.ai", "modelProviders", "lambda"),
    _statuspage("assemblyai", "AssemblyAI", "https://status.assemblyai.com", "modelProviders",
                "assemblyai"),
    _statuspage("mistral", "Mistral AI", "https://status.mistral.ai", "modelProviders",
                "mistral-vibe"),
    _statuspage("moonshot", "Moonshot AI", "https://status.moonshot.cn", "modelProviders", "kimi"),
    _statuspage("nebius", "Nebius", "https://status.nebius.com", "modelProviders", "nebius"),
    _statuspage("stability", "Stability AI", "https://status.stability.ai", "modelProviders",
                "stability"),
    _statuspage("ai21", "AI21", "https://status.ai21.com", "modelProviders", "ai21"),
    _statuspage("sambanova", "SambaNova", "https://status.sambanova.ai", "modelProviders",
                "sambanova"),
    _statuspage("poe", "Poe", "https://status.poe.com", "modelProviders", "poe"),
    _statuspage("writer", "Writer", "https://status.writer.com", "modelProviders", "writer"),
    _statuspage("cartesia", "Cartesia", "https://status.cartesia.ai", "modelProviders", "cartesia"),
    _statuspage("deepgram", "Deepgram", "https://status.deepgram.com", "modelProviders", "deepgram"),
    _statuspage("nvidia", "NVIDIA NGC", "https://status.ngc.nvidia.com", "modelProviders", "nvidia"),
    _statuspage("anyscale", "Anyscale", "https://status.anyscale.com", "modelProviders", "anyscale"),
    # Agent products and coding platforms
    _statuspage("droid", "Factory", "https://status.factory.ai", "agentPlatforms", "droid"),
    _statuspage("windsurf", "Windsurf", "https://status.windsurf.com", "agentPlatforms", "windsurf"),
    _statuspage("replit", "Replit", "https://status.replit.com", "agentPlatforms", "replit"),
    _statuspage("sourcegraph", "Sourcegraph", "https://sourcegraphstatus.com", "agentPlatforms",
                "sourcegraph"),
    _statuspage("augment", "Augment Code", "https://status.augmentcode.com", "agentPlatforms",
                "augment"),
    _statuspage("warp", "Warp", "https://status.warp.dev", "agentPlatforms", "warp"),
    _statuspage("manus", "Manus", "https://status.manus.im", "agentPlatforms", "manus"),
    _statuspage("lovable", "Lovable", "https://status.lovable.dev", "agentPlatforms", "lovable"),
    _statuspage("blackbox", "Blackbox", "https://status.blackbox.ai", "agentPlatforms", "blackbox"),
    _statuspage("kilo", "Kilo Code", "https://status.kilo.ai", "agentPlatforms", "kilo"),
    _statuspage("ona", "Ona", "https://onastatus.com", "agentPlatforms", "ona"),
    _statuspage("e2b", "E2B", "https://status.e2b.dev", "agentPlatforms", "e2b"),
    _statuspage("browserbase", "Browserbase", "https://status.browserbase.com", "agentPlatforms",
                "browserbase"),
    _statuspage("tavily", "Tavily", "https://status.tavily.com", "agentPlatforms", "tavily"),
    _statuspage("runloop", "Runloop", "https://status.runloop.ai", "agentPlatforms", "runloop"),
    _statuspage("arcade", "Arcade", "https://status.arcade.dev", "agentPlatforms", "arcade"),
    _statuspage("morph", "Morph", "https://status.morphllm.com", "agentPlatforms", "morph"),
    _statuspage("supermemory", "Supermemory", "https://status.supermemory.ai", "agentPlatforms",
                "supermemory"),
    _statuspage("bolt", "Bolt.new", "https://status.bolt.new", "agentPlatforms", "bolt"),
    _statuspage("v0", "v0", "https://v0-status.com", "agentPlatforms", "v0"),
    # Cloud hosting, edge and connectivity
    _statuspage("digitalocean", "DigitalOcean", "https://status.digitalocean.com", "cloud",
                "digitalocean"),
    _statuspage("render", "Render", "https://status.render.com", "cloud", "render"),
    _statuspage("netlify", "Netlify", "https://www.netlifystatus.com", "cloud", "netlify"),
    _statuspage("flyio", "Fly.io", "https://status.flyio.net", "cloud", "flyio"),
    _statuspage("linode", "Linode", "https://status.linode.com", "cloud", "linode"),
    _statuspage("expo", "Expo", "https://status.expo.dev", "cloud", "expo"),
    _statuspage("tailscale", "Tailscale", "https://status.tailscale.com", "cloud", "tailscale"),
    _statuspage("ngrok", "ngrok", "https://status.ngrok.com", "cloud", "ngrok"),
    # Databases and data platforms
    _statuspage("supabase", "Supabase", "https://status.supabase.com", "data", "supabase"),
    _statuspage("convex", "Convex", "https://status.convex.dev", "data", "convex"),
    _statuspage("upstash", "Upstash", "https://status.upstash.com", "data", "upstash"),
    _statuspage("mongodb", "MongoDB Atlas", "https://status.mongodb.com", "data", "mongodb"),
    _statuspage("pinecone", "Pinecone", "https://status.pinecone.io", "data", "pinecone"),
    _statuspage("prisma", "Prisma", "https://www.prisma-status.com", "data", "prisma"),
    _statuspage("sanity", "Sanity", "https://www.sanity-status.com", "data", "sanity"),
    _statuspage("contentful", "Contentful", "https://www.contentfulstatus.com", "data", "contentful"),
    _statuspage("snowflake", "Snowflake", "https://status.snowflake.com", "data", "snowflake"),
    _statuspage("clickhouse", "ClickHouse", "https://status.clickhouse.com", "data", "clickhouse"),
    _statuspage("cockroach", "CockroachDB", "https://status.cockroachlabs.cloud", "data",
                "cockroach"),
    _statuspage("planetscale", "PlanetScale", "https://www.planetscalestatus.com", "data",
                "planetscale"),
    # Source control, CI, quality and API tooling
    _statuspage("bitbucket", "Bitbucket", "https://bitbucket.status.atlassian.com", "devtools",
                "bitbucket"),
    _statuspage("circleci", "CircleCI", "https://status.circleci.com", "devtools", "circleci"),
    _statuspage("buildkite", "Buildkite", "https://www.buildkitestatus.com", "devtools", "buildkite"),
    _statuspage("travisci", "Travis CI", "https://www.traviscistatus.com", "devtools", "travisci"),
    _statuspage("semaphore", "Semaphore", "https://status.semaphore.io", "devtools", "semaphore"),
    _statuspage("postman", "Postman", "https://status.postman.com", "devtools", "postman"),
    _statuspage("hashicorp", "HashiCorp", "https://status.hashicorp.com", "devtools", "hashicorp"),
    _statuspage("snyk", "Snyk", "https://status.snyk.io", "devtools", "snyk"),
    _statuspage("codecov", "Codecov", "https://status.codecov.com", "devtools", "codecov"),
    _statuspage("launchdarkly", "LaunchDarkly", "https://status.launchdarkly.com", "devtools",
                "launchdarkly"),
    _statuspage("statsig", "Statsig", "https://status.statsig.com", "devtools", "statsig"),
    # Package registries
    _statuspage("docker", "Docker Hub", "https://www.dockerstatus.com", "registries", "docker"),
    _statuspage("pypi", "PyPI", "https://status.python.org", "registries", "pypi"),
    _statuspage("crates", "crates.io", "https://status.crates.io", "registries", "crates"),
    _statuspage("rubygems", "RubyGems", "https://status.rubygems.org", "registries", "rubygems"),
    _statuspage("maven", "Maven Central", "https://status.maven.org", "registries", "maven"),
    _statuspage("sonatype", "Sonatype", "https://status.sonatype.com", "registries", "sonatype"),
    # Observability and incident management
    _statuspage("sentry", "Sentry", "https://status.sentry.io", "observability", "sentry"),
    _statuspage("datadog", "Datadog", "https://status.datadoghq.com", "observability", "datadog"),
    _statuspage("grafana", "Grafana Cloud", "https://status.grafana.com", "observability", "grafana"),
    _statuspage("newrelic", "New Relic", "https://status.newrelic.com", "observability", "newrelic"),
    _statuspage("honeycomb", "Honeycomb", "https://status.honeycomb.io", "observability",
                "honeycomb"),
    _statuspage("incidentio", "incident.io", "https://status.incident.io", "observability",
                "incidentio"),
    _statuspage("langfuse", "Langfuse", "https://status.langfuse.com", "observability", "langfuse"),
    _statuspage("braintrust", "Braintrust", "https://status.braintrust.dev", "observability",
                "braintrust"),
    # Workflow, identity, comms and team tools
    _statuspage("temporal", "Temporal", "https://status.temporal.io", "services", "temporal"),
    _statuspage("inngest", "Inngest", "https://status.inngest.com", "services", "inngest"),
    _statuspage("astronomer", "Astronomer", "https://status.astronomer.io", "services", "astronomer"),
    _statuspage("prefect", "Prefect", "https://status.prefect.io", "services", "prefect"),
    _statuspage("zapier", "Zapier", "https://status.zapier.com", "services", "zapier"),
    _statuspage("make", "Make", "https://status.make.com", "services", "make"),
    _statuspage("clerk", "Clerk", "https://status.clerk.com", "services", "clerk"),
    _statuspage("workos", "WorkOS", "https://status.workos.com", "services", "workos"),
    _statuspage("revenuecat", "RevenueCat", "https://status.revenuecat.com", "services",
                "revenuecat"),
    _statuspage("twilio", "Twilio", "https://status.twilio.com", "services", "twilio"),
    _statuspage("mailgun", "Mailgun", "https://status.mailgun.com", "services", "mailgun"),
    _statuspage("discord", "Discord", "https://discordstatus.com", "services", "discord"),
    _statuspage("linear", "Linear", "https://linearstatus.com", "services", "linear"),
    _statuspage("notion", "Notion", "https://www.notion-status.com", "services", "notion"),
    _statuspage("figma", "Figma", "https://status.figma.com", "services", "figma"),
    _statuspage("atlassian", "Atlassian", "https://status.atlassian.com", "services", "atlassian"),
    _statuspage("loom", "Loom", "https://loom.status.atlassian.com", "services", "loom"),
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
