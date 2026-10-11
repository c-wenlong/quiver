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
    public enum Group: Sendable, Hashable, CaseIterable {
        /// The model providers and tools the harnesses run on. Only these
        /// reach the menu bar.
        case providers
        /// What installing and shipping depends on.
        case infrastructure

        public var title: String {
            switch self {
            case .providers: "AI providers"
            case .infrastructure: "Infrastructure"
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
    /// Where the reading comes from, when that is not obvious from the name.
    public var note: String?

    public var feedURL: URL {
        switch feed {
        case .statuspage(let url), .googleCloud(let url, _): url
        }
    }

    /// The feed's host, for messages ("Couldn't reach status.claude.com").
    public var host: String { feedURL.host() ?? feedURL.absoluteString }

    /// Status pages whose logo is filed under the harness's own id.
    static let logoIDs = ["openai": "codex", "github": "copilot"]

    /// The bundled harness logo for this service, or nil when there is none
    /// (npm, Vercel and Cloudflare are not harnesses, so they show no icon).
    public var logoID: String? {
        let id = Self.logoIDs[id] ?? id
        return HarnessIcons.icon(for: id) != nil ? id : nil
    }

    private static func statuspage(_ id: String, _ name: String, _ base: String, _ group: Group) -> StatusService {
        let page = URL(string: base)!
        return StatusService(id: id, name: name, pageURL: page,
                             feed: .statuspage(page.appending(path: "api/v2/summary.json")), group: group)
    }

    /// Every service on the page, in order. Each URL is the final one: the
    /// transport follows no redirects, so status.anthropic.com (which
    /// redirects to status.claude.com) would fail.
    public static let all: [StatusService] = [
        statuspage("claude", "Claude", "https://status.claude.com", .providers),
        statuspage("openai", "OpenAI / Codex", "https://status.openai.com", .providers),
        statuspage("github", "GitHub / Copilot", "https://www.githubstatus.com", .providers),
        statuspage("cursor", "Cursor", "https://status.cursor.com", .providers),
        // AI Studio's status page has no JSON behind it; Google Cloud's feed
        // covers the Gemini API on Vertex (renamed "Gemini on Agent Platform").
        StatusService(
            id: "gemini", name: "Gemini", pageURL: URL(string: "https://aistudio.google.com/status")!,
            feed: .googleCloud(URL(string: "https://status.cloud.google.com/incidents.json")!,
                               products: ["Z0FZJAMvEB4j3NbCJs6B", "Vertex Gemini API", "Gemini on Agent Platform"]),
            group: .providers, note: "Gemini API incidents from Google Cloud's status feed"),
        statuspage("devin", "Devin", "https://www.devinstatus.com", .providers),
        statuspage("npm", "npm", "https://status.npmjs.org", .infrastructure),
        statuspage("vercel", "Vercel", "https://www.vercel-status.com", .infrastructure),
        statuspage("cloudflare", "Cloudflare", "https://www.cloudflarestatus.com", .infrastructure),
    ]
}

/// Short summaries for the menu bar.
public enum ServiceStatusSummary {
    /// The worst known state among the providers, or nil when none is known.
    public static func worst(_ statuses: [ServiceStatus]) -> ServiceHealth? {
        statuses.filter { $0.service.group == .providers }.compactMap(\.report?.health).max()
    }

    /// The providers with an incident, worst first and in catalog order
    /// among equals; empty when every provider is up or under maintenance.
    public static func affected(_ statuses: [ServiceStatus]) -> [ServiceStatus] {
        statuses
            .filter { $0.service.group == .providers && ($0.report?.health ?? .operational) >= .minor }
            .enumerated()
            .sorted { ($0.element.report!.health, -$0.offset) > ($1.element.report!.health, -$1.offset) }
            .map(\.element)
    }

    /// "Claude: minor incident" for providers with an incident, worst first;
    /// nil when every provider is up or under maintenance.
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
