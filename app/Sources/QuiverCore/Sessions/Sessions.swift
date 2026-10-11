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

    /// swe's client decodes with `convertFromSnakeCase`.
    enum CodingKeys: String, CodingKey {
        case id, harness, agent, cwd, title, status, resume, titleSource, lastActiveAt
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
        var parts = cwd.split(separator: "/").map(String.init)
        for marker in [".claude", ".worktrees", ".codex"] {
            if let index = parts.firstIndex(of: marker), index > 0,
               marker == ".worktrees" || parts.indices.contains(index + 1) && parts[index + 1] == "worktrees" {
                parts = Array(parts[..<index])
                break
            }
        }
        return parts.last
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
    func sessions(days: Int, limit: Int) async throws -> SessionList
}

extension SweClient: SessionListing {
    /// The latest `limit` sessions from the last `days` calendar days.
    public func sessions(days: Int, limit: Int) async throws -> SessionList {
        let payload: SessionListPayload = try await call(["session", String(limit), "--days=\(days)", "--json"])
        return SessionList(sessions: payload.sessions,
                           failures: (payload.failures ?? []).map { "\($0.harness): \($0.error)" })
    }
}

public protocol SessionUsageQuerying: Sendable {
    func bySession(in range: DateInterval) async -> [SessionUsage]
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

        public init(session: SessionInfo, usage: SessionUsage?, id: String? = nil) {
            self.id = id ?? session.id
            self.session = session
            self.usage = usage
        }

