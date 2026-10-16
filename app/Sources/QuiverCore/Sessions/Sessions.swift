import Foundation
import Observation

/// One session as `swe session --json` lists it.
public struct SessionInfo: Codable, Sendable, Equatable, Identifiable {
    public struct Resume: Codable, Sendable, Equatable {
        public var argv: [String]
        public var cwd: String?
        public init(argv: [String], cwd: String?) {
            self.argv = argv
            self.cwd = cwd
        }
    }

    public var id: String
    public var harness: String
    public var agent: String
    public var cwd: String?
    public var title: String?
    public var titleSource: String?
    public var lastActiveAt: Date
    /// `active`, `done`, `interrupted`, `followup`, `error`, or nil when unknown.
    public var status: String?
    /// Nil when the harness has no resume command.
    public var resume: Resume?
    /// Where a cloud session opens in the browser; nil for local ones.
    public var webURL: String?

    public init(id: String, harness: String, agent: String, cwd: String?, title: String?, titleSource: String?,
                lastActiveAt: Date, status: String?, resume: Resume?) {
        self.id = id
        self.harness = harness
        self.agent = agent
        self.cwd = cwd
        self.title = title
        self.titleSource = titleSource
        self.lastActiveAt = lastActiveAt
        self.status = status
        self.resume = resume
    }

    /// A session with no resume command that has a web page, which the
    /// row's button opens instead.
    public var opensInBrowser: Bool { resume == nil && webURL != nil }

    /// swe's client decodes with `convertFromSnakeCase`.
    enum CodingKeys: String, CodingKey {
        case id, harness, agent, cwd, title, status, resume, titleSource, lastActiveAt, webURL
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        harness = try c.decode(String.self, forKey: .harness)
        agent = try c.decodeIfPresent(String.self, forKey: .agent) ?? harness
        cwd = try c.decodeIfPresent(String.self, forKey: .cwd)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        titleSource = try c.decodeIfPresent(String.self, forKey: .titleSource)
        lastActiveAt = Date(timeIntervalSince1970: try c.decode(Double.self, forKey: .lastActiveAt))
        status = try c.decodeIfPresent(String.self, forKey: .status)
        resume = try c.decodeIfPresent(Resume.self, forKey: .resume)
        webURL = try c.decodeIfPresent(String.self, forKey: .webURL)
        // A harness can list a session it has no id for; one such row must
        // not sink the whole list, so it gets a stable id of its own.
        id = try c.decodeIfPresent(String.self, forKey: .id)
            ?? "\(harness)|\(cwd ?? "")|\(Int(lastActiveAt.timeIntervalSince1970))"
    }

    /// The same shape it decodes from, so a saved list reads back as is.
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(harness, forKey: .harness)
        try c.encode(agent, forKey: .agent)
        try c.encodeIfPresent(cwd, forKey: .cwd)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encodeIfPresent(titleSource, forKey: .titleSource)
        try c.encode(lastActiveAt.timeIntervalSince1970, forKey: .lastActiveAt)
        try c.encodeIfPresent(status, forKey: .status)
        try c.encodeIfPresent(resume, forKey: .resume)
    }

    public var project: String? { Self.project(of: cwd) }

    /// The folder a session belongs to, with a worktree counted as its repository.
    public static func project(of cwd: String?) -> String? {
        guard let cwd, !cwd.isEmpty else { return nil }
        return SessionProjects.pattern(of: cwd) ?? cwd.split(separator: "/").last.map(String.init)
    }
}

public struct SessionList: Sendable, Equatable {
    public var sessions: [SessionInfo]
    /// "harness: error" for each harness whose sessions couldn't be read.
    public var failures: [String]

    public init(sessions: [SessionInfo], failures: [String]) {
        self.sessions = sessions
        self.failures = failures
    }
}

struct SessionListPayload: Decodable {
    struct Failure: Decodable { var harness: String; var error: String }
    var sessions: [SessionInfo]
    var failures: [Failure]?
}

public protocol SessionListing: Sendable {
    /// `days` nil asks for every session swe can list, `limit` permitting.
    func sessions(days: Int?, limit: Int) async throws -> SessionList
}

