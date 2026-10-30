import Foundation

/// How a service is doing overall, best to worst.
public enum ServiceHealth: Int, Sendable, Codable, Comparable, CaseIterable {
    case operational, maintenance, minor, major, critical

    public static func < (lhs: Self, rhs: Self) -> Bool { lhs.rawValue < rhs.rawValue }

    public var title: String {
        switch self {
        case .operational: "Operational"
        case .maintenance: "Under maintenance"
        case .minor: "Minor incident"
        case .major: "Major outage"
        case .critical: "Critical outage"
        }
    }

    /// Statuspage's `indicator` and incident `impact` vocabulary.
    init?(indicator: String) {
        switch indicator {
        case "none": self = .operational
        case "maintenance": self = .maintenance
        case "minor": self = .minor
        case "major": self = .major
        case "critical": self = .critical
        default: return nil
        }
    }
}

/// A component that is not fully up, worst last in raw order.
public enum ComponentState: Int, Sendable, Codable, Comparable {
    case maintenance, degraded, partialOutage, majorOutage

    public static func < (lhs: Self, rhs: Self) -> Bool { lhs.rawValue < rhs.rawValue }

    public var title: String {
        switch self {
        case .maintenance: "Under maintenance"
        case .degraded: "Degraded performance"
        case .partialOutage: "Partial outage"
        case .majorOutage: "Major outage"
        }
    }

    /// Statuspage's component `status`; nil for operational or unknown.
    init?(statuspage: String) {
        switch statuspage {
        case "under_maintenance": self = .maintenance
        case "degraded_performance": self = .degraded
        case "partial_outage": self = .partialOutage
        case "major_outage": self = .majorOutage
        default: return nil
        }
    }
}

public struct AffectedComponent: Sendable, Codable, Equatable, Hashable {
    public var name: String
    public var state: ComponentState

    public init(name: String, state: ComponentState) {
        self.name = name
        self.state = state
    }
}

/// An open incident, or a maintenance that is under way.
public struct ServiceIncident: Sendable, Codable, Equatable, Identifiable {
    public var id: String
    public var name: String
    public var impact: ServiceHealth
    /// Where the provider says it is: "Investigating", "Monitoring", "In progress".
    public var stage: String?
    public var startedAt: Date?
    /// The newest update's text, as the provider wrote it.
    public var latestUpdate: String?
    public var updatedAt: Date?
    public var url: URL?
    public var isMaintenance: Bool
}

/// One reading of a status page.
public struct StatusReport: Sendable, Codable, Equatable {
    public var health: ServiceHealth
    /// The page's own one-line summary ("Partially Degraded Service").
    public var summary: String?
    /// Components that are not operational, worst first.
    public var components: [AffectedComponent]
    /// Open incidents and maintenances under way, worst first.
    public var incidents: [ServiceIncident]

    public init(health: ServiceHealth, summary: String?, components: [AffectedComponent], incidents: [ServiceIncident]) {
        self.health = health
        self.summary = summary
        self.components = components
        self.incidents = incidents
    }
}

/// What is known about one service right now.
public struct ServiceStatus: Sendable, Equatable, Identifiable {
    public var id: String { service.id }
    public var service: StatusService
    /// Nil when there has been no reading in the last hour.
    public var report: StatusReport?
    /// When the page last answered. A reused reading keeps its own time.
    public var checkedAt: Date?
    /// Why the last attempt failed, if it did. Never shown as an outage.
    public var problem: String?
    /// True when `report` is an older reading shown because this check failed.
    public var isStale: Bool

    public init(service: StatusService, report: StatusReport?, checkedAt: Date?, problem: String?, isStale: Bool) {
        self.service = service
        self.report = report
        self.checkedAt = checkedAt
        self.problem = problem
        self.isStale = isStale
    }
}

/// A status page Quiver reads.
public struct StatusService: Sendable, Hashable, Identifiable {
    public enum Group: String, Sendable, Hashable, CaseIterable, Codable {
        /// The model APIs the harnesses run on.
        case modelProviders
        /// The agent products and coding platforms built on them.
        case agentPlatforms
        /// Hosting, edge and connectivity.
        case cloud
        /// Databases and data platforms.
        case data
        /// Source control, CI, quality and API tooling.
        case devtools
        /// Package registries.
        case registries
        /// Monitoring, tracing and incident management.
        case observability
        /// Workflow, identity, comms and team tools.
        case services

