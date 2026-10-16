import Foundation

/// Where a file was read up to, and enough of its content to tell an appended
/// file (resume at `offset`) from a replaced or truncated one (read it again).
struct FileCursor: Equatable {
    var path: String
    var inode: UInt64
    var size: UInt64
    var modified: Double
    var offset: UInt64
    /// Hash of the first `headLength` bytes. Those bytes sit before `offset`,
    /// so appending never changes them.
    var headHash: Int64
    var headLength: Int
    /// Hash of the `tailLength` bytes that end at `offset`.
    var tailHash: Int64
    var tailLength: Int
    /// The parser's state at `offset`, for parsers that carry state between lines.
    var state: Data?
    var skippedLines: Int
}

/// The persisted ledger: one row per deduplicated response, plus per-file
/// cursors. SQLite, so a scan commits a file's events and its cursor in one
/// transaction (a crash cannot count a line twice), memory stays flat however
/// long the history is, and the rows outlive the logs they came from.
///
/// Rows hold token counts, not dollars. Cost is applied when queried, so a
/// pricing refresh reprices the whole history and the long-context and
/// 1-hour-cache rules can still see each request on its own.
final class LedgerStore {
    /// Bump when parsing changes what a row means. A mismatch drops the rows
    /// and cursors and the next scan rebuilds them, so history whose logs are
    /// gone is lost: bump only when the old rows are wrong.
    static let schemaVersion = 1

    let db: SQLiteDatabase
    private var dimIDs: [DimKey: Int64] = [:]
    private var selectByKey: Statement!
    private var selectByMessage: Statement!
    private var insertEvent: Statement!
    private var deleteEvent: Statement!
    private var selectDim: Statement!
    private var insertDim: Statement!

    struct DimKey: Hashable {
        var harness, account, session, cwd, model, tier: String
    }

    init(url: URL) throws {
        // The ledger names projects and sessions, so only the user reads it.
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        db = try SQLiteDatabase(url: url)
        try db.execute("PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA foreign_keys = OFF;")
        try db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        // Scoped: an unfinalized statement that read a row keeps a cursor on
        // meta open, which locks the INSERT below with SQLITE_LOCKED.
        let stored: String? = try {
            let version = try db.prepare("SELECT value FROM meta WHERE key = 'schema'")
            return try version.step() ? version.text(0) : nil
        }()
        if stored != String(Self.schemaVersion) {
            try archiveOldTables(at: url, stored: stored)
            try db.execute("DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS dims; DROP TABLE IF EXISTS cursors;")
        }
        try db.execute("""
            CREATE TABLE IF NOT EXISTS dims (
                id INTEGER PRIMARY KEY,
                harness TEXT NOT NULL, account TEXT NOT NULL, session TEXT NOT NULL,
                cwd TEXT NOT NULL, model TEXT NOT NULL, tier TEXT NOT NULL,
                UNIQUE (harness, account, session, cwd, model, tier));
            CREATE TABLE IF NOT EXISTS events (
                key INTEGER PRIMARY KEY,
                msg INTEGER,
                sidechain INTEGER NOT NULL,
                dim INTEGER NOT NULL,
                ts REAL NOT NULL,
                input INTEGER NOT NULL, output INTEGER NOT NULL,
                cache_read INTEGER NOT NULL, cache_write INTEGER NOT NULL, cache_write_1h INTEGER NOT NULL,
                reasoning INTEGER NOT NULL,
                cost REAL);
            CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
            CREATE INDEX IF NOT EXISTS events_msg ON events (msg) WHERE msg IS NOT NULL;
            CREATE TABLE IF NOT EXISTS cursors (
                path TEXT PRIMARY KEY, inode INTEGER NOT NULL, size INTEGER NOT NULL, modified REAL NOT NULL,
                offset INTEGER NOT NULL, head_hash INTEGER NOT NULL, head_length INTEGER NOT NULL,
                tail_hash INTEGER NOT NULL, tail_length INTEGER NOT NULL, state BLOB, skipped INTEGER NOT NULL);
            INSERT OR REPLACE INTO meta (key, value) VALUES ('schema', '\(Self.schemaVersion)');
            """)
        // Session-total events never count toward long-context surcharges.
        // Old rows are correct with the 0 default, so no schema bump.
        try? db.execute("ALTER TABLE events ADD COLUMN session_total INTEGER NOT NULL DEFAULT 0")
        selectByKey = try db.prepare(
            "SELECT sidechain, input + output + cache_read + cache_write + reasoning FROM events WHERE key = ?")
        selectByMessage = try db.prepare("""
            SELECT key, sidechain, input + output + cache_read + cache_write + reasoning
            FROM events WHERE msg = ? AND (sidechain = 1 OR ? = 1) LIMIT 1
            """)
        insertEvent = try db.prepare("""
            INSERT OR REPLACE INTO events (key, msg, sidechain, dim, ts, input, output, cache_read, cache_write,
                cache_write_1h, reasoning, session_total, cost)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """)
        deleteEvent = try db.prepare("DELETE FROM events WHERE key = ?")
        selectDim = try db.prepare(
            "SELECT id FROM dims WHERE harness = ? AND account = ? AND session = ? AND cwd = ? AND model = ? AND tier = ?")
        insertDim = try db.prepare(
            "INSERT INTO dims (harness, account, session, cwd, model, tier) VALUES (?, ?, ?, ?, ?, ?)")
    }