extension SweClient: SessionListing {
    /// The latest `limit` sessions from the last `days` calendar days, or
    /// all of them when `days` is nil: swe sorts newest first and the cap
    /// keeps the payload bounded.
    public func sessions(days: Int?, limit: Int) async throws -> SessionList {
        var arguments = ["session", String(limit)]
        if let days { arguments.append("--days=\(days)") }
        arguments.append("--json")
        let payload: SessionListPayload = try await call(arguments)
        return SessionList(sessions: payload.sessions,
                           failures: (payload.failures ?? []).map { "\($0.harness): \($0.error)" })
    }
}

public protocol SessionUsageQuerying: Sendable {
    func bySession(in range: DateInterval, harness: String?) async -> [SessionUsage]
    /// Per day, harness and model, for the model filter's token counts.
    func daily(in range: DateInterval, calendar: Calendar) async -> [DailyUsage]
}

extension Ledger: SessionUsageQuerying {}

/// The shell line that resumes a session in the account it ran under.
public enum SessionResume {
    public static func shellLine(_ resume: SessionInfo.Resume, harness: String, accountHome: String?,
                                 isDefaultHome: Bool) -> String {
        var parts: [String] = []
        if let cwd = resume.cwd, !cwd.isEmpty { parts.append("cd \(LaunchPlanner.shellQuote(cwd)) && ") }
        let variable = ["claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"][harness]
        if let variable {
            if isDefaultHome || accountHome == nil {
                parts.append("unset \(variable) && ")
            } else if let accountHome {
                parts.append("\(variable)=\(LaunchPlanner.shellQuote(accountHome)) ")
            }
        }
        parts.append(resume.argv.map(LaunchPlanner.shellQuote).joined(separator: " "))
        return parts.joined()
    }
}

/// Where a session ran. Cursor's usage feed has no field for it, so its
/// conversation ids decide: `bc-` is a cloud background agent and `sand-`
/// a cloud sandbox subagent. Grok Bot only runs in Cursor's cloud, and
/// `devin-cloud` sessions come from Devin's API. Every other session is
/// one swe read from this Mac.
public enum SessionLocation: String, Sendable, Equatable {
    case local, cloud

    public init(harness: String, id: String) {
        switch harness {
        case "grok-bot", DevinCloud.harness: self = .cloud
        case "cursor" where id.hasPrefix("bc-") || id.hasPrefix("sand-"): self = .cloud
        default: self = .local
        }
    }

    public var title: String {
        switch self {
        case .local: "Local"
        case .cloud: "Cloud"
        }
    }
}

/// The Sessions page: every recent session, its cost from the ledger, and filters.
@MainActor
@Observable
public final class SessionsModel {
    public struct Row: Identifiable, Equatable, Sendable {
        /// The session id, or for a session from an extra account the
        /// account home plus id: one session copied into two accounts is
        /// two rows.
        public var id: String
        public var session: SessionInfo
        public var usage: SessionUsage?
        /// When the session ran, for a source with no ledger usage to say
        /// it (Devin cloud sessions, from their message times).
        public var span: DateInterval?

        public init(session: SessionInfo, usage: SessionUsage?, id: String? = nil, span: DateInterval? = nil) {
            self.id = id ?? session.id
            self.session = session
            self.usage = usage
            self.span = span
        }

        /// From the first to the last recorded event, when the ledger has
        /// any, else the source's own span.
        public var duration: TimeInterval? {
            usage.map { $0.lastTimestamp.timeIntervalSince($0.firstTimestamp) } ?? span?.duration
        }

        public var location: SessionLocation { SessionLocation(harness: session.harness, id: session.id) }
    }

    /// What the filters currently match.
    public struct Summary: Equatable, Sendable {
        public var sessions = 0
        public var totals = UsageTotals()
    }

    public struct DayGroup: Identifiable, Equatable, Sendable {
        public var id: Date { day }
        public var day: Date
        public var rows: [Row]
    }

    public enum Window: String, CaseIterable, Identifiable, Sendable {
        case today, week, month, quarter, year, all
        public var id: String { rawValue }
        public var title: String {
            switch self {
            case .today: "Today"
            case .week: "7 days"
            case .month: "30 days"
            case .quarter: "90 days"
            case .year: "365 days"
            case .all: "All time"
            }
        }
        var seconds: TimeInterval {
            switch self {
            case .today: 86_400
            case .week: 7 * 86_400
            case .month: 30 * 86_400
            case .quarter: 90 * 86_400
            case .year: 365 * 86_400
            // `visible` treats `all` as unbounded, so this never reads.
            case .all: 0
            }
        }
    }

