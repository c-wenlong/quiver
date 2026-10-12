import Foundation

/// Where to look for usage. Each list can name several homes because Quiver
/// keeps extra accounts under `~/.quiver/accounts/<vendor>/<id>/home`.
public struct LedgerRoots: Sendable, Equatable {
    /// Claude config homes; transcripts are under `<home>/projects`.
    public var claudeHomes: [URL]
    /// Codex homes (`CODEX_HOME`); rollouts are under `sessions/` and `archived_sessions/`.
    public var codexHomes: [URL]
    /// OpenCode databases (`opencode.db`).
    public var openCodeDatabases: [URL]
    /// Devin CLI databases (`sessions.db`).
    public var devinDatabases: [URL]

    public init(
        claudeHomes: [URL] = [], codexHomes: [URL] = [],
        openCodeDatabases: [URL] = [], devinDatabases: [URL] = []
    ) {
        self.claudeHomes = claudeHomes
        self.codexHomes = codexHomes
        self.openCodeDatabases = openCodeDatabases
        self.devinDatabases = devinDatabases
    }

    /// The default locations under one user home, plus any profile homes.
    public static func standard(
        home: URL = FileManager.default.homeDirectoryForCurrentUser,
        claudeProfiles: [URL] = [], codexProfiles: [URL] = []
    ) -> LedgerRoots {
        LedgerRoots(
            claudeHomes: [home.appendingPathComponent(".claude")] + claudeProfiles,
            codexHomes: [home.appendingPathComponent(".codex")] + codexProfiles,
            openCodeDatabases: [home.appendingPathComponent(".local/share/opencode/opencode.db")],
            devinDatabases: [home.appendingPathComponent(".local/share/devin/cli/sessions.db")])
    }
}

/// Token and dollar totals for a set of events.
public struct UsageTotals: Sendable, Equatable, Codable {
    public var inputTokens = 0
    public var outputTokens = 0
    public var cacheReadTokens = 0
    public var cacheWriteTokens = 0
    public var reasoningTokens = 0
    /// USD for every event that could be priced: the log's own cost when it
    /// has one, otherwise the catalog's rates.
    public var costUSD = 0.0
    /// Tokens of events whose model the catalog does not know. They are in the
    /// token counts above but not in `costUSD`.
    public var unpricedTokens = 0
    public var unpricedModels: Set<String> = []
    public var eventCount = 0

    public init() {}

    public var totalTokens: Int {
        inputTokens + outputTokens + cacheReadTokens + cacheWriteTokens + reasoningTokens
    }

    mutating func add(_ other: UsageTotals) {
        inputTokens += other.inputTokens
        outputTokens += other.outputTokens
        cacheReadTokens += other.cacheReadTokens
        cacheWriteTokens += other.cacheWriteTokens
        reasoningTokens += other.reasoningTokens
        costUSD += other.costUSD
        unpricedTokens += other.unpricedTokens
        unpricedModels.formUnion(other.unpricedModels)
        eventCount += other.eventCount
    }
}

/// One harness and model on one calendar day.
public struct DailyUsage: Sendable, Equatable {
    /// Start of the day in the calendar the query used.
    public var day: Date
    public var harness: String
    public var model: String
    public var totals: UsageTotals

    public init(day: Date, harness: String, model: String, totals: UsageTotals) {
        self.day = day
        self.harness = harness
        self.model = model
        self.totals = totals
    }
}

/// Usage of one session inside a date range.
public struct SessionUsage: Sendable, Equatable, Codable {
    public var sessionId: String
    public var harness: String
    public var accountHome: String
    /// The working directory of the session's latest event.
    public var cwd: String?
    public var models: [String]
    public var totals: UsageTotals
    public var firstTimestamp: Date
    public var lastTimestamp: Date
}

/// What a scan did.
public struct ScanReport: Sendable, Equatable {
    public var filesRead = 0
    public var filesUnchanged = 0
    /// Files read from the start again because they were replaced or truncated.
    public var filesRescanned = 0
    /// Lines over the size limit that were skipped.
    public var skippedLines = 0
    public var databasesRead = 0
    public init() {}
}