    /// A schema mismatch drops the tables below and the next scan rebuilds
    /// them, which loses rows whose logs are gone. Copy the database first:
    /// it stays beside the live one as `ledger.schema-<version>.sqlite` (a
    /// `.N` suffix when one exists already). Best effort: a failed copy
    /// never blocks the rebuild.
    private func archiveOldTables(at url: URL, stored: String?) throws {
        let hasEvents = try db.prepare(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'events'"
        ).step()
        guard hasEvents else { return }
        let tag = String((stored ?? "unknown").map {
            $0.isLetter || $0.isNumber || $0 == "-" || $0 == "." ? $0 : "_"
        })
        let directory = url.deletingLastPathComponent()
        let stem = url.deletingPathExtension().lastPathComponent
        var candidate = directory.appending(path: "\(stem).schema-\(tag).sqlite")
        var copy = 1
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = directory.appending(path: "\(stem).schema-\(tag).\(copy).sqlite")
            copy += 1
        }
        let escaped = candidate.path.replacingOccurrences(of: "'", with: "''")
        try? db.execute("VACUUM INTO '\(escaped)'")
    }

    // MARK: - Events

    /// Adds a record unless the ledger already holds the same response. On a
    /// collision the non-sidechain copy wins, then the larger token total.
    func upsert(_ record: LedgerRecord) throws {
        let key = StableHash.of(record.key)
        let total = Int64(record.event.totalTokens)
        selectByKey.bind([.int(key)])
        if try selectByKey.step() {
            let existingSidechain = selectByKey.int(0) != 0
            let existingTotal = selectByKey.int(1)
            if record.authoritative
                || Self.shouldReplace(record.isSidechain, total, existingSidechain, existingTotal) {
                try write(record, key: key)
            }
            return
        }
        if let messageId = record.messageId {
            selectByMessage.bind([.int(StableHash.of(messageId)), .int(record.isSidechain ? 1 : 0)])
            if try selectByMessage.step() {
                let otherKey = selectByMessage.int(0)
                let otherSidechain = selectByMessage.int(1) != 0
                let otherTotal = selectByMessage.int(2)
                if Self.shouldReplace(record.isSidechain, total, otherSidechain, otherTotal) {
                    try deleteEvent.bind([.int(otherKey)]).run()
                    try write(record, key: key)
                }
                return
            }
        }
        try write(record, key: key)
    }

    private static func shouldReplace(
        _ candidateSidechain: Bool, _ candidateTotal: Int64, _ existingSidechain: Bool, _ existingTotal: Int64
    ) -> Bool {
        if candidateSidechain != existingSidechain { return existingSidechain }
        return candidateTotal > existingTotal
    }

    private func write(_ record: LedgerRecord, key: Int64) throws {
        let e = record.event
        let dim = try dimID(DimKey(
            harness: e.harness, account: e.accountHome, session: e.sessionId,
            cwd: e.cwd ?? "", model: e.model, tier: e.serviceTier ?? ""))
        try insertEvent.bind([
            .int(key), record.messageId.map { .int(StableHash.of($0)) } ?? .null,
            .int(record.isSidechain ? 1 : 0), .int(dim), .double(e.timestamp.timeIntervalSince1970),
            .int(e.inputTokens), .int(e.outputTokens), .int(e.cacheReadTokens), .int(e.cacheWriteTokens),
            .int(e.cacheWrite1hTokens), .int(e.reasoningTokens),
            .int(e.isSessionTotal ? 1 : 0), .optionalDouble(e.costUSD),
        ]).run()
    }

    /// Drops the event under `key` when there is one. Used by session-total
    /// sources whose file now reports nothing, so stale spend clears.
    func delete(key: String) throws {
        try deleteEvent.bind([.int(StableHash.of(key))]).run()
    }

    /// Abandons the open transaction. Dimension ids inserted in it are gone
    /// from the database, so they must not stay cached either.
    func rollback() {
        try? db.execute("ROLLBACK")
        dimIDs.removeAll()
    }

    private func dimID(_ key: DimKey) throws -> Int64 {
        if let id = dimIDs[key] { return id }
        let values: [SQLiteValue?] = [
            .text(key.harness), .text(key.account), .text(key.session),
            .text(key.cwd), .text(key.model), .text(key.tier),
        ]
        selectDim.bind(values)
        let id: Int64
        if try selectDim.step() {
            id = selectDim.int(0)
        } else {
            try insertDim.bind(values).run()
            id = db.lastInsertRowID
        }
        dimIDs[key] = id
        return id
    }

    // MARK: - Cursors

    func cursor(for path: String) throws -> FileCursor? {
        let statement = try db.prepare("""
            SELECT inode, size, modified, offset, head_hash, head_length, tail_hash, tail_length, state, skipped
            FROM cursors WHERE path = ?
            """)
        statement.bind([.text(path)])
        guard try statement.step() else { return nil }
        return FileCursor(
            path: path, inode: UInt64(bitPattern: statement.int(0)), size: UInt64(statement.int(1)),
            modified: statement.double(2), offset: UInt64(statement.int(3)),
            headHash: statement.int(4), headLength: Int(statement.int(5)),
            tailHash: statement.int(6), tailLength: Int(statement.int(7)),
            state: statement.blob(8), skippedLines: Int(statement.int(9)))
    }

    func save(_ cursor: FileCursor) throws {
        try db.prepare("""
            INSERT OR REPLACE INTO cursors
            (path, inode, size, modified, offset, head_hash, head_length, tail_hash, tail_length, state, skipped)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """).bind([
                .text(cursor.path), .int(Int64(bitPattern: cursor.inode)), .int(Int64(cursor.size)),
                .double(cursor.modified), .int(Int64(cursor.offset)), .int(cursor.headHash),
                .int(cursor.headLength), .int(cursor.tailHash), .int(cursor.tailLength),
                cursor.state.map(SQLiteValue.blob) ?? .null, .int(cursor.skippedLines),
            ]).run()
    }

    /// Generic key-value state for sources that are not files (SQLite databases).
    func meta(_ key: String) throws -> String? {
        let statement = try db.prepare("SELECT value FROM meta WHERE key = ?")
        statement.bind([.text(key)])
        return try statement.step() ? statement.text(0) : nil
    }

    func setMeta(_ key: String, _ value: String) throws {
        try db.prepare("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)").bind([.text(key), .text(value)]).run()
    }
}