    public struct Filter: Equatable, Sendable {
        public var harness: String?
        public var window: Window = .quarter
        /// A project choice's `key`, lower-cased; "other" holds every
        /// generated (hash-like) name.
        public var project: String?
        public var model: String?
        public var search = ""
        public init() {}
    }

    public var filter = Filter()
    public private(set) var rows: [Row] = []
    public private(set) var failures: [String] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    /// The listing hit `limit`, so older sessions are missing from the page.
    public private(set) var isCapped = false

    private let source: SessionListing
    private let ledger: SessionUsageQuerying
    private let defaultHomes: Set<String>
    private let limit: Int
    private let now: () -> Date
    private let calendar: Calendar
    private var listed: [SessionInfo] = []
    /// Devin cloud sessions, which swe doesn't list and the ledger has no
    /// usage for; `DevinCloudSync` pushes them in.
    private var cloud: [DevinCloudSession] = []
    /// A cloud session has no folder; its first pull request's repository
    /// stands in as the project.
    private var cloudProjects: [String: String] = [:]
    private let snapshots: (any SnapshotStoring<SessionSnapshot>)?
    private let maxAge: TimeInterval
    private let listTimeout: Duration
    /// When the list on show was listed and by which swe; nil until one is.
    private var listedAt: Date?
    private var listedFrom: String?
    /// The listing in flight and the swe it asks. It runs in a task of its
    /// own, so a page that closes doesn't cancel it, and a page that
    /// reopens waits for it instead of starting another.
    private var inflight: (source: String, task: Task<Void, Never>)?
    /// Only the newest list load writes the list.
    private var generation = 0
    /// Only the newest usage read (from a load or a refresh) writes usage.
    private var usageGeneration = 0

    /// - Parameters:
    ///   - defaultHomes: the Claude and Codex homes `swe session` reads. The
    ///     ledger's sessions from any other home (Quiver's extra accounts)
    ///     are added from the ledger, since swe never lists them.
    ///   - limit: the most sessions to ask swe for.
    ///   - snapshots: where the last list is kept between launches.
    ///   - maxAge: how long a list counts as fresh, so opening the page again
    ///     soon after doesn't ask swe again.
    ///   - listTimeout: how long swe may take before the listing is stopped
    ///     and counts as failed, so a hung swe can't block the next try.
    public init(source: SessionListing, ledger: SessionUsageQuerying, defaultHomes: Set<String> = [],
                limit: Int = 100_000, snapshots: (any SnapshotStoring<SessionSnapshot>)? = nil, maxAge: TimeInterval = 60,
                listTimeout: Duration = .seconds(60),
                now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.source = source
        self.ledger = ledger
        self.defaultHomes = defaultHomes
        self.limit = limit
        self.snapshots = snapshots
        self.maxAge = maxAge
        self.listTimeout = listTimeout
        self.now = now
        self.calendar = calendar
    }

    /// Shows the last saved list until swe answers. Does nothing once a list
    /// has loaded, so a slow read never replaces a newer one.
    public func restore() async {
        guard listedAt == nil, let snapshot = await snapshots?.load(), listedAt == nil else { return }
        let usage = Self.keyed(snapshot.usage)
        listed = snapshot.sessions
        latestUsage = usage
        rows = join(listed, usage)
        resolveProjects(for: rows)
        failures = snapshot.failures
        isCapped = snapshot.isCapped
        listedAt = snapshot.savedAt
        listedFrom = snapshot.source
    }

    /// Lists again unless this swe's list is under `maxAge` old. Joins a
    /// listing already in flight from the same swe rather than starting
    /// another. Refresh calls `load` instead, which always lists.
    public func loadIfStale(for sweSource: String) async {
        if let inflight, inflight.source == sweSource { return await inflight.task.value }
        if error == nil, listedFrom == sweSource, let listedAt, now().timeIntervalSince(listedAt) < maxAge { return }
        await load(for: sweSource)
    }

    /// Lists the sessions from swe and joins their usage.
    /// - Parameter sweSource: the swe asked, as set in Settings; nil keeps the last one.
    public func load(for sweSource: String? = nil) async {
        generation += 1
        let mine = generation
        let key = sweSource ?? listedFrom ?? ""
        isLoading = true
        let task = Task { await self.list(from: key, generation: mine) }
        inflight = (key, task)
        await task.value
    }

