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