// MARK: - Queries

extension LedgerStore {
    enum Column: String {
        case harness, account, session, cwd, model
    }

    /// One group of rows that share their dimensions and every input the price
    /// depends on: model, tier, whether the log carried its own cost, and how
    /// many long-context thresholds the request's prompt crossed.
    struct PricedGroup {
        var dimensions: [Column: String]
        var model: String
        var tier: String?
        var hasLoggedCost: Bool
        var thresholdsCrossed: Int
        var tokens: TokenSums
        var loggedCost: Double
        var count: Int
        var first: Date
        var last: Date
        /// With `bucketSeconds`, the bucket's start.
        var bucket: Date?
    }

    struct TokenSums: Equatable {
        var input = 0, output = 0, cacheRead = 0, cacheWrite = 0, cacheWrite1h = 0, reasoning = 0
        var total: Int { input + output + cacheRead + cacheWrite + reasoning }
    }

    /// - Parameter bucketSeconds: also split each group into time buckets of
    ///   this many seconds since the epoch.
    func groups(in range: DateInterval, by columns: [Column], thresholds: [Int],
                bucketSeconds: Int? = nil) throws -> [PricedGroup] {
        let bucket = bucketSeconds.map { "CAST(e.ts / \($0) AS INTEGER)" }
        let dims = columns.map { "d.\($0.rawValue)" }
        let crossed = thresholds.isEmpty
            ? "0"
            : thresholds.map { "(e.session_total = 0 AND e.input + e.cache_read + e.cache_write > \($0))" }
                .joined(separator: " + ")
        let groupColumns = (dims + ["d.model", "d.tier", "logged", "crossed"] + (bucket.map { _ in ["bucket"] } ?? []))
            .joined(separator: ", ")
        let select = (dims + ["d.model", "d.tier", "(e.cost IS NOT NULL) AS logged", "\(crossed) AS crossed"]
            + (bucket.map { ["\($0) AS bucket"] } ?? []))
            .joined(separator: ", ")
        let statement = try db.prepare("""
            SELECT \(select),
                SUM(e.input), SUM(e.output), SUM(e.cache_read), SUM(e.cache_write), SUM(e.cache_write_1h),
                SUM(e.reasoning), TOTAL(e.cost), COUNT(*), MIN(e.ts), MAX(e.ts)
            FROM events e JOIN dims d ON d.id = e.dim
            WHERE e.ts >= ? AND e.ts < ?
            GROUP BY \(groupColumns)
            """)
        statement.bind([.double(range.start.timeIntervalSince1970), .double(range.end.timeIntervalSince1970)])
        var result: [PricedGroup] = []
        while try statement.step() {
            var dimensions: [Column: String] = [:]
            for (index, column) in columns.enumerated() {
                dimensions[column] = statement.text(Int32(index)) ?? ""
            }
            var i = Int32(columns.count)
            func next() -> Int32 { defer { i += 1 }; return i }
            let model = statement.text(next()) ?? ""
            let tier = statement.text(next()) ?? ""
            let logged = statement.int(next()) != 0
            let crossedCount = Int(statement.int(next()))
            let bucketStart = bucketSeconds.map { size in
                Date(timeIntervalSince1970: Double(statement.int(next())) * Double(size))
            }
            let sums = TokenSums(
                input: Int(statement.int(next())), output: Int(statement.int(next())),
                cacheRead: Int(statement.int(next())), cacheWrite: Int(statement.int(next())),
                cacheWrite1h: Int(statement.int(next())), reasoning: Int(statement.int(next())))
            let cost = statement.double(next())
            let count = Int(statement.int(next()))
            let first = Date(timeIntervalSince1970: statement.double(next()))
            let last = Date(timeIntervalSince1970: statement.double(next()))
            result.append(PricedGroup(
                dimensions: dimensions, model: model, tier: tier.isEmpty ? nil : tier,
                hasLoggedCost: logged, thresholdsCrossed: crossedCount, tokens: sums,
                loggedCost: cost, count: count, first: first, last: last, bucket: bucketStart))
        }
        return result
    }