    private func list(from key: String, generation mine: Int) async {
        defer {
            if mine == generation {
                isLoading = false
                inflight = nil
            }
        }
        let list: SessionList
        do {
            list = try await Self.listing(from: source, limit: limit, within: listTimeout)
        } catch is CancellationError {
            return
        } catch {
            if mine == generation { self.error = error.localizedDescription }
            return
        }
        usageGeneration += 1
        let myUsage = usageGeneration
        let (usage, daily) = await readUsage()
        guard mine == generation else { return }
        // A refresh that finished meanwhile read the ledger later; keep
        // its numbers, the model filter's daily rows included.
        let staleRead = myUsage != usageGeneration
        let joinedUsage = staleRead ? (latestUsage ?? usage) : usage
        listed = list.sessions
        isCapped = list.sessions.count >= limit
        latestUsage = joinedUsage
        if !staleRead { dailyRows = daily }
        rows = join(listed, joinedUsage)
        resolveProjects(for: rows)
        failures = list.failures
        error = nil
        listedAt = now()
        listedFrom = key
        isLoading = false
        await save()
    }

    /// Re-reads tokens and spend from the ledger after a scan, keeping the list.
    /// `ledgerScanned` is the overview's "a scan has completed": missing
    /// usage only counts as empty once it is true.
    public func refreshUsage(ledgerScanned: Bool = false) async {
        self.ledgerScanned = ledgerScanned
        usageGeneration += 1
        let mine = usageGeneration
        let (usage, daily) = await readUsage()
        guard mine == usageGeneration else { return }
        latestUsage = usage
        dailyRows = daily
        rows = join(listed, usage)
        resolveProjects(for: rows)
        await save()
    }

    /// The Devin cloud sessions changed: rejoin with what is listed.
    public func updateCloud(_ sessions: [DevinCloudSession]) {
        guard sessions != cloud else { return }
        cloud = sessions
        cloudProjects = Dictionary(sessions.compactMap { s in s.project.map { (s.id, $0) } },
                                   uniquingKeysWith: { a, _ in a })
        rows = join(listed, latestUsage ?? [:])
    }

    struct ListingTimedOut: LocalizedError {
        var timeout: Duration
        var errorDescription: String? {
            "swe didn't list the sessions within \(timeout.components.seconds) seconds. Refresh to try again."
        }
    }

    /// swe's list, or `ListingTimedOut` once `timeout` passes. Losing the race
    /// cancels the listing, which stops the swe process. The listing is
    /// unbounded: swe returns the newest `limit` sessions over all history,
    /// so the All time period needs no second fetch.
    nonisolated static func listing(from source: SessionListing, limit: Int,
                                    within timeout: Duration) async throws -> SessionList {
        try await withDeadline(timeout, onTimeout: { ListingTimedOut(timeout: timeout) }) {
            try await source.sessions(days: nil, limit: limit)
        }
    }

    /// Writes what is on show for the next launch, once something was listed.
    private func save() async {
        guard let snapshots, let listedAt, let listedFrom else { return }
        await snapshots.save(SessionSnapshot(
            source: listedFrom, savedAt: listedAt, sessions: listed, failures: failures, isCapped: isCapped,
            usage: Array((latestUsage ?? [:]).values)))
    }

    private var latestUsage: [UsageKey: SessionUsage]?

    /// Usage per harness, account home and session.
    struct UsageKey: Hashable {
        var harness: String
        var account: String
        var session: String
    }

    /// Every session's usage over its whole history, plus the daily rows
    /// the model filter's token counts come from: a session resumed today
    /// may have started months ago.
    private func readUsage() async -> ([UsageKey: SessionUsage], [DailyUsage]) {
        let range = DateInterval(start: Date(timeIntervalSince1970: 0), end: now())
        async let usage = ledger.bySession(in: range, harness: nil)
        async let daily = ledger.daily(in: range, calendar: calendar)
        return (Self.keyed(await usage), await daily)
    }

    /// One entry per harness, account and session, merging repeats.
    static func keyed(_ usage: [SessionUsage]) -> [UsageKey: SessionUsage] {
        var byKey: [UsageKey: SessionUsage] = [:]
        for row in usage {
            let key = UsageKey(harness: row.harness, account: row.accountHome, session: row.sessionId)
            byKey[key] = byKey[key].map { merged($0, row) } ?? row
        }
        return byKey
    }

