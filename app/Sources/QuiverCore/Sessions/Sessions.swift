import Foundation
import Observation

/// One session as `swe session --json` lists it.
public struct SessionInfo: Decodable, Sendable, Equatable, Identifiable {
    public struct Resume: Decodable, Sendable, Equatable {
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
        public var id: String { session.id }
        public var session: SessionInfo
        public var usage: SessionUsage?

        public init(session: SessionInfo, usage: SessionUsage?) {
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
    /// Only the newest list load writes the list.
    private var generation = 0
    /// Only the newest usage read (from a load or a refresh) writes usage.
    private var usageGeneration = 0

    /// - Parameters:
    ///   - defaultHomes: the Claude and Codex homes `swe session` reads. The
    ///     ledger's sessions from any other home (Quiver's extra accounts)
    ///     are added from the ledger, since swe never lists them.
    ///   - limit: the most sessions to ask swe for.
    public init(source: SessionListing, ledger: SessionUsageQuerying, defaultHomes: Set<String> = [],
                limit: Int = 100_000, now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.source = source
        self.ledger = ledger
        self.defaultHomes = defaultHomes
        self.limit = limit
        self.now = now
        self.calendar = calendar
    }

    public func load() async {
        generation += 1
        let mine = generation
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
    }

    /// Re-reads tokens and spend from the ledger after a scan, keeping the list.
    public func refreshUsage() async {
        usageGeneration += 1
        let mine = usageGeneration
        let usage = await usageBySession()
        guard mine == usageGeneration else { return }
        latestUsage = usage
        rows = join(listed, usage)
    }

    private var latestUsage: [String: SessionUsage]?

    /// Every session's usage over its whole history: a session resumed
    /// today may have started months ago.
    private func usageBySession() async -> [String: SessionUsage] {
        let usage = await ledger.bySession(in: DateInterval(start: Date(timeIntervalSince1970: 0), end: now()))
        var byId: [String: SessionUsage] = [:]
        for row in usage {
            if var existing = byId[row.sessionId] {
                existing.totals.add(row.totals)
                existing.models = Array(Set(existing.models + row.models)).sorted()
                existing.firstTimestamp = min(existing.firstTimestamp, row.firstTimestamp)
                existing.lastTimestamp = max(existing.lastTimestamp, row.lastTimestamp)
                existing.cwd = existing.cwd ?? row.cwd
                byId[row.sessionId] = existing
            } else {
                byId[row.sessionId] = row
            }
        }
        return byId
    }

    private func join(_ sessions: [SessionInfo], _ usage: [String: SessionUsage]) -> [Row] {
        var rows = sessions.map { Row(session: $0, usage: usage[$0.id]) }
        let listedIds = Set(sessions.map(\.id))
        let since = now().addingTimeInterval(-Double(Self.days) * 86_400)
        for (id, use) in usage where !listedIds.contains(id) && !defaultHomes.contains(use.accountHome)
            && use.lastTimestamp >= since {
            guard let resume = Self.resumeArgv(harness: use.harness, id: id) else { continue }
            let session = SessionInfo(
                id: id, harness: use.harness, agent: UsageFormat.harness(use.harness), cwd: use.cwd,
                title: nil, titleSource: nil, lastActiveAt: use.lastTimestamp, status: nil,
                resume: .init(argv: resume, cwd: use.cwd))
            rows.append(Row(session: session, usage: use))
        }
        return rows.sorted { ($0.session.lastActiveAt, $1.id) > ($1.session.lastActiveAt, $0.id) }
    }

    /// How each CLI resumes a session by id.
    static func resumeArgv(harness: String, id: String) -> [String]? {
        switch harness {
        case "claude": ["claude", "--resume", id]
        case "codex": ["codex", "resume", id]
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
