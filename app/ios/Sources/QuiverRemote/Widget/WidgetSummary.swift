import Foundation
import WidgetKit

/// `quiver://session/<key>?hub=<hub id>`: opens a session's chat, on the
/// hub named when this phone is paired with it. The widget's rows link
/// here; `AppModel.handle` routes it onto the Sessions stack.
struct SessionLink: Equatable {
    let key: String
    /// The hub the session lives on (`PairedHub.id`); nil leaves the hub
    /// on screen.
    let hubId: String?

    init(key: String, hubId: String?) {
        self.key = key
        self.hubId = hubId
    }

    /// Keys hold `:`, `@` and sometimes `/`, so the path is one
    /// percent-encoded segment.
    init?(_ url: URL) {
        guard url.scheme == "quiver", url.host == "session",
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        let raw = parts.percentEncodedPath.hasPrefix("/") ? String(parts.percentEncodedPath.dropFirst()) : parts.percentEncodedPath
        guard let key = raw.removingPercentEncoding, !key.isEmpty else { return nil }
        let hub = parts.queryItems?.first { $0.name == "hub" }?.value
        self.init(key: key, hubId: hub?.isEmpty == false ? hub : nil)
    }

    var url: URL? {
        var parts = URLComponents()
        parts.scheme = "quiver"
        parts.host = "session"
        parts.percentEncodedPath = "/" + HubClient.encodeKey(key)
        if let hubId { parts.queryItems = [URLQueryItem(name: "hub", value: hubId)] }
        return parts.url
    }
}

/// What the home screen widget shows for one hub, cached in the App
/// Group so the widget can draw it without the hub. The app writes it
/// after each refresh; the widget writes it after its own fetch.
struct WidgetSnapshot: Codable, Equatable {
    struct Row: Codable, Equatable, Identifiable {
        let key: String
        let title: String
        let harness: String
        /// The hub's raw status ("approval", "followup", "active").
        let status: String?
        var id: String { key }
        var needsApproval: Bool { status == "approval" }
    }

    /// `PairedHub.id`: a snapshot of another hub is never shown.
    var hubId: String
    var hubName: String
    /// The first few, newest first; the counts cover them all.
    var needsYou: [Row]
    var needsYouCount: Int
    var working: [Row]
    var workingCount: Int
    /// The day's cost, and when the hub reported it. The app's refresh
    /// carries it over, so it ages apart from `fetchedAt`.
    var spendUsd: Double?
    var spendAt: Date?
    var fetchedAt: Date

    /// Rows kept per list: the medium widget shows three.
    static let rowLimit = 4

    /// The spend, if it's for `now`'s day: yesterday's total is not today's.
    func spendToday(now: Date, timeZone: TimeZone = .current) -> Double? {
        guard let spendAt, WidgetSummary.day(spendAt, timeZone: timeZone) == WidgetSummary.day(now, timeZone: timeZone)
        else { return nil }
        return spendUsd
    }

    /// Equal apart from when it was fetched: what decides whether the
    /// widget needs reloading.
    func sameContent(as other: WidgetSnapshot) -> Bool {
        var a = self
        a.fetchedAt = other.fetchedAt
        return a == other
    }

    /// The sessions the medium widget lists: waiting on you first.
    var rows: [Row] { needsYou + working }
}

