import Foundation
import Observation

/// One `GET /v1/inbox` row. The hub applies its age cutoff and this
/// device's dismissals before rows ship: approvals always arrive,
/// follow-ups, errors and interrupts only while fresh, `done` rows
/// (finished since the hub's midnight) only on `?done=1`. The iPhone
/// app's `InboxItem` (app/ios) reads the same shape.
public struct HubInboxItem: Codable, Sendable, Equatable, Identifiable {
    public var key: String
    public var machineId: String?
    public var harness: String?
    public var agent: String?
    public var title: String?
    public var cwd: String?
    public var status: String?
    public var lastActiveAt: Double?
    public var requestId: String?
    /// "github" on an unread GitHub notification row; session rows omit it.
    public var kind: String?

    public init(key: String, machineId: String? = nil, harness: String? = nil, agent: String? = nil,
                title: String? = nil, cwd: String? = nil, status: String? = nil,
                lastActiveAt: Double? = nil, requestId: String? = nil, kind: String? = nil) {
        self.key = key
        self.machineId = machineId
        self.harness = harness
        self.agent = agent
        self.title = title
        self.cwd = cwd
        self.status = status
        self.lastActiveAt = lastActiveAt
        self.requestId = requestId
        self.kind = kind
    }

    public var id: String { key }
    public var displayTitle: String { (title?.isEmpty == false ? title : nil) ?? "Untitled session" }
    /// An unread GitHub thread (`gh:<thread id>`). The Mac lists those
    /// from `swe gh inbox` already, so Needs you leaves them out.
    public var isGitHub: Bool { kind == "github" || status == "github" }
    /// A pending approval: it blocks the agent, so the hub keeps it in
    /// the inbox whatever a device clears, and Clear never offers it.
    public var isApproval: Bool { status == "approval" }
    /// What a dismiss posts back as `last_active_at`.
    public var dismissStamp: Double { lastActiveAt ?? 0 }
}

struct HubInboxItems: Decodable { let items: [HubInboxItem] }

extension HubClient {
    /// `GET /v1/inbox`: this device's undismissed pending rows; `done`
    /// adds the sessions that finished since the hub's midnight.
    public func inbox(done: Bool) async throws -> [HubInboxItem] {
        try await send(request("GET", "/v1/inbox", query: done ? ["done": "1"] : [:]),
                       as: HubInboxItems.self).items
    }

    /// `POST /v1/inbox/dismiss {key, last_active_at}`: hides the row for
    /// this device until its `last_active_at` moves past the stamp.
    public func dismissInbox(_ key: String, lastActiveAt: Double) async throws {
        _ = try await raw(request("POST", "/v1/inbox/dismiss",
                                  body: ["key": key, "last_active_at": lastActiveAt]))
    }
}

/// The Inbox page's Needs you sections, in page order. The hub decides
/// which rows ship, so this only sorts what arrived, as the iPhone's
/// `InboxGrouping` does. An unfamiliar status lands in Waiting on you
/// rather than vanishing.
public enum NeedsYouSection: CaseIterable, Sendable {
    case waiting, errors, finished

    public var title: String {
        switch self {
        case .waiting: "Waiting on you"
        case .errors: "Errors"
        case .finished: "Finished today"
        }
    }

    /// What the section's Clear button clears, for its hover tip.
    public var clearTitle: String {
        switch self {
        case .waiting: "waiting on you"
        case .errors: "errors"
        case .finished: "finished"
        }
    }

    /// The section a row belongs in; nil for a GitHub row.
    public static func of(_ item: HubInboxItem) -> NeedsYouSection? {
        if item.isGitHub { return nil }
        switch item.status {
        case "error", "interrupted": return .errors
        case "done": return .finished
        default: return .waiting
        }
    }

    /// `items` bucketed by section, sections in page order, empty ones
    /// left out.
    public static func groups(_ items: [HubInboxItem]) -> [(section: NeedsYouSection, items: [HubInboxItem])] {
        var grouped: [NeedsYouSection: [HubInboxItem]] = [:]
        for item in items {
            if let section = of(item) { grouped[section, default: []].append(item) }
        }
        return allCases.compactMap { s in grouped[s].map { (s, $0) } }
    }