/// The usage ledger: scans harness logs into a persisted, deduplicated event
/// store and answers the questions the Overview and Sessions pages ask.
///
/// - `scan(roots:)` reads only what changed since the last scan.
/// - `totals`, `daily`, `bySession` and `byAccount` price events at query time
///   with the current `PricingCatalog`.
public actor Ledger {
    private let store: LedgerStore
    private var pricing: PricingCatalog
    private let reader: JSONLReader

    /// - Parameters:
    ///   - cacheDirectory: where the ledger database lives; production uses
    ///     `~/.quiver/cache/ledger/`.
    ///   - pricing: defaults to the bundled LiteLLM snapshot.
    ///   - maxLineBytes: longer lines are skipped rather than buffered.
    public init(
        cacheDirectory: URL, pricing: PricingCatalog = (try? .bundled()) ?? .empty, maxLineBytes: Int = 64 << 20
    ) throws {
        store = try LedgerStore(url: cacheDirectory.appendingPathComponent("ledger.sqlite"))
        self.pricing = pricing
        reader = JSONLReader(maxLineBytes: maxLineBytes)
    }

    public func setPricing(_ catalog: PricingCatalog) {
        pricing = catalog
    }

    // MARK: - Scanning

    @discardableResult
    public func scan(roots: LedgerRoots) async throws -> ScanReport {
        var report = ScanReport()
        var seen = Set<String>()
        for home in roots.claudeHomes {
            for file in Self.jsonlFiles(under: home.appendingPathComponent("projects")) {
                if Task.isCancelled { return report }
                try scanFile(file, accountHome: home.path, parser: ClaudeFileParser.self, seen: &seen, report: &report)
                await Task.yield()
            }
        }
        for home in roots.codexHomes {
            // Active sessions first, so a rollout that also sits in the archive
            // is attributed to the live copy.
            for directory in ["sessions", "archived_sessions"] {
                for file in Self.jsonlFiles(under: home.appendingPathComponent(directory)) {
                    if Task.isCancelled { return report }
                    try scanFile(file, accountHome: home.path, parser: CodexFileParser.self, seen: &seen, report: &report)
                    await Task.yield()
                }
            }
        }
        for database in roots.openCodeDatabases where FileManager.default.fileExists(atPath: database.path) {
            if try DatabaseSources.scanOpenCode(database, store: store) { report.databasesRead += 1 }
        }
        for database in roots.devinDatabases where FileManager.default.fileExists(atPath: database.path) {
            if try DatabaseSources.scanDevin(database, store: store) { report.databasesRead += 1 }
        }
        return report
    }

    private func scanFile<P: FileLogParser>(
        _ url: URL, accountHome: String, parser: P.Type, seen: inout Set<String>, report: inout ScanReport
    ) throws {
        let path = url.resolvingSymlinksInPath().path
        guard seen.insert(path).inserted, let info = FileInfo(path: path) else { return }
        let saved = try store.cursor(for: path)

        var start: UInt64 = 0
        var state: Data?
        if let saved {
            if saved.inode == info.inode, saved.size == info.size, saved.modified == info.modified,
               saved.offset == info.size {
                report.filesUnchanged += 1
                return
            }
            if saved.inode == info.inode, info.size >= saved.offset, Self.fingerprintsMatch(saved, path: path) {
                start = saved.offset
                state = saved.state
            } else {
                report.filesRescanned += 1
            }
        }

        var lineParser = P(file: url, accountHome: accountHome, savedState: state)
        let store = self.store
        try store.db.execute("BEGIN IMMEDIATE")
        do {
            let result = try reader.read(URL(fileURLWithPath: path), from: start) { line in
                for record in lineParser.records(fromLine: line) {
                    try store.upsert(record)
                }
            }
            let head = Self.hashRange(path: path, from: 0, length: min(4096, result.endOffset))
            let tailLength = min(4096, result.endOffset)
            let tail = Self.hashRange(path: path, from: result.endOffset - tailLength, length: tailLength)
            try store.save(FileCursor(
                path: path, inode: info.inode, size: info.size, modified: info.modified,
                offset: result.endOffset, headHash: head, headLength: Int(min(4096, result.endOffset)),
                tailHash: tail, tailLength: Int(tailLength), state: lineParser.savedState(),
                skippedLines: (start > 0 ? saved?.skippedLines ?? 0 : 0) + result.skippedLines))
            try store.db.execute("COMMIT")
            report.skippedLines += result.skippedLines
        } catch {
            store.rollback()
            throw error
        }
        report.filesRead += 1
    }

    private static func fingerprintsMatch(_ cursor: FileCursor, path: String) -> Bool {
        hashRange(path: path, from: 0, length: UInt64(cursor.headLength)) == cursor.headHash
            && hashRange(path: path, from: cursor.offset - UInt64(cursor.tailLength), length: UInt64(cursor.tailLength))
            == cursor.tailHash
    }

    private static func hashRange(path: String, from offset: UInt64, length: UInt64) -> Int64 {
        guard length > 0, let handle = FileHandle(forReadingAtPath: path) else { return StableHash.of("") }
        defer { try? handle.close() }
        guard (try? handle.seek(toOffset: offset)) != nil,
              let data = try? handle.read(upToCount: Int(length)), data.count == Int(length)
        else { return 0 }
        return Int64(bitPattern: StableHash.of(data))
    }

    /// Every `*.jsonl` under `directory`, path-sorted so dedup's keep-first is
    /// deterministic.
    static func jsonlFiles(under directory: URL) -> [URL] {
        guard let enumerator = FileManager.default.enumerator(
            at: directory, includingPropertiesForKeys: [.isRegularFileKey], options: [.skipsHiddenFiles])
        else { return [] }
        var files: [URL] = []
        for case let url as URL in enumerator where url.pathExtension == "jsonl" {
            files.append(url)
        }
        return files.sorted { $0.path < $1.path }
    }

    // MARK: - Queries

    public func events(in range: DateInterval) -> [UsageEvent] {
        (try? store.events(in: range)) ?? []
    }

    public func totals(in range: DateInterval) -> UsageTotals {
        aggregate(in: range, by: []).values.reduce(into: UsageTotals()) { $0.add($1) }
    }

    /// Usage per calendar day, harness and model. Days with no usage are left out.
    ///
    /// One query in 15-minute buckets, each then placed in its local day:
    /// every timezone offset in use is a whole number of quarter hours, so a
    /// bucket never straddles midnight.
    public func daily(in range: DateInterval, calendar: Calendar) -> [DailyUsage] {
        guard let groups = try? store.groups(
            in: range, by: [.harness, .model], thresholds: pricing.thresholds, bucketSeconds: 900)
        else { return [] }
        struct Key: Hashable { var day: Date, harness: String, model: String }
        var days: [Key: UsageTotals] = [:]
        for group in groups {
            let day = calendar.startOfDay(for: max(group.bucket ?? group.first, range.start))
            days[Key(day: day, harness: group.dimensions[.harness] ?? "", model: group.model), default: UsageTotals()]
                .add(price(group))
        }
        return days
            .map { DailyUsage(day: $0.key.day, harness: $0.key.harness, model: $0.key.model, totals: $0.value) }
            .sorted { ($0.day, $0.harness, $0.model) < ($1.day, $1.harness, $1.model) }
    }

    /// Usage per session, most recent first.
    public func bySession(in range: DateInterval) -> [SessionUsage] {
        guard let groups = try? store.groups(
            in: range, by: [.harness, .account, .session, .cwd], thresholds: pricing.thresholds)
        else { return [] }
        struct Key: Hashable { var harness, account, session: String }
        var sessions: [Key: SessionUsage] = [:]
        var cwdSeenAt: [Key: Date] = [:]
        for group in groups {
            let key = Key(harness: group.dimensions[.harness] ?? "", account: group.dimensions[.account] ?? "",
                          session: group.dimensions[.session] ?? "")
            let totals = price(group)
            let cwd = group.dimensions[.cwd].flatMap { $0.isEmpty ? nil : $0 }
            var session = sessions[key] ?? SessionUsage(
                sessionId: key.session, harness: key.harness, accountHome: key.account, cwd: nil,
                models: [], totals: UsageTotals(), firstTimestamp: group.first, lastTimestamp: group.last)
            session.totals.add(totals)
            if !session.models.contains(group.model) { session.models.append(group.model) }
            session.firstTimestamp = min(session.firstTimestamp, group.first)
            session.lastTimestamp = max(session.lastTimestamp, group.last)
            if let cwd, group.last >= (cwdSeenAt[key] ?? .distantPast) {
                session.cwd = cwd
                cwdSeenAt[key] = group.last
            }
            sessions[key] = session
        }
        return sessions.values
            .map { var s = $0; s.models.sort(); return s }
            .sorted { ($0.lastTimestamp, $0.sessionId) > ($1.lastTimestamp, $1.sessionId) }
    }

    /// Usage per account home.
    public func byAccount(in range: DateInterval) -> [String: UsageTotals] {
        Dictionary(uniqueKeysWithValues: aggregate(in: range, by: [.account]).map { ($0.key[0], $0.value) })
    }

    /// The harness each account home's usage came from: the one with the
    /// most tokens, should a home ever hold more than one.
    public func harnessByAccount(in range: DateInterval) -> [String: String] {
        var best: [String: (harness: String, tokens: Int)] = [:]
        for (key, totals) in aggregate(in: range, by: [.account, .harness]) {
            let (account, harness) = (key[0], key[1])
            guard !harness.isEmpty else { continue }
            if let current = best[account],
               (current.tokens, harness) >= (totals.totalTokens, current.harness) { continue }
            best[account] = (harness, totals.totalTokens)
        }
        return best.mapValues(\.harness)
    }

    /// Usage per harness.
    public func byHarness(in range: DateInterval) -> [String: UsageTotals] {
        Dictionary(uniqueKeysWithValues: aggregate(in: range, by: [.harness]).map { ($0.key[0], $0.value) })
    }

    private func aggregate(in range: DateInterval, by columns: [LedgerStore.Column]) -> [[String]: UsageTotals] {
        guard let groups = try? store.groups(in: range, by: columns, thresholds: pricing.thresholds) else {
            return [:]
        }
        var result: [[String]: UsageTotals] = [:]
        for group in groups {
            let key = columns.map { group.dimensions[$0] ?? "" }
            result[key, default: UsageTotals()].add(price(group))
        }
        return result
    }

    private func price(_ group: LedgerStore.PricedGroup) -> UsageTotals {
        var totals = UsageTotals()
        let t = group.tokens
        totals.inputTokens = t.input
        totals.outputTokens = t.output
        totals.cacheReadTokens = t.cacheRead
        totals.cacheWriteTokens = t.cacheWrite
        totals.reasoningTokens = t.reasoning
        totals.eventCount = group.count
        if group.hasLoggedCost {
            totals.costUSD = group.loggedCost
        } else if let rates = pricing.rates(for: group.model) {
            let crosses = rates.longContext.map { long in
                (pricing.thresholds.firstIndex(of: long.threshold) ?? Int.max) < group.thresholdsCrossed
            } ?? false
            totals.costUSD = rates.cost(
                input: t.input, output: t.output, cacheRead: t.cacheRead, cacheWrite: t.cacheWrite,
                cacheWrite1h: t.cacheWrite1h, reasoning: t.reasoning, tier: group.tier, crossesLongContext: crosses)
        } else if t.total > 0 {
            totals.unpricedTokens = t.total
            totals.unpricedModels = [group.model]
        }
        return totals
    }
}

/// `stat` of one file.
struct FileInfo {
    var inode: UInt64
    var size: UInt64
    var modified: Double

    init?(path: String) {
        var info = stat()
        guard stat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG else { return nil }
        inode = UInt64(info.st_ino)
        size = UInt64(info.st_size)
        modified = Double(info.st_mtimespec.tv_sec) + Double(info.st_mtimespec.tv_nsec) / 1e9
    }
}

/// Parses the lines of one log file. A parser that needs context from earlier
/// lines (Codex's running model and totals) saves it with the file's cursor,
/// so an appended file resumes with the right state.
protocol FileLogParser {
    init(file: URL, accountHome: String, savedState: Data?)
    mutating func records(fromLine line: Data) -> [LedgerRecord]
    func savedState() -> Data?
}

struct ClaudeFileParser: FileLogParser {
    let accountHome: String
    init(file: URL, accountHome: String, savedState: Data?) { self.accountHome = accountHome }
    func records(fromLine line: Data) -> [LedgerRecord] {
        ClaudeLogParser.records(fromLine: line, accountHome: accountHome)
    }
    func savedState() -> Data? { nil }
}
