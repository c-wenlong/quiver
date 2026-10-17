import Foundation
import os

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
    /// Pi homes; session logs are under `<home>/agent/sessions`.
    public var piHomes: [URL]
    /// Droid homes (`~/.factory`); session totals are under `<home>/sessions`.
    public var droidHomes: [URL]
    /// Cline homes; sessions are under `<home>/data/sessions`.
    public var clineHomes: [URL]

    public init(
        claudeHomes: [URL] = [], codexHomes: [URL] = [],
        openCodeDatabases: [URL] = [], devinDatabases: [URL] = [],
        piHomes: [URL] = [], droidHomes: [URL] = [], clineHomes: [URL] = []
    ) {
        self.claudeHomes = claudeHomes
        self.codexHomes = codexHomes
        self.openCodeDatabases = openCodeDatabases
        self.devinDatabases = devinDatabases
        self.piHomes = piHomes
        self.droidHomes = droidHomes
        self.clineHomes = clineHomes
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
            devinDatabases: [home.appendingPathComponent(".local/share/devin/cli/sessions.db")],
            piHomes: [home.appendingPathComponent(".pi")],
            droidHomes: [home.appendingPathComponent(".factory")],
            clineHomes: [home.appendingPathComponent(".cline")])
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

/// One model's usage for one harness, for the Pricing page.
public struct ModelUsage: Sendable, Equatable {
    public var model: String
    public var harness: String
    /// The logged tier for this slice ("fast", "priority"), nil for the
    /// base tier. The price it shows charges the tier's rates.
    public var tier: String?
    public var totals: UsageTotals
    /// Every event in the slice carried its own logged cost, so no price
    /// list was consulted.
    public var harnessBilled: Bool
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
    /// Sources that failed this round and were skipped, one note each. A
    /// source in a transient state (mid-rebuild, locked for recovery, an
    /// unexpected schema) must not take the rest of the scan down with it.
    public var notes: [String] = []
    public init() {}
}