    /// One feed conversation's key. A conversation id is the same session
    /// in every profile home; a day bucket (events Cursor gave no id) is
    /// only one account's day, so it keeps its home.
    public nonisolated static func feedKey(harness: String, account: String, session: String) -> String {
        CursorUsageFeed.isDayBucket(session) ? "\(harness)|\(account)|\(session)" : "\(harness)|\(session)"
    }

    /// Two usage rows of one session added together. The account is the
    /// one that recorded the later event.
    static func merged(_ a: SessionUsage, _ b: SessionUsage) -> SessionUsage {
        var result = a.lastTimestamp >= b.lastTimestamp ? a : b
        let other = a.lastTimestamp >= b.lastTimestamp ? b : a
        result.totals.add(other.totals)
        result.models = Array(Set(result.models + other.models)).sorted()
        result.firstTimestamp = min(result.firstTimestamp, other.firstTimestamp)
        result.cwd = result.cwd ?? other.cwd
        return result
    }

    /// Harnesses Quiver keeps extra accounts for; swe reads only their default home.
    private static let accountHarnesses: Set<String> = ["claude", "codex"]

    private func join(_ sessions: [SessionInfo], _ usage: [UsageKey: SessionUsage]) -> [Row] {
        var byHarnessAndId: [String: [SessionUsage]] = [:]
        for (key, use) in usage { byHarnessAndId["\(key.harness)|\(key.session)", default: []].append(use) }
        // A feed conversation is one session whichever profile polled it:
        // profiles signed in to one account take turns, and each stores
        // the events it read under its own home.
        var feed: [String: SessionUsage] = [:]
        for (key, use) in usage where SessionFilter.feedHarnesses.contains(key.harness) {
            let id = Self.feedKey(harness: key.harness, account: key.account, session: key.session)
            feed[id] = feed[id].map { Self.merged($0, use) } ?? use
        }

        // swe's rows: Claude and Codex from their default home only; other
        // harnesses have one store each, whatever its folder.
        var rows = sessions.map { session -> Row in
            let id = "\(session.harness)|\(session.id)"
            if let use = feed[id] { return Row(session: session, usage: use) }
            let candidates = byHarnessAndId[id] ?? []
            let use = Self.accountHarnesses.contains(session.harness)
                ? candidates.first { defaultHomes.contains($0.accountHome) }
                : candidates.max { $0.lastTimestamp < $1.lastTimestamp }
            return Row(session: session, usage: use)
        }
        // Sessions in extra accounts, which swe never lists. The listing
        // is unbounded, so these join at any age too and the window
        // filter bounds what shows.
        for (key, use) in usage where Self.accountHarnesses.contains(key.harness)
            && !defaultHomes.contains(key.account) {
            let argv = Self.resumeArgv(harness: key.harness, id: key.session)
            let session = SessionInfo(
                id: key.session, harness: key.harness, agent: UsageFormat.harness(key.harness), cwd: use.cwd,
                title: nil, titleSource: nil, lastActiveAt: use.lastTimestamp, status: nil,
                resume: argv.map { .init(argv: $0, cwd: use.cwd) })
            rows.append(Row(session: session, usage: use, id: "\(key.account)|\(key.session)"))
        }
        // Feed sessions swe has no transcript for: cloud runs, Grok Bot and
        // editor chats. Usage is all there is, so they have no title,
        // folder or resume command.
        let listedIds = Set(sessions.map { "\($0.harness)|\($0.id)" })
        for (id, use) in feed where !listedIds.contains(id) {
            let session = SessionInfo(
                id: use.sessionId, harness: use.harness, agent: UsageFormat.harness(use.harness), cwd: use.cwd,
                title: nil, titleSource: nil, lastActiveAt: use.lastTimestamp, status: nil, resume: nil)
            rows.append(Row(session: session, usage: use, id: id))
        }
        // Devin cloud sessions: API-listed, no usage, opened in the browser.
        for session in cloud {
            rows.append(Row(session: session.sessionInfo, usage: nil,
                            id: "\(DevinCloud.harness)|\(session.id)", span: session.span))
        }
        return rows.sorted { ($0.session.lastActiveAt, $1.id) > ($1.session.lastActiveAt, $0.id) }
    }

    static func codexThreadId(_ id: String) -> String? {
        guard let range = id.range(of: #"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"#,
                                   options: .regularExpression) else { return nil }
        return id[range].lowercased()
    }