enum WidgetSummary {
    /// Sessions waiting on you are the Inbox's "Waiting on you" rows (the
    /// tab badge's set) plus any listed session the hub marks approval or
    /// follow-up; working are the active sessions not among them.
    static func make(hubId: String, hubName: String, sessions: [SessionRow], inbox: [InboxItem],
                     spendUsd: Double?, spendAt: Date?, at: Date) -> WidgetSnapshot {
        var waiting: [String: WidgetSnapshot.Row] = [:]
        var stamps: [String: Double] = [:]
        for item in inbox where InboxGrouping.section(of: item) == .waiting {
            waiting[item.key] = .init(key: item.key, title: item.displayTitle, harness: item.harnessName, status: item.status)
            stamps[item.key] = item.lastActiveAt ?? 0
        }
        for row in sessions where row.sessionStatus.needsYou && waiting[row.key] == nil {
            waiting[row.key] = .init(key: row.key, title: row.displayTitle, harness: row.harnessName, status: row.status)
            stamps[row.key] = row.lastActiveAt ?? 0
        }
        let needsYou = waiting.values.sorted { (stamps[$0.key] ?? 0, $1.key) > (stamps[$1.key] ?? 0, $0.key) }
        let active = sessions
            .filter { $0.sessionStatus == .active && waiting[$0.key] == nil }
            .sorted { ($0.lastActiveAt ?? 0, $1.key) > ($1.lastActiveAt ?? 0, $0.key) }
        return WidgetSnapshot(
            hubId: hubId, hubName: hubName,
            needsYou: Array(needsYou.prefix(WidgetSnapshot.rowLimit)), needsYouCount: needsYou.count,
            working: active.prefix(WidgetSnapshot.rowLimit).map {
                .init(key: $0.key, title: $0.displayTitle, harness: $0.harnessName, status: $0.status)
            },
            workingCount: active.count,
            spendUsd: spendUsd, spendAt: spendAt, fetchedAt: at)
    }

    /// The phone's calendar day, as the overview's `tz` cuts it.
    static func day(_ date: Date, timeZone: TimeZone = .current) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let c = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", c.year ?? 0, c.month ?? 0, c.day ?? 0)
    }
}

/// The snapshot as a JSON file in the App Group container.
struct WidgetSnapshotStore {
    /// Nil when the build has no App Group: nothing is kept.
    var directory: URL? = FileManager.default
        .containerURL(forSecurityApplicationGroupIdentifier: SharedContainer.appGroup)

    private var file: URL? { directory?.appendingPathComponent("widget-snapshot.json") }

    func read() -> WidgetSnapshot? {
        guard let file, let data = try? Data(contentsOf: file) else { return nil }
        return try? JSONDecoder().decode(WidgetSnapshot.self, from: data)
    }

    /// Writes `snapshot`; true when what the widget shows changed (a new
    /// hub, or different rows, counts or spend).
    @discardableResult
    func write(_ snapshot: WidgetSnapshot) -> Bool {
        guard let file, let data = try? JSONEncoder().encode(snapshot) else { return false }
        let old = read()
        guard (try? data.write(to: file, options: .atomic)) != nil else { return false }
        return old.map { !$0.sameContent(as: snapshot) } ?? true
    }

    /// Drops the snapshot if it's `hubId`'s (every snapshot when nil);
    /// true when one was dropped.
    @discardableResult
    func clear(hubId: String? = nil) -> Bool {
        guard let file, let old = read(), hubId == nil || old.hubId == hubId else { return false }
        return (try? FileManager.default.removeItem(at: file)) != nil
    }
}

/// How the app keeps the widget's snapshot current: it rewrites it after
/// each refresh and asks WidgetKit to redraw only when the content
/// changed, since the app refreshes every 15 s and reloads are budgeted.
struct WidgetPublisher {
    var store: WidgetSnapshotStore
    var reload: () -> Void

    static var live: WidgetPublisher {
        WidgetPublisher(store: WidgetSnapshotStore()) { WidgetCenter.shared.reloadAllTimelines() }
    }

    /// The hub's sessions and inbox as fetched; the spend carries over
    /// from the last snapshot of the same hub, since the app's refresh
    /// doesn't fetch it.
    func publish(hubId: String, hubName: String, sessions: [SessionRow], inbox: [InboxItem], at: Date = Date()) {
        let old = store.read().flatMap { $0.hubId == hubId ? $0 : nil }
        let snapshot = WidgetSummary.make(hubId: hubId, hubName: hubName, sessions: sessions, inbox: inbox,
                                          spendUsd: old?.spendUsd, spendAt: old?.spendAt, at: at)
        if store.write(snapshot) { reload() }
    }

    /// Today's spend from the Usage tab, laid onto the hub's snapshot.
    func publishSpend(hubId: String, usd: Double?, at: Date = Date()) {
        guard var snapshot = store.read(), snapshot.hubId == hubId,
              (snapshot.spendAt ?? .distantPast) <= at else { return }
        snapshot.spendUsd = usd
        snapshot.spendAt = at
        if store.write(snapshot) { reload() }
    }