        /// From the first to the last recorded event, when the ledger has any.
        public var duration: TimeInterval? {
            usage.map { $0.lastTimestamp.timeIntervalSince($0.firstTimestamp) }
        }
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
        case today, week, month, quarter
        public var id: String { rawValue }
        public var title: String {
            switch self {
            case .today: "Today"
            case .week: "7 days"
            case .month: "30 days"
            case .quarter: "90 days"
            }
        }
        var seconds: TimeInterval {
            switch self {
            case .today: 86_400
            case .week: 7 * 86_400
            case .month: 30 * 86_400
            case .quarter: 90 * 86_400
            }
        }
    }

    public struct Filter: Equatable, Sendable {
        public var harness: String?
        public var window: Window = .quarter
        public var project: String?
        public var model: String?
        public var search = ""
        public init() {}
    }

    public static let days = 90

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
    private let snapshots: SessionSnapshotStoring?
    private let maxAge: TimeInterval
    /// When the list on show was listed and by which swe; nil until one is.
    private var listedAt: Date?
    private var listedFrom: String?
    /// The swe the load in flight is asking.
    private var loadingFrom: String?
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
    public init(source: SessionListing, ledger: SessionUsageQuerying, defaultHomes: Set<String> = [],
                limit: Int = 100_000, snapshots: SessionSnapshotStoring? = nil, maxAge: TimeInterval = 60,
                now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.source = source
        self.ledger = ledger
        self.defaultHomes = defaultHomes
        self.limit = limit
        self.snapshots = snapshots
        self.maxAge = maxAge
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
        failures = snapshot.failures
        isCapped = snapshot.isCapped
        listedAt = snapshot.savedAt
        listedFrom = snapshot.source
    }

    /// Lists again unless this swe's list is under `maxAge` old or is
    /// already being listed. Refresh calls `load` instead, which always lists.
    public func loadIfStale(for sweSource: String) async {
        if isLoading, loadingFrom == sweSource { return }
        if error == nil, listedFrom == sweSource, let listedAt, now().timeIntervalSince(listedAt) < maxAge { return }
        await load(for: sweSource)
    }

    /// Lists the sessions from swe and joins their usage.
    /// - Parameter sweSource: the swe asked, as set in Settings; nil keeps the last one.
    public func load(for sweSource: String? = nil) async {
        generation += 1
        let mine = generation
        let key = sweSource ?? listedFrom ?? ""
        loadingFrom = key
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        let list: SessionList
        do {
            list = try await source.sessions(days: Self.days, limit: limit)
        } catch is CancellationError {
            return
        } catch {
            if mine == generation { self.error = error.localizedDescription }
            return
        }
        usageGeneration += 1
        let myUsage = usageGeneration
        let usage = await usageBySession()
        guard mine == generation else { return }
        // A refresh that finished meanwhile read the ledger later; keep its numbers.
        let joinedUsage = myUsage == usageGeneration ? usage : (latestUsage ?? usage)
        listed = list.sessions
        isCapped = list.sessions.count >= limit
        latestUsage = joinedUsage
        rows = join(listed, joinedUsage)
        failures = list.failures
        error = nil
        listedAt = now()
        listedFrom = key
        isLoading = false
        await save()
    }

    /// Re-reads tokens and spend from the ledger after a scan, keeping the list.
    public func refreshUsage() async {
        usageGeneration += 1
        let mine = usageGeneration
        let usage = await usageBySession()
        guard mine == usageGeneration else { return }
        latestUsage = usage
        rows = join(listed, usage)
        await save()
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

    /// Every session's usage over its whole history: a session resumed
    /// today may have started months ago.
    private func usageBySession() async -> [UsageKey: SessionUsage] {
        Self.keyed(await ledger.bySession(in: DateInterval(start: Date(timeIntervalSince1970: 0), end: now())))
    }

    /// One entry per harness, account and session, merging repeats.
    static func keyed(_ usage: [SessionUsage]) -> [UsageKey: SessionUsage] {
        var byKey: [UsageKey: SessionUsage] = [:]
        for row in usage {
            let key = UsageKey(harness: row.harness, account: row.accountHome, session: row.sessionId)
            if var existing = byKey[key] {
                existing.totals.add(row.totals)
                existing.models = Array(Set(existing.models + row.models)).sorted()
                existing.firstTimestamp = min(existing.firstTimestamp, row.firstTimestamp)
                existing.lastTimestamp = max(existing.lastTimestamp, row.lastTimestamp)
                existing.cwd = existing.cwd ?? row.cwd
                byKey[key] = existing
            } else {
                byKey[key] = row
            }
        }
        return byKey
    }

    /// Harnesses Quiver keeps extra accounts for; swe reads only their default home.
    private static let accountHarnesses: Set<String> = ["claude", "codex"]

    private func join(_ sessions: [SessionInfo], _ usage: [UsageKey: SessionUsage]) -> [Row] {
        var byHarnessAndId: [String: [SessionUsage]] = [:]
        for (key, use) in usage { byHarnessAndId["\(key.harness)|\(key.session)", default: []].append(use) }

        // swe's rows: Claude and Codex from their default home only; other
        // harnesses have one store each, whatever its folder.
        var rows = sessions.map { session -> Row in
            let candidates = byHarnessAndId["\(session.harness)|\(session.id)"] ?? []
            let use = Self.accountHarnesses.contains(session.harness)
                ? candidates.first { defaultHomes.contains($0.accountHome) }
                : candidates.max { $0.lastTimestamp < $1.lastTimestamp }
            return Row(session: session, usage: use)
        }
        // Sessions in extra accounts, which swe never lists.
        let since = now().addingTimeInterval(-Double(Self.days) * 86_400)
        for (key, use) in usage where Self.accountHarnesses.contains(key.harness)
            && !defaultHomes.contains(key.account) && use.lastTimestamp >= since {
            let argv = Self.resumeArgv(harness: key.harness, id: key.session)
            let session = SessionInfo(
                id: key.session, harness: key.harness, agent: UsageFormat.harness(key.harness), cwd: use.cwd,
                title: nil, titleSource: nil, lastActiveAt: use.lastTimestamp, status: nil,
                resume: argv.map { .init(argv: $0, cwd: use.cwd) })
            rows.append(Row(session: session, usage: use, id: "\(key.account)|\(key.session)"))
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

    public var harnesses: [String] { Array(Set(rows.map(\.session.harness))).sorted() }
    public var projects: [String] { Array(Set(rows.compactMap(\.session.project))).sorted() }
    public var models: [String] { Array(Set(rows.flatMap { $0.usage?.models ?? [] })).sorted() }

    public var visible: [Row] {
        let now = now()
        let since = filter.window == .today
            ? calendar.startOfDay(for: now)
            : now.addingTimeInterval(-filter.window.seconds)
        let needle = filter.search.trimmingCharacters(in: .whitespaces).lowercased()
        return rows.filter { row in
            let s = row.session
            if let harness = filter.harness, s.harness != harness { return false }
            if s.lastActiveAt < since { return false }
            if let project = filter.project, s.project != project { return false }
            if let model = filter.model, !(row.usage?.models.contains(model) ?? false) { return false }
            if !needle.isEmpty {
                let haystack = ([s.title, s.cwd, s.project, s.id, s.agent] + (row.usage?.models ?? []).map(Optional.some))
                    .compactMap { $0 }.joined(separator: " ").lowercased()
                if !haystack.contains(needle) { return false }
            }
            return true
        }
    }
}