    func events(in range: DateInterval) throws -> [UsageEvent] {
        let statement = try db.prepare("""
            SELECT d.harness, d.account, d.session, d.cwd, d.model, d.tier, e.ts,
                e.input, e.output, e.cache_read, e.cache_write, e.cache_write_1h, e.reasoning, e.cost
            FROM events e JOIN dims d ON d.id = e.dim
            WHERE e.ts >= ? AND e.ts < ? ORDER BY e.ts, e.key
            """)
        statement.bind([.double(range.start.timeIntervalSince1970), .double(range.end.timeIntervalSince1970)])
        var events: [UsageEvent] = []
        while try statement.step() {
            let cwd = statement.text(3) ?? ""
            let tier = statement.text(5) ?? ""
            events.append(UsageEvent(
                harness: statement.text(0) ?? "", accountHome: statement.text(1) ?? "",
                sessionId: statement.text(2) ?? "", cwd: cwd.isEmpty ? nil : cwd,
                model: statement.text(4) ?? "", timestamp: Date(timeIntervalSince1970: statement.double(6)),
                inputTokens: Int(statement.int(7)), outputTokens: Int(statement.int(8)),
                cacheReadTokens: Int(statement.int(9)), cacheWriteTokens: Int(statement.int(10)),
                cacheWrite1hTokens: Int(statement.int(11)), reasoningTokens: Int(statement.int(12)),
                costUSD: statement.isNull(13) ? nil : statement.double(13),
                serviceTier: tier.isEmpty ? nil : tier))
        }
        return events
    }
}
