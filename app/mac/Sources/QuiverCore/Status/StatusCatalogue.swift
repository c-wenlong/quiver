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
        statuspage("groq", "Groq", "https://groqstatus.com", .providers),
        statuspage("perplexity", "Perplexity", "https://status.perplexity.com", .providers),
        statuspage("fireworks", "Fireworks AI", "https://status.fireworks.ai", .providers),
        statuspage("cerebras", "Cerebras", "https://status.cerebras.ai", .providers),
        statuspage("cohere", "Cohere", "https://status.cohere.com", .providers),
        statuspage("baseten", "Baseten", "https://status.baseten.co", .providers),
        statuspage("minimax", "MiniMax", "https://status.minimax.io", .providers),
        statuspage("elevenlabs", "ElevenLabs", "https://status.elevenlabs.io", .providers),
        statuspage("lambda", "Lambda", "https://status.lambda.ai", .providers),
        statuspage("assemblyai", "AssemblyAI", "https://status.assemblyai.com", .providers),
        // Model API providers
        statuspage("mistral", "Mistral AI", "https://status.mistral.ai", .providers),
        statuspage("moonshot", "Moonshot AI", "https://status.moonshot.cn", .providers),
        statuspage("nebius", "Nebius", "https://status.nebius.com", .providers),
        statuspage("stability", "Stability AI", "https://status.stability.ai", .providers),
        statuspage("ai21", "AI21", "https://status.ai21.com", .providers),
        statuspage("sambanova", "SambaNova", "https://status.sambanova.ai", .providers),
        statuspage("poe", "Poe", "https://status.poe.com", .providers),
        statuspage("writer", "Writer", "https://status.writer.com", .providers),
        statuspage("cartesia", "Cartesia", "https://status.cartesia.ai", .providers),
        statuspage("deepgram", "Deepgram", "https://status.deepgram.com", .providers),
        // Agent products and coding platforms
        statuspage("droid", "Factory", "https://status.factory.ai", .providers),
        statuspage("windsurf", "Windsurf", "https://status.windsurf.com", .providers),
        statuspage("replit", "Replit", "https://status.replit.com", .providers),
        statuspage("sourcegraph", "Sourcegraph", "https://sourcegraphstatus.com", .providers),
        statuspage("augment", "Augment Code", "https://status.augmentcode.com", .providers),
        statuspage("warp", "Warp", "https://status.warp.dev", .providers),
        statuspage("manus", "Manus", "https://status.manus.im", .providers),
        statuspage("lovable", "Lovable", "https://status.lovable.dev", .providers),
        statuspage("blackbox", "Blackbox", "https://status.blackbox.ai", .providers),
        statuspage("kilo", "Kilo Code", "https://status.kilo.ai", .providers),
        statuspage("ona", "Ona", "https://onastatus.com", .providers),
        // Agent tooling and sandbox platforms
        statuspage("e2b", "E2B", "https://status.e2b.dev", .providers),
        statuspage("browserbase", "Browserbase", "https://status.browserbase.com", .providers),
        statuspage("tavily", "Tavily", "https://status.tavily.com", .providers),
        statuspage("runloop", "Runloop", "https://status.runloop.ai", .providers),
        statuspage("arcade", "Arcade", "https://status.arcade.dev", .providers),
        statuspage("morph", "Morph", "https://status.morphllm.com", .providers),
        statuspage("supermemory", "Supermemory", "https://status.supermemory.ai", .providers),
        statuspage("docker", "Docker Hub", "https://www.dockerstatus.com", .infrastructure),
        statuspage("tailscale", "Tailscale", "https://status.tailscale.com", .infrastructure),
        statuspage("sentry", "Sentry", "https://status.sentry.io", .infrastructure),
        statuspage("datadog", "Datadog", "https://status.datadoghq.com", .infrastructure),
        statuspage("supabase", "Supabase", "https://status.supabase.com", .infrastructure),
        statuspage("digitalocean", "DigitalOcean", "https://status.digitalocean.com", .infrastructure),
        statuspage("render", "Render", "https://status.render.com", .infrastructure),
        statuspage("upstash", "Upstash", "https://status.upstash.com", .infrastructure),
        statuspage("convex", "Convex", "https://status.convex.dev", .infrastructure),
        statuspage("linear", "Linear", "https://linearstatus.com", .infrastructure),
        statuspage("notion", "Notion", "https://www.notion-status.com", .infrastructure),
        statuspage("twilio", "Twilio", "https://status.twilio.com", .infrastructure),
        statuspage("clerk", "Clerk", "https://status.clerk.com", .infrastructure),
        // Package registries
        statuspage("pypi", "PyPI", "https://status.python.org", .infrastructure),
        statuspage("crates", "crates.io", "https://status.crates.io", .infrastructure),
        statuspage("rubygems", "RubyGems", "https://status.rubygems.org", .infrastructure),
        statuspage("maven", "Maven Central", "https://status.maven.org", .infrastructure),
        // Source control and CI
        statuspage("bitbucket", "Bitbucket", "https://bitbucket.status.atlassian.com", .infrastructure),
        statuspage("circleci", "CircleCI", "https://status.circleci.com", .infrastructure),
        statuspage("buildkite", "Buildkite", "https://www.buildkitestatus.com", .infrastructure),
        statuspage("travisci", "Travis CI", "https://www.traviscistatus.com", .infrastructure),
        // Hosting and app platforms
        statuspage("netlify", "Netlify", "https://www.netlifystatus.com", .infrastructure),
        statuspage("flyio", "Fly.io", "https://status.flyio.net", .infrastructure),
        statuspage("linode", "Linode", "https://status.linode.com", .infrastructure),
        statuspage("expo", "Expo", "https://status.expo.dev", .infrastructure),
        // Data platforms
        statuspage("mongodb", "MongoDB Atlas", "https://status.mongodb.com", .infrastructure),
        statuspage("pinecone", "Pinecone", "https://status.pinecone.io", .infrastructure),
        statuspage("prisma", "Prisma", "https://www.prisma-status.com", .infrastructure),
        statuspage("sanity", "Sanity", "https://www.sanity-status.com", .infrastructure),
        statuspage("contentful", "Contentful", "https://www.contentfulstatus.com", .infrastructure),
        // Workflow and job platforms
        statuspage("temporal", "Temporal", "https://status.temporal.io", .infrastructure),
        statuspage("inngest", "Inngest", "https://status.inngest.com", .infrastructure),
        statuspage("astronomer", "Astronomer", "https://status.astronomer.io", .infrastructure),
        // Observability and incident management
        statuspage("grafana", "Grafana Cloud", "https://status.grafana.com", .infrastructure),
        statuspage("newrelic", "New Relic", "https://status.newrelic.com", .infrastructure),
        statuspage("honeycomb", "Honeycomb", "https://status.honeycomb.io", .infrastructure),
        statuspage("incidentio", "incident.io", "https://status.incident.io", .infrastructure),
        // Feature flags and code quality
        statuspage("launchdarkly", "LaunchDarkly", "https://status.launchdarkly.com", .infrastructure),
        statuspage("statsig", "Statsig", "https://status.statsig.com", .infrastructure),
        statuspage("snyk", "Snyk", "https://status.snyk.io", .infrastructure),
        statuspage("codecov", "Codecov", "https://status.codecov.com", .infrastructure),
        // API tooling and misc platforms
        statuspage("postman", "Postman", "https://status.postman.com", .infrastructure),
        statuspage("ngrok", "ngrok", "https://status.ngrok.com", .infrastructure),
        statuspage("hashicorp", "HashiCorp", "https://status.hashicorp.com", .infrastructure),
        statuspage("figma", "Figma", "https://status.figma.com", .infrastructure),
        statuspage("atlassian", "Atlassian", "https://status.atlassian.com", .infrastructure),
        statuspage("workos", "WorkOS", "https://status.workos.com", .infrastructure),
        statuspage("mailgun", "Mailgun", "https://status.mailgun.com", .infrastructure),
        statuspage("discord", "Discord", "https://discordstatus.com", .infrastructure),
        statuspage("revenuecat", "RevenueCat", "https://status.revenuecat.com", .infrastructure),
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