    /// How each CLI resumes a session by id.
    static func resumeArgv(harness: String, id: String) -> [String]? {
        switch harness {
        case "claude": ["claude", "--resume", id]
        // The ledger's Codex id is the rollout file's stem; `codex resume`
        // takes the thread uuid at its end (as swe's `_codex_resume_args`).
        case "codex": codexThreadId(id).map { ["codex", "resume", $0] }
        default: nil
        }
    }

    public var summary: Summary {
        var summary = Summary()
        for row in visible {
            summary.sessions += 1
            if let usage = row.usage { summary.totals.add(usage.totals) }
        }
        return summary
    }

    /// The visible sessions by the local day they were last active, newest first.
    public func groupedByDay(calendar: Calendar = .current) -> [DayGroup] {
        var groups: [DayGroup] = []
        for row in visible {
            let day = calendar.startOfDay(for: row.session.lastActiveAt)
            if groups.last?.day == day {
                groups[groups.count - 1].rows.append(row)
            } else {
                groups.append(DayGroup(day: day, rows: [row]))
            }
        }
        return groups
    }

    /// The period's start: today from local midnight, the other windows
    /// that many days back, and unbounded for All time.
    private func windowStart(_ window: Window) -> Date {
        switch window {
        case .today: calendar.startOfDay(for: now())
        case .all: .distantPast
        default: now().addingTimeInterval(-window.seconds)
        }
    }

    // MARK: - Filter choices

    /// One project in the filter: its display name and how many listed
    /// sessions it holds.
    public struct ProjectChoice: Equatable, Sendable, Identifiable {
        public var id: String { key }
        /// Lower-cased project name, or "other" for the generated names.
        public var key: String
        public var name: String
        public var count: Int
        public init(key: String, name: String, count: Int) {
            self.key = key
            self.name = name
            self.count = count
        }
    }

    /// One model in the filter, with its tokens in the selected period.
    public struct ModelChoice: Equatable, Sendable, Identifiable {
        public var id: String { model }
        public var model: String
        public var tokens: Int
        public init(model: String, tokens: Int) {
            self.model = model
            self.tokens = tokens
        }
    }

    /// The key hash-like folder names fold into; "Other" sits last.
    public static let otherProjectKey = "other"
    public static let otherProjectName = "Other"

    /// The filter key for a project name: generated names share one.
    public static func projectKey(_ name: String?) -> String? {
        guard let name, !name.isEmpty else { return nil }
        return SessionProjects.isHashLike(name) ? otherProjectKey : name.lowercased()
    }

    public var harnesses: [String] { Array(Set(rows.map(\.session.harness))).sorted() }

    /// One filter entry per project: case-folded duplicates merged,
    /// hash-like names folded into a single "Other" entry at the end,
    /// each with its session count, most sessions first.
    public var projects: [ProjectChoice] {
        var names: [String: String] = [:]
        var counts: [String: Int] = [:]
        var order: [String] = []
        for row in rows {
            guard let project = project(for: row.session),
                  let key = Self.projectKey(project) else { continue }
            if names[key] == nil {
                names[key] = key == Self.otherProjectKey ? Self.otherProjectName : project
                order.append(key)
            }
            counts[key, default: 0] += 1
        }
        let named = order
            .filter { $0 != Self.otherProjectKey }
            .sorted { counts[$0]! != counts[$1]!
                ? counts[$0]! > counts[$1]!
                : names[$0]!.localizedCaseInsensitiveCompare(names[$1]!) == .orderedAscending }
        var choices = named.map { ProjectChoice(key: $0, name: names[$0]!, count: counts[$0]!) }
        if let count = counts[Self.otherProjectKey] {
            choices.append(ProjectChoice(key: Self.otherProjectKey, name: Self.otherProjectName, count: count))
        }
        return choices
    }

    /// One filter entry per model the listed sessions used, heaviest in
    /// the selected period first.
    public var models: [ModelChoice] {
        // Daily rows are labelled at midnight, so the cutoff lands on a
        // day boundary like the window's own day count intends.
        let since = filter.window == .all ? .distantPast
            : calendar.startOfDay(for: windowStart(filter.window))
        var tokens: [String: Int] = [:]
        for row in dailyRows where row.day >= since {
            tokens[row.model, default: 0] += row.totals.totalTokens
        }
        let listed = Set(rows.flatMap { $0.usage?.models ?? [] })
        return listed
            .map { ModelChoice(model: $0, tokens: tokens[$0] ?? 0) }
            .sorted { $0.tokens != $1.tokens ? $0.tokens > $1.tokens : $0.model < $1.model }
    }