        /// Stored records from before the split: a page that read
        /// "providers" lands under model providers, one that read
        /// "infrastructure" under services, and anything unknown under
        /// services rather than failing the record's decode.
        public init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            switch raw {
            case "providers": self = .modelProviders
            case "infrastructure": self = .services
            default: self = Group(rawValue: raw) ?? .services
            }
        }

        /// The two provider groups are the only ones that reach the
        /// menu bar on a minor incident.
        var isProvider: Bool {
            self == .modelProviders || self == .agentPlatforms
        }

        public var title: String {
            switch self {
            case .modelProviders: "Model providers"
            case .agentPlatforms: "Agent platforms"
            case .cloud: "Cloud & network"
            case .data: "Data"
            case .devtools: "Dev tools & quality"
            case .registries: "Registries"
            case .observability: "Observability"
            case .services: "Services & productivity"
            }
        }

        /// The group's name in a picker chip, where `title` won't fit.
        public var shortTitle: String {
            switch self {
            case .modelProviders: "Models"
            case .agentPlatforms: "Agents"
            case .cloud: "Cloud"
            case .data: "Data"
            case .devtools: "Dev tools"
            case .registries: "Registries"
            case .observability: "Monitoring"
            case .services: "Services"
            }
        }
    }

    public enum Feed: Sendable, Hashable {
        /// Atlassian Statuspage's `/api/v2/summary.json` (incident.io serves the same shape).
        case statuspage(URL)
        /// Google Cloud's `incidents.json`, narrowed to products matched by
        /// id, title or current title.
        case googleCloud(URL, products: Set<String>)
    }

    public var id: String
    public var name: String
    /// The page a person reads.
    public var pageURL: URL
    public var feed: Feed
    public var group: Group
    /// A page the user added rather than a built-in entry. Customs are the
    /// only ones that can be removed, and they persist in preferences.
    public var isCustom = false
    /// Where the reading comes from, when that is not obvious from the name.
    public var note: String?

    public var feedURL: URL {
        switch feed {
        case .statuspage(let url), .googleCloud(let url, _): url
        }
    }

    /// The feed's host, for messages ("Couldn't reach status.claude.com").
    public var host: String { feedURL.host() ?? feedURL.absoluteString }

    /// Status pages whose logo is deliberately filed under a harness's
    /// id, ahead of any brand match: githubstatus covers Copilot here,
    /// so it shows the Copilot mark even though a GitHub logo exists.
    static let logoIDs = ["github": "copilot"]

    /// The bundled mark for this service, or nil when there is none. A
    /// custom page is matched on its name first ("OpenRouter" finds
    /// openrouter), then the catalogue entry its page came from, by the
    /// feed's host — a one-click add lands under a fresh custom id, which
    /// matches nothing below. Other services match on their id.
    public var logoID: String? {
        if isCustom, let id = APIKeyProviderLogo.logoID(for: name) { return id }
        if isCustom, let logo = StatusCatalogue.all.first(where: { $0.host == host })?.logoID {
            return logo
        }
        if let id = Self.logoIDs[id], HarnessIcons.icon(for: id) != nil { return id }
        // A company's page shows its brand logo (OpenAI's for "openai"),
        // the one every other page draws for that company.
        if let icon = ModelBrands.brand(id: id)?.icon, HarnessIcons.icon(for: icon) != nil { return icon }
        return HarnessIcons.icon(for: id) != nil ? id : nil
    }

    /// The service an edit to a built-in turns it into. A `pageURL` edit
    /// on a Statuspage service moves its feed to the checked one the edit
    /// sheet probed (or the derived `api/v2/summary.json` when none is
    /// given); feeds of other kinds keep reading the catalog's.
    public func applying(_ edit: StatusServicePrefs.Edit?) -> StatusService {
        guard let edit else { return self }
        var s = self
        if let name = edit.name { s.name = name }
        if let pageURL = edit.pageURL { s.pageURL = pageURL }
        if let group = edit.group { s.group = group }
        if case .statuspage = s.feed, edit.pageURL != nil || edit.feedURL != nil {
            s.feed = .statuspage(edit.feedURL ?? s.pageURL.appending(path: "api/v2/summary.json"))
        }
        return s
    }

    /// "custom-groq-cloud-a1b2c3d4" for "Groq Cloud": lowercased,
    /// non-alphanumerics as "-", trimmed, then a random suffix so a removed
    /// page's id is never reused (its poll cache is keyed by id). The "-2",
    /// "-3"... loop stays as a backstop.
    static func customID(for name: String, taken: Set<String>,
                         suffix: () -> String = { String(UUID().uuidString.prefix(8)).lowercased() }) -> String {
        let slug = name.lowercased()
            .map { $0.isLetter || $0.isNumber ? $0 : "-" }
            .reduce(into: "") { if $1 != "-" || $0.last != "-" { $0.append($1) } }
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let base = "custom-\(slug.isEmpty ? "page" : slug)-\(suffix())"
        var id = base, n = 2
        while taken.contains(id) { id = "\(base)-\(n)"; n += 1 }
        return id
    }

    private static func statuspage(_ id: String, _ name: String, _ base: String, _ group: Group) -> StatusService {
        let page = URL(string: base)!
        return StatusService(id: id, name: name, pageURL: page,
                             feed: .statuspage(page.appending(path: "api/v2/summary.json")), group: group)
    }

    /// Every service on the page, in order. Each URL is the final one: the
    /// transport follows no redirects, so status.anthropic.com (which
    /// redirects to status.claude.com) would fail. Cursor's page moved to
    /// status.x.ai, which serves the Statuspage shape under /api/compat.
    public static let all: [StatusService] = [
        statuspage("claude", "Claude", "https://status.claude.com", .modelProviders),
        statuspage("openai", "OpenAI / Codex", "https://status.openai.com", .modelProviders),
        statuspage("github", "GitHub / Copilot", "https://www.githubstatus.com", .agentPlatforms),
        StatusService(id: "cursor", name: "Cursor", pageURL: URL(string: "https://status.x.ai")!,
                      feed: .statuspage(URL(string: "https://status.x.ai/api/compat/summary.json")!),
                      group: .agentPlatforms),
        // AI Studio's status page has no JSON behind it; Google Cloud's feed
        // covers the Gemini API on Vertex (renamed "Gemini on Agent Platform").
        StatusService(
            id: "gemini", name: "Gemini", pageURL: URL(string: "https://aistudio.google.com/status")!,
            feed: .googleCloud(URL(string: "https://status.cloud.google.com/incidents.json")!,
                               products: ["Z0FZJAMvEB4j3NbCJs6B", "Vertex Gemini API", "Gemini on Agent Platform"]),
            group: .modelProviders, note: "Gemini API incidents from Google Cloud's status feed"),
        statuspage("devin", "Devin", "https://www.devinstatus.com", .agentPlatforms),
        statuspage("npm", "npm", "https://status.npmjs.org", .registries),
        statuspage("vercel", "Vercel", "https://www.vercel-status.com", .cloud),
        statuspage("cloudflare", "Cloudflare", "https://www.cloudflarestatus.com", .cloud),
    ]
}