    /// The hub on screen changed or went: the widget follows.
    func hubChanged(dropping hubId: String? = nil) {
        if let hubId { store.clear(hubId: hubId) }
        reload()
    }
}

/// What the widget draws.
enum WidgetContent: Equatable {
    /// No hub paired on this phone, or the hub signed this phone out.
    case notPaired
    /// The hub didn't answer and there's no snapshot of it.
    case unreachable(hubName: String)
    /// `stale` when the hub didn't answer and this is the last snapshot:
    /// the widget says "as of" its time.
    case summary(WidgetSnapshot, stale: Bool)
}

/// The widget's fetch. Every route it calls is one the app already uses:
/// sessions, the inbox and today's overview.
struct WidgetLoader {
    struct Fetched {
        let sessions: SessionsResponse
        let inbox: InboxResponse
        /// Nil when the overview failed: the spend carries over instead.
        let spendUsd: Double?
    }

    var store: WidgetSnapshotStore
    var fetch: (PairedHub) async throws -> Fetched

    /// A snapshot this fresh is drawn as it is: the app just wrote it
    /// and asked for the redraw...
    static let reuseFor: TimeInterval = 120
    /// ...as long as its spend is from this widget interval too.
    static let spendFreshFor: TimeInterval = 15 * 60

    /// Draws from the snapshot when the app wrote it moments ago,
    /// otherwise asks the hub and keeps what it answered; falls back to
    /// the last snapshot when the hub can't be reached.
    func load(hubs: HubList, now: Date = Date()) async -> WidgetContent {
        guard let hub = hubs.active else { return .notPaired }
        let cached = store.read().flatMap { $0.hubId == hub.id ? $0 : nil }
        if let cached, Self.canReuse(cached, now: now) { return .summary(cached, stale: false) }
        do {
            let got = try await fetch(hub)
            let name = got.sessions.machines?.first?.displayName ?? hubs.name(of: hub)
            let fetched = WidgetSummary.make(hubId: hub.id, hubName: name, sessions: got.sessions.sessions,
                                             inbox: got.inbox.items, spendUsd: got.spendUsd,
                                             spendAt: got.spendUsd == nil ? nil : now, at: now)
            let snapshot = Self.merge(fetched, onto: store.read())
            store.write(snapshot)
            return .summary(snapshot, stale: false)
        } catch let error as HubError where error.isUnauthorized {
            store.clear(hubId: hub.id)
            return .notPaired
        } catch {
            if let cached { return .summary(cached, stale: true) }
            return .unreachable(hubName: hubs.name(of: hub))
        }
    }

    static func canReuse(_ snapshot: WidgetSnapshot, now: Date) -> Bool {
        let age = now.timeIntervalSince(snapshot.fetchedAt)
        let spendAge = snapshot.spendAt.map { now.timeIntervalSince($0) } ?? .infinity
        return age >= 0 && age < reuseFor && spendAge < spendFreshFor && snapshot.spendToday(now: now) != nil
    }

    /// What the fetch answered, laid onto the snapshot as it is now, which
    /// the app may have rewritten while the fetch was out: the newer rows
    /// and the newer spend win, each on its own.
    static func merge(_ fetched: WidgetSnapshot, onto latest: WidgetSnapshot?) -> WidgetSnapshot {
        guard let latest, latest.hubId == fetched.hubId else { return fetched }
        var out = latest.fetchedAt > fetched.fetchedAt ? latest : fetched
        let ours = fetched.spendAt ?? .distantPast, theirs = latest.spendAt ?? .distantPast
        if ours >= theirs, fetched.spendAt != nil {
            out.spendUsd = fetched.spendUsd
            out.spendAt = fetched.spendAt
        } else {
            out.spendUsd = latest.spendUsd
            out.spendAt = latest.spendAt
        }
        return out
    }

    /// The live fetch: sessions and inbox must answer; the spend is best
    /// effort.
    static func hubFetch(_ hub: PairedHub) async throws -> Fetched {
        let client = HubClient(baseURL: hub.baseURL, credential: hub.credential.credential)
        async let s = client.sessions()
        async let i = client.inbox()
        async let o = try? client.overview(period: .today)
        let (sessions, inbox) = try await (s, i)
        return Fetched(sessions: sessions, inbox: inbox, spendUsd: await o?.totals?.costUsd)
    }
}