    /// The project `session` belongs to: the main repository once the
    /// working folders resolve (off the main thread, cached per cwd), so
    /// every worktree counts under it. Until then, the path-only guess.
    public func project(for session: SessionInfo) -> String? {
        if session.harness == DevinCloud.harness { return cloudProjects[session.id] }
        guard let cwd = session.cwd, !cwd.isEmpty else { return nil }
        return resolvedProjects[cwd] ?? SessionInfo.project(of: cwd)
    }

    /// Per day, harness and model over all history, for `models`' counts.
    private var dailyRows: [DailyUsage] = []
    /// cwd -> repository name, once resolved against the filesystem.
    private var resolvedProjects: [String: String] = [:]
    /// Only the newest resolution writes `resolvedProjects`.
    private var projectGeneration = 0

    /// Resolves each row's working folder to its main repository off the
    /// main thread. A stale result (an older rows list finished later) is
    /// dropped.
    private func resolveProjects(for rows: [Row]) {
        projectGeneration += 1
        let mine = projectGeneration
        let cwds = Set(rows.compactMap(\.session.cwd))
        guard !cwds.isEmpty else { resolvedProjects = [:]; return }
        Task.detached(priority: .utility) { [weak self] in
            var resolved: [String: String] = [:]
            for cwd in cwds {
                if let project = SessionProjects.project(of: cwd) { resolved[cwd] = project }
            }
            await self?.commitProjects(resolved, generation: mine)
        }
    }

    /// A resolution commits only while it is still the newest one.
    private func commitProjects(_ resolved: [String: String], generation: Int) {
        guard projectGeneration == generation else { return }
        resolvedProjects = resolved
    }

    // MARK: - Trivial sessions

    /// Settings' "Hide accidental sessions". Off disables the filter here
    /// and on the calendar alike.
    public var hideTrivial = true
    /// The page's "Show" for hidden rows; a view state, not persisted.
    public var showTrivial = false

    /// Set once a ledger scan has completed. Until then a listed
    /// session's missing usage proves nothing: the ledger may simply not
    /// have read the logs yet.
    public var ledgerScanned = false

    /// Whether the row is an accidental session: empty (zero responses —
    /// a listed session of a local-log harness with no ledger events at
    /// all counts, once the ledger has actually scanned) or probe-like.
    /// Feed-polled harnesses (Cursor, Grok Bot) and harnesses the ledger
    /// does not read are never hidden for missing usage.
    func isTrivial(_ row: Row) -> Bool {
        guard SessionFilter.usageLogHarnesses.contains(row.session.harness) else { return false }
        guard let usage = row.usage else {
            return ledgerScanned && SessionFilter.localLogHarnesses.contains(row.session.harness)
        }
        return SessionFilter.isTrivial(usage: usage)
    }

    /// Rows the current filters match that the trivial rule would hide.
    /// Counts them even while `showTrivial` keeps them visible.
    public var hiddenTrivialCount: Int {
        guard hideTrivial else { return 0 }
        return rows.count { matches($0) && isTrivial($0) }
    }

    /// The non-trivial filter terms: harness, window, project, model and
    /// search.
    private func matches(_ row: Row) -> Bool {
        let s = row.session
        if let harness = filter.harness, s.harness != harness { return false }
        if s.lastActiveAt < windowStart(filter.window) { return false }
        if let key = filter.project, Self.projectKey(project(for: s)) != key { return false }
        if let model = filter.model, !(row.usage?.models.contains(model) ?? false) { return false }
        let needle = filter.search.trimmingCharacters(in: .whitespaces).lowercased()
        if !needle.isEmpty {
            let haystack = ([s.title, s.cwd, project(for: s), s.id, s.agent, row.location.title]
                + (row.usage?.models ?? []).map(Optional.some))
                .compactMap { $0 }.joined(separator: " ").lowercased()
            if !haystack.contains(needle) { return false }
        }
        return true
    }

    public var visible: [Row] {
        rows.filter { row in
            guard matches(row) else { return false }
            if hideTrivial, !showTrivial, isTrivial(row) { return false }
            return true
        }
    }
}