    /// A row's status in the Sessions page's words.
    public static func statusLabel(_ item: HubInboxItem) -> String {
        switch item.status {
        case "approval": "Approval needed"
        case "followup": "Waiting"
        case "error": "Error"
        case "interrupted": "Interrupted"
        case "done": "Finished"
        case let other?: other.isEmpty ? "Waiting" : other.prefix(1).uppercased() + other.dropFirst()
        case nil: "Waiting"
        }
    }
}

/// Dismissals posted but possibly not yet reflected in a fetched list:
/// a poll that left before the dismiss landed would put the row back.
/// An entry filters fetched rows whose `lastActiveAt` has not moved past
/// the stamp posted; a row that did move past it has new activity,
/// returns legitimately, and retires the entry. The iPhone app's
/// `DismissalLedger` follows the same rule, which is the hub's. A
/// pending approval always passes, as the hub ships one whatever was
/// dismissed: a session cleared a moment ago can ask for approval
/// without its activity stamp moving.
public struct InboxDismissals: Sendable {
    public private(set) var stamps: [String: Double] = [:]

    public init() {}

    public mutating func choose(_ key: String, stamp: Double) { stamps[key] = stamp }
    public mutating func drop(_ key: String) { stamps[key] = nil }

    public mutating func overlay(_ items: [HubInboxItem]) -> [HubInboxItem] {
        guard !stamps.isEmpty else { return items }
        var out: [HubInboxItem] = []
        out.reserveCapacity(items.count)
        for item in items {
            guard !item.isApproval, let stamp = stamps[item.key] else { out.append(item); continue }
            if item.dismissStamp > stamp {
                stamps[item.key] = nil
                out.append(item)
            }
        }
        return out
    }
}

/// The Inbox page's Needs you rows from this Mac's hub. Any failure,
/// a hub that isn't running included, empties the list: the section
/// then shows nothing rather than an error, since the GitHub list
/// below it still works.
@MainActor
@Observable
public final class NeedsYouModel {
    /// Session rows only; GitHub rows never land here.
    public private(set) var items: [HubInboxItem] = []
    /// The last fetch answered. A page polls less often while it doesn't.
    public private(set) var reachable = false
    private var dismissals = InboxDismissals()
    /// Bumped by every load and dismiss, so a slow fetch that left
    /// before a newer change can't land over it.
    private var generation = 0

    public init() {}

    public var groups: [(section: NeedsYouSection, items: [HubInboxItem])] {
        NeedsYouSection.groups(items)
    }

    public func load(_ fetch: () async throws -> [HubInboxItem]) async {
        generation += 1
        let mine = generation
        do {
            let fetched = try await fetch()
            guard mine == generation else { return }
            items = dismissals.overlay(fetched.filter { !$0.isGitHub })
            reachable = true
        } catch is CancellationError {
            return
        } catch {
            guard mine == generation else { return }
            items = []
            reachable = false
        }
    }

    /// Clears `rows` for this device: gone at once, posted one by one.
    /// Approvals are skipped, the hub keeps them anyway. A refused post
    /// drops its ledger entry and refetches, which restores the row.
    public func dismiss(_ rows: [HubInboxItem],
                        post: (String, Double) async throws -> Void,
                        refetch: () async throws -> [HubInboxItem]) async {
        let rows = rows.filter { !$0.isApproval }
        guard !rows.isEmpty else { return }
        generation += 1
        let keys = Set(rows.map(\.key))
        items.removeAll { keys.contains($0.key) }
        for row in rows { dismissals.choose(row.key, stamp: row.dismissStamp) }
        var failed = false
        for row in rows {
            do {
                try await post(row.key, row.dismissStamp)
            } catch {
                dismissals.drop(row.key)
                failed = true
            }
        }
        if failed { await load(refetch) }
    }
}