/// The usage ledger: scans harness logs into a persisted, deduplicated event
/// store and answers the questions the Overview and Sessions pages ask.
///
/// - `scan(roots:)` reads only what changed since the last scan.
/// - `totals`, `daily`, `bySession` and `byAccount` price events at query time
///   with the current `PricingCatalog`: names resolve through today's list,
///   and each event pays the rate its price entry had when it happened.
public actor Ledger {
    let store: LedgerStore
    private var pricing: PricingCatalog
    private let reader: JSONLReader
    /// The ledger file's path and a lazily opened read-only connection to
    /// it, so the calendar's block query never queues behind a scan on
    /// the write connection or this actor.
    let activityURL: URL
    let activityConnection = OSAllocatedUnfairLock<SQLiteDatabase?>(initialState: nil)
    /// Settings' "Hide accidental sessions": whether the calendar drops
    /// trivial sessions. Outside the actor because the block query runs
    /// on its own read connection.
    let trivialGate = OSAllocatedUnfairLock<Bool>(initialState: true)

    /// Flips the calendar's trivial-session drop (Settings → "Hide
    /// accidental sessions").
    public nonisolated func setHidesTrivialSessions(_ hides: Bool) {
        trivialGate.withLock { $0 = hides }
    }

    /// The daily copies `backUpIfDue` takes.
    public nonisolated let backups: LedgerBackups
    private static let log = Logger(subsystem: "com.cwenlong.quiver", category: "ledger")

    /// - Parameters:
    ///   - directory: where the ledger database lives; production uses
    ///     `~/.quiver/data/ledger/` (see `LedgerLocation`).
    ///   - backups: where the daily copies go; defaults to `directory/backups`.
    ///   - pricing: defaults to the bundled LiteLLM snapshot.
    ///   - maxLineBytes: longer lines are skipped rather than buffered.
    public init(
        directory: URL, backups: URL? = nil,
        pricing: PricingCatalog = (try? .bundled()) ?? .empty, maxLineBytes: Int = 64 << 20
    ) throws {
        activityURL = directory.appendingPathComponent(LedgerLocation.fileName)
        store = try LedgerStore(url: activityURL)
        self.backups = LedgerBackups(directory: backups ?? directory.appendingPathComponent("backups"))
        self.pricing = pricing
        reader = JSONLReader(maxLineBytes: maxLineBytes)
    }

    public func setPricing(_ catalog: PricingCatalog) {
        pricing = catalog
    }

    /// The catalog currently in force, for views that look models up
    /// directly (the Pricing page).
    public func catalog() -> PricingCatalog { pricing }

    // MARK: - Backups

    /// Takes today's copy of the ledger when there is none yet. Returns the
    /// copy, or nil when today's exists already.
    @discardableResult
    public func backUpIfDue(now: Date = Date(), calendar: Calendar = .current) throws -> URL? {
        try backups.backUpIfDue(store.db, now: now, calendar: calendar)
    }

    /// Checks for a due copy every `interval` until cancelled, so an app
    /// left running for days still takes one each day.
    public func keepBackedUp(every interval: Duration = .seconds(3600)) async {
        while !Task.isCancelled {
            do { try backUpIfDue() } catch {
                Self.log.error("ledger backup failed: \(error)")
            }
            do { try await Task.sleep(for: interval) } catch { return }
        }
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
            report.databasesRead += try scanSource(database, kind: HarnessMark("opencode").name,
                                                   scan: DatabaseSources.scanOpenCode, report: &report)
        }
        for database in roots.devinDatabases where FileManager.default.fileExists(atPath: database.path) {
            report.databasesRead += try scanSource(database, kind: HarnessMark("devin").name,
                                                   scan: DatabaseSources.scanDevin, report: &report)
        }
        for home in roots.piHomes {
            for file in Self.jsonlFiles(under: home.appendingPathComponent("agent/sessions")) {
                if Task.isCancelled { return report }
                try scanFile(file, accountHome: home.path, parser: PiFileParser.self, seen: &seen, report: &report)
                await Task.yield()
            }
        }
        for home in roots.droidHomes {
            for file in Self.files(under: home.appendingPathComponent("sessions"), suffix: ".settings.json") {
                if Task.isCancelled { return report }
                try scanSessionFile(file, accountHome: home.path,
                                    watched: SessionTotalsFile.droidTranscript(for: file),
                                    parse: { try SessionTotalsFile.droid(settings: $0, accountHome: home.path) },
                                    report: &report)
                await Task.yield()
            }
        }
        for home in roots.clineHomes {
            // `<id>/<id>.json`: the stem must equal its session directory.
            for file in Self.files(under: home.appendingPathComponent("data/sessions"), suffix: ".json")
            where file.deletingPathExtension().lastPathComponent
                == file.deletingLastPathComponent().lastPathComponent {
                if Task.isCancelled { return report }
                try scanSessionFile(file, accountHome: home.path,
                                    parse: { try SessionTotalsFile.cline(session: $0, accountHome: home.path) },
                                    report: &report)
                await Task.yield()
            }
        }
        return report
    }

    /// One database source. A failure reading the source becomes a note and
    /// a skip: the file can be locked or rebuilt by the app that owns it at
    /// any moment, and one unreadable source is no reason to drop the
    /// others. A failure on Quiver's own cache still throws, so the
    /// Overview shows the error and the scan does not count as complete.
    private func scanSource(
        _ url: URL, kind: String,
        scan: (URL, LedgerStore) throws -> Bool, report: inout ScanReport
    ) throws -> Int {
        do {
            return try scan(url, store) ? 1 : 0
        } catch let error as DatabaseSources.SourceRead {
            report.notes.append(
                "Skipped \(kind)'s database (\(url.path)): \(error.underlying.localizedDescription)")
            return 0
        }
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

    /// Every file under `directory` ending in `suffix`, path-sorted.
    static func files(under directory: URL, suffix: String) -> [URL] {
        guard let enumerator = FileManager.default.enumerator(
            at: directory, includingPropertiesForKeys: [.isRegularFileKey], options: [.skipsHiddenFiles])
        else { return [] }
        var files: [URL] = []
        for case let url as URL in enumerator where url.lastPathComponent.hasSuffix(suffix) {
            files.append(url)
        }
        return files.sorted { $0.path < $1.path }
    }

    /// A second file a session-total file is read together with, stored in
    /// the cursor's state so its stamp changes also trigger a re-read
    /// (Droid's `<uuid>.jsonl` carries the timestamp and cwd its
    /// `.settings.json` sidecar does not).
    private struct WatchedStamp: Codable, Equatable {
        var inode: UInt64
        var size: UInt64
        var modified: Double

        init?(_ path: String) {
            guard let info = FileInfo(path: path) else { return nil }
            inode = info.inode
            size = info.size
            modified = info.modified
        }
    }

    /// One session-total file (Droid's `.settings.json`, Cline's session
    /// JSON): re-read whole whenever its stamp (or its watched sibling's)
    /// changes; the record upserts under the session's stable key, replacing
    /// rather than duplicating. A file that now reports no usage deletes its
    /// old event.
    private func scanSessionFile(
        _ url: URL, accountHome: String, watched: URL? = nil,
        parse: (URL) throws -> SessionTotals?, report: inout ScanReport
    ) throws {
        let path = url.resolvingSymlinksInPath().path
        guard let info = FileInfo(path: path) else { return }
        // The watched file's absence is part of the stamp, so a transcript
        // appearing or disappearing re-reads too.
        let watchedStamp = watched.flatMap { WatchedStamp($0.path) }
        let state = watched.map { _ in (try? JSONEncoder().encode(watchedStamp)) ?? nil } ?? nil
        if let saved = try store.cursor(for: path),
           saved.inode == info.inode, saved.size == info.size, saved.modified == info.modified,
           saved.state == state {
            report.filesUnchanged += 1
            return
        }
        // A bad file (mid-rewrite, corrupt) skips this round without a
        // cursor, so it is retried on the next scan instead of aborting
        // every source after it.
        let totals: SessionTotals?
        do {
            totals = try parse(URL(fileURLWithPath: path))
        } catch {
            report.notes.append("\(url.lastPathComponent): \(error.localizedDescription)")
            return
        }
        try store.db.execute("BEGIN IMMEDIATE")
        do {
            switch totals {
            case .record(let record): try store.upsert(record)
            case .empty(let key): try store.delete(key: key)
            case nil: break
            }
            try store.save(FileCursor(
                path: path, inode: info.inode, size: info.size, modified: info.modified,
                offset: info.size, headHash: 0, headLength: 0, tailHash: 0, tailLength: 0,
                state: state, skippedLines: 0))
            try store.db.execute("COMMIT")
        } catch {
            store.rollback()
            throw error
        }
        report.filesRead += 1
    }

    /// Store records a poll produced (Cursor's usage feed) in one
    /// transaction. Every record carries a stable key, so an overlapping
    /// re-fetch upserts to the same rows instead of duplicating them.
    /// - Returns: how many rows were inserted or changed, so the caller
    ///   can tell a no-op ingest from one the Overview should reload for.
    @discardableResult
    func ingest(_ records: [LedgerRecord]) throws -> Int {
        var changed = 0
        try store.db.execute("BEGIN IMMEDIATE")
        do {
            for record in records {
                if try store.upsert(record) { changed += 1 }
            }
            try store.db.execute("COMMIT")
        } catch {
            store.rollback()
            throw error
        }
        return changed
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
            in: range, by: [.harness, .model], thresholds: pricing.thresholds, bucketSeconds: 900,
            priceChanges: priceChanges(in: range))
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

    /// One account home's token totals per calendar day for one harness —
    /// every token kind summed, the same `totalTokens` Overview reports.
    /// Days with no usage are left out.
    public func dailyTokens(harness: String, accountHome: String,
                            in range: DateInterval, calendar: Calendar) -> [Date: Int] {
        guard let groups = try? store.groups(
            in: range, by: [.harness, .account], thresholds: pricing.thresholds,
            bucketSeconds: 900)
        else { return [:] }
        var days: [Date: Int] = [:]
        for group in groups
        where group.dimensions[.harness] == harness
            && group.dimensions[.account] == accountHome {
            let day = calendar.startOfDay(for: max(group.bucket ?? group.first, range.start))
            days[day, default: 0] += group.tokens.total
        }
        return days
    }

    /// Usage per session, most recent first. `harness` restricts the read
    /// to one harness's dims when the caller only wants those.
    public func bySession(in range: DateInterval, harness: String? = nil) -> [SessionUsage] {
        guard let groups = try? store.groups(
            in: range, by: [.harness, .account, .session, .cwd],
            thresholds: pricing.thresholds, harness: harness, priceChanges: priceChanges(in: range))
        else { return [] }
        struct Key: Hashable { var harness, account, session: String }
        var sessions: [Key: SessionUsage] = [:]
        // Events per folder, in first-seen order: a side session's few
        // notes-folder events must not name it.
        var cwdVotes: [Key: [(cwd: String, events: Int)]] = [:]
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
            if let cwd {
                if let index = cwdVotes[key]?.firstIndex(where: { $0.cwd == cwd }) {
                    cwdVotes[key]![index].events += group.count
                } else {
                    cwdVotes[key, default: []].append((cwd, group.count))
                }
            }
            sessions[key] = session
        }
        for (key, votes) in cwdVotes { sessions[key]?.cwd = SessionFolders.pick(votes) }
        return sessions.values
            .map { var s = $0; s.models.sort(); return s }
            .sorted { ($0.lastTimestamp, $0.sessionId) > ($1.lastTimestamp, $1.sessionId) }
    }

    /// Usage per model and harness inside `range`, priced like the other
    /// queries, for the Pricing page's per-model rows.
    public func byModelHarness(in range: DateInterval) -> [ModelUsage] {
        guard let groups = try? store.groups(
            in: range, by: [.harness, .model], thresholds: pricing.thresholds,
            priceChanges: priceChanges(in: range))
        else { return [] }
        return groups.map { group in
            ModelUsage(model: group.model, harness: group.dimensions[.harness] ?? "",
                       tier: group.tier,
                       totals: price(group), harnessBilled: group.hasLoggedCost)
        }
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
        guard let groups = try? store.groups(in: range, by: columns, thresholds: pricing.thresholds,
                                             priceChanges: priceChanges(in: range)) else {
            return [:]
        }
        var result: [[String]: UsageTotals] = [:]
        for group in groups {
            let key = columns.map { group.dimensions[$0] ?? "" }
            result[key, default: UsageTotals()].add(price(group))
        }
        return result
    }

    /// The moments inside `range` at which the price entry of any model
    /// the ledger holds changed. Splitting the query there keeps every
    /// group inside one price; other entries' changes split nothing.
    private func priceChanges(in range: DateInterval) -> [Double] {
        guard pricing.hasHistory, let used = try? store.modelsAndTiers() else { return [] }
        var entries = Set<String>()
        for (model, tier) in used {
            if case .priced(_, let entry, _, _) = pricing.resolve(model: model, tier: tier) {
                entries.insert(entry)
            }
        }
        let start = range.start.timeIntervalSince1970, end = range.end.timeIntervalSince1970
        return pricing.priceChanges(for: entries).filter { $0 > start && $0 < end }
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
            return totals
        }
        // One resolution for the ledger and the Pricing page: the literal
        // name wins (`xai/grok-code-fast` is a real entry), then the
        // `-fast` tier suffix, then the free-model check.
        switch pricing.resolve(model: group.model, tier: group.tier ?? "") {
        case .priced(let current, let entry, _, let tier):
            // A group never spans a price change, so its first event's
            // time picks the one rate every event in it paid.
            let rates = pricing.historicalRates(entry: entry, at: group.first.timeIntervalSince1970) ?? current
            let crosses = rates.longContext.map { long in
                (pricing.thresholds.firstIndex(of: long.threshold) ?? Int.max) < group.thresholdsCrossed
            } ?? false
            totals.costUSD = rates.cost(
                input: t.input, output: t.output, cacheRead: t.cacheRead, cacheWrite: t.cacheWrite,
                cacheWrite1h: t.cacheWrite1h, reasoning: t.reasoning, tier: tier, crossesLongContext: crosses)
        case .free:
            // A free model is priced at zero, not reported as unpriced.
            totals.costUSD = 0
        case nil:
            if t.total > 0 {
                totals.unpricedTokens = t.total
                totals.unpricedModels = [group.model]
            }
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