/// Short summaries for the menu bar.
public enum ServiceStatusSummary {
    /// The worst known state among the provider groups and any other
    /// service `affected` names, so the dot beside the line matches it;
    /// nil when none is known.
    public static func worst(_ statuses: [ServiceStatus]) -> ServiceHealth? {
        statuses.compactMap { status -> ServiceHealth? in
            guard let health = status.report?.health else { return nil }
            return status.service.group.isProvider || health >= threshold(for: status.service.group)
                ? health : nil
        }.max()
    }

    /// The providers with an incident, plus other groups with a major or
    /// worse outage (a minor npm or Vercel blip isn't worth the line),
    /// worst first and in catalog order among equals; empty when nothing
    /// clears that bar.
    public static func affected(_ statuses: [ServiceStatus]) -> [ServiceStatus] {
        statuses
            .filter { ($0.report?.health ?? .operational) >= threshold(for: $0.service.group) }
            .enumerated()
            .sorted { ($0.element.report!.health, -$0.offset) > ($1.element.report!.health, -$1.offset) }
            .map(\.element)
    }

    /// The least a service's state must be to reach the menu line.
    static func threshold(for group: StatusService.Group) -> ServiceHealth {
        group.isProvider ? .minor : .major
    }

    /// "Claude: minor incident" for every `affected` service, worst first;
    /// nil when none is.
    public static func menuLine(_ statuses: [ServiceStatus]) -> String? {
        let hit = affected(statuses)
        guard let first = hit.first else { return nil }
        func part(_ status: ServiceStatus) -> String {
            "\(status.service.name): \(status.report!.health.title.lowercased())"
        }
        switch hit.count {
        case 1: return part(first)
        case 2: return "\(part(first)), \(part(hit[1]))"
        default: return "\(part(first)), and \(hit.count - 1) more"
        }
    }
}
