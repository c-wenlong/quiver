import Foundation

/// The catalogue behind the "one-click add" sheet: every built-in first —
/// so a removed or edited page can be tracked again from the same list —
/// then the common status pages, each verified to serve a Statuspage
/// `summary.json`. Every entry is a plain `StatusService`, so a row's
/// logo resolves the same way an added card's does. Each URL is the
/// page's final one, since the transport follows no redirects; pages
/// checked and rejected (betterstack, status.io, Cloudflare-challenged
/// or custom feeds like AWS's) are not listed.
public enum StatusCatalogue {
    private static func statuspage(_ id: String, _ name: String, _ base: String,
                                   _ group: StatusService.Group) -> StatusService {
        let page = URL(string: base)!
        return StatusService(id: id, name: name, pageURL: page,
                             feed: .statuspage(page.appending(path: "api/v2/summary.json")),
                             group: group)
    }

    /// The pages beyond the built-ins the sheet offers. The ids are for
    /// the catalogue row only: `model.add` gives every entry a fresh
    /// custom id when it lands on the page.
    private static let extra: [StatusService] = [
        // Model API providers
        statuspage("groq", "Groq", "https://groqstatus.com", .modelProviders),
        statuspage("perplexity", "Perplexity", "https://status.perplexity.com", .modelProviders),
        statuspage("fireworks", "Fireworks AI", "https://status.fireworks.ai", .modelProviders),
        statuspage("cerebras", "Cerebras", "https://status.cerebras.ai", .modelProviders),
        statuspage("cohere", "Cohere", "https://status.cohere.com", .modelProviders),
        statuspage("baseten", "Baseten", "https://status.baseten.co", .modelProviders),
        statuspage("minimax", "MiniMax", "https://status.minimax.io", .modelProviders),
        statuspage("elevenlabs", "ElevenLabs", "https://status.elevenlabs.io", .modelProviders),
        statuspage("lambda", "Lambda", "https://status.lambda.ai", .modelProviders),
        statuspage("assemblyai", "AssemblyAI", "https://status.assemblyai.com", .modelProviders),
        statuspage("mistral", "Mistral AI", "https://status.mistral.ai", .modelProviders),
        statuspage("moonshot", "Moonshot AI", "https://status.moonshot.cn", .modelProviders),
        statuspage("nebius", "Nebius", "https://status.nebius.com", .modelProviders),
        statuspage("stability", "Stability AI", "https://status.stability.ai", .modelProviders),
        statuspage("ai21", "AI21", "https://status.ai21.com", .modelProviders),
        statuspage("sambanova", "SambaNova", "https://status.sambanova.ai", .modelProviders),
        statuspage("poe", "Poe", "https://status.poe.com", .modelProviders),
        statuspage("writer", "Writer", "https://status.writer.com", .modelProviders),
        statuspage("cartesia", "Cartesia", "https://status.cartesia.ai", .modelProviders),
        statuspage("deepgram", "Deepgram", "https://status.deepgram.com", .modelProviders),
        statuspage("nvidia", "NVIDIA NGC", "https://status.ngc.nvidia.com", .modelProviders),
        statuspage("anyscale", "Anyscale", "https://status.anyscale.com", .modelProviders),
        // Agent products and coding platforms
        statuspage("droid", "Factory", "https://status.factory.ai", .agentPlatforms),
        statuspage("windsurf", "Windsurf", "https://status.windsurf.com", .agentPlatforms),
        statuspage("replit", "Replit", "https://status.replit.com", .agentPlatforms),
        statuspage("sourcegraph", "Sourcegraph", "https://sourcegraphstatus.com", .agentPlatforms),
        statuspage("augment", "Augment Code", "https://status.augmentcode.com", .agentPlatforms),
        statuspage("warp", "Warp", "https://status.warp.dev", .agentPlatforms),
        statuspage("manus", "Manus", "https://status.manus.im", .agentPlatforms),
        statuspage("lovable", "Lovable", "https://status.lovable.dev", .agentPlatforms),
        statuspage("blackbox", "Blackbox", "https://status.blackbox.ai", .agentPlatforms),
        statuspage("kilo", "Kilo Code", "https://status.kilo.ai", .agentPlatforms),
        statuspage("ona", "Ona", "https://onastatus.com", .agentPlatforms),
        statuspage("e2b", "E2B", "https://status.e2b.dev", .agentPlatforms),
        statuspage("browserbase", "Browserbase", "https://status.browserbase.com", .agentPlatforms),
        statuspage("tavily", "Tavily", "https://status.tavily.com", .agentPlatforms),
        statuspage("runloop", "Runloop", "https://status.runloop.ai", .agentPlatforms),
        statuspage("arcade", "Arcade", "https://status.arcade.dev", .agentPlatforms),
        statuspage("morph", "Morph", "https://status.morphllm.com", .agentPlatforms),
        statuspage("supermemory", "Supermemory", "https://status.supermemory.ai", .agentPlatforms),
        statuspage("bolt", "Bolt.new", "https://status.bolt.new", .agentPlatforms),
        statuspage("v0", "v0", "https://v0-status.com", .agentPlatforms),
        // Cloud hosting, edge and connectivity
        statuspage("digitalocean", "DigitalOcean", "https://status.digitalocean.com", .cloud),
        statuspage("render", "Render", "https://status.render.com", .cloud),
        statuspage("netlify", "Netlify", "https://www.netlifystatus.com", .cloud),
        statuspage("flyio", "Fly.io", "https://status.flyio.net", .cloud),
        statuspage("linode", "Linode", "https://status.linode.com", .cloud),
        statuspage("expo", "Expo", "https://status.expo.dev", .cloud),
        statuspage("tailscale", "Tailscale", "https://status.tailscale.com", .cloud),
        statuspage("ngrok", "ngrok", "https://status.ngrok.com", .cloud),
        // Databases and data platforms
        statuspage("supabase", "Supabase", "https://status.supabase.com", .data),
        statuspage("convex", "Convex", "https://status.convex.dev", .data),
        statuspage("upstash", "Upstash", "https://status.upstash.com", .data),
        statuspage("mongodb", "MongoDB Atlas", "https://status.mongodb.com", .data),
        statuspage("pinecone", "Pinecone", "https://status.pinecone.io", .data),
        statuspage("prisma", "Prisma", "https://www.prisma-status.com", .data),
        statuspage("sanity", "Sanity", "https://www.sanity-status.com", .data),
        statuspage("contentful", "Contentful", "https://www.contentfulstatus.com", .data),
        statuspage("snowflake", "Snowflake", "https://status.snowflake.com", .data),
        statuspage("clickhouse", "ClickHouse", "https://status.clickhouse.com", .data),
        statuspage("cockroach", "CockroachDB", "https://status.cockroachlabs.cloud", .data),
        statuspage("planetscale", "PlanetScale", "https://www.planetscalestatus.com", .data),
        // Source control, CI, quality and API tooling
        statuspage("bitbucket", "Bitbucket", "https://bitbucket.status.atlassian.com", .devtools),
        statuspage("circleci", "CircleCI", "https://status.circleci.com", .devtools),
        statuspage("buildkite", "Buildkite", "https://www.buildkitestatus.com", .devtools),
        statuspage("travisci", "Travis CI", "https://www.traviscistatus.com", .devtools),
        statuspage("semaphore", "Semaphore", "https://status.semaphore.io", .devtools),
        statuspage("postman", "Postman", "https://status.postman.com", .devtools),
        statuspage("hashicorp", "HashiCorp", "https://status.hashicorp.com", .devtools),
        statuspage("snyk", "Snyk", "https://status.snyk.io", .devtools),
        statuspage("codecov", "Codecov", "https://status.codecov.com", .devtools),
        statuspage("launchdarkly", "LaunchDarkly", "https://status.launchdarkly.com", .devtools),
        statuspage("statsig", "Statsig", "https://status.statsig.com", .devtools),
        // Package registries
        statuspage("docker", "Docker Hub", "https://www.dockerstatus.com", .registries),
        statuspage("pypi", "PyPI", "https://status.python.org", .registries),
        statuspage("crates", "crates.io", "https://status.crates.io", .registries),
        statuspage("rubygems", "RubyGems", "https://status.rubygems.org", .registries),
        statuspage("maven", "Maven Central", "https://status.maven.org", .registries),
        statuspage("sonatype", "Sonatype", "https://status.sonatype.com", .registries),
        // Observability and incident management
        statuspage("sentry", "Sentry", "https://status.sentry.io", .observability),
        statuspage("datadog", "Datadog", "https://status.datadoghq.com", .observability),
        statuspage("grafana", "Grafana Cloud", "https://status.grafana.com", .observability),
        statuspage("newrelic", "New Relic", "https://status.newrelic.com", .observability),
        statuspage("honeycomb", "Honeycomb", "https://status.honeycomb.io", .observability),
        statuspage("incidentio", "incident.io", "https://status.incident.io", .observability),
        statuspage("langfuse", "Langfuse", "https://status.langfuse.com", .observability),
        statuspage("braintrust", "Braintrust", "https://status.braintrust.dev", .observability),
        // Workflow, identity, comms and team tools
        statuspage("temporal", "Temporal", "https://status.temporal.io", .services),
        statuspage("inngest", "Inngest", "https://status.inngest.com", .services),
        statuspage("astronomer", "Astronomer", "https://status.astronomer.io", .services),
        statuspage("prefect", "Prefect", "https://status.prefect.io", .services),
        statuspage("zapier", "Zapier", "https://status.zapier.com", .services),
        statuspage("make", "Make", "https://status.make.com", .services),
        statuspage("clerk", "Clerk", "https://status.clerk.com", .services),
        statuspage("workos", "WorkOS", "https://status.workos.com", .services),
        statuspage("revenuecat", "RevenueCat", "https://status.revenuecat.com", .services),
        statuspage("twilio", "Twilio", "https://status.twilio.com", .services),
        statuspage("mailgun", "Mailgun", "https://status.mailgun.com", .services),
        statuspage("discord", "Discord", "https://discordstatus.com", .services),
        statuspage("linear", "Linear", "https://linearstatus.com", .services),
        statuspage("notion", "Notion", "https://www.notion-status.com", .services),
        statuspage("figma", "Figma", "https://status.figma.com", .services),
        statuspage("atlassian", "Atlassian", "https://status.atlassian.com", .services),
        statuspage("loom", "Loom", "https://loom.status.atlassian.com", .services),
    ]

    /// The built-ins, then the extra pages, in display order. Built-in
    /// rows keep their catalog ids, so `model.track` on one restores the
    /// catalog's entry rather than adding a custom copy.
    public static let all: [StatusService] = StatusService.all + extra

    /// The entries passing the chip pick and the search; the query
    /// matches the name and the feed's host ("docker" finds Docker Hub by
    /// name, "notion-status" by its page's host).
    public static func filter(_ entries: [StatusService] = all,
                              group: StatusService.Group?, query: String) -> [StatusService] {
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        return entries.filter { entry in
            if let group, entry.group != group { return false }
            guard !needle.isEmpty else { return true }
            return [entry.name, entry.host]
                .contains { $0.lowercased().contains(needle) }
        }
    }
}
