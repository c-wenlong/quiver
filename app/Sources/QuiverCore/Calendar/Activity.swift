import Foundation

/// One stretch of consecutive work in a session: the first to the last
/// response where no two consecutive responses are more than `gap` apart.
/// It is the Calendar page's event; the session it came from is what a
/// click opens.
public struct ActivityBlock: Sendable, Equatable, Identifiable, Codable {
    /// "claude", "codex", "opencode" or "devin".
    public var harness: String
    /// The config root the responses were read from, so two accounts of
    /// the same harness make two blocks.
    public var accountHome: String
    public var sessionId: String
    /// The first response of the block.
    public var start: Date
    /// The last response of the block.
    public var end: Date
    /// How many responses the block holds.
    public var responses: Int
    /// The session's working directory, when the ledger knows one: the
    /// folder a block with no session title gets named by.
    public var cwd: String?

    public var id: String {
        "\(harness)|\(accountHome)|\(sessionId)|\(start.timeIntervalSince1970)"
    }

    public init(harness: String, accountHome: String, sessionId: String,
                start: Date, end: Date, responses: Int, cwd: String? = nil) {
        self.harness = harness
        self.accountHome = accountHome
        self.sessionId = sessionId
        self.start = start
        self.end = end
        self.responses = responses
        self.cwd = cwd
    }
}

/// Anything that can say when a harness was actually working. The ledger
/// answers from logged responses; providers for harnesses the ledger does
/// not read are added the same way.
public protocol ActivityProviding: Sendable {
    /// Every block overlapping `range`, whole: a block that straddles an
    /// edge keeps its real start and end. `gap` is the silence, in seconds,
    /// that splits one session's responses into separate blocks.
    /// Throws on a read failure; the model keeps the provider's last
    /// good blocks and surfaces the error instead of an empty week.
    /// `force` asks a cached source to refresh instead of serving its
    /// throttle window.
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) async throws -> [ActivityBlock]
}

extension Ledger: ActivityProviding {
    /// Blocks over the ledger's response rows. `events` only reaches back
    /// for logs the app still has, but rows the scanner already committed
    /// answer for as long as they persist.
    ///
    /// Nonisolated on its own read-only connection: a WAL reader never
    /// waits behind a scan running on the actor's write connection.
    /// `force` is a no-op; the ledger answers live.
    public nonisolated func activityBlocks(in range: DateInterval, gap: TimeInterval,
                                           force: Bool) throws -> [ActivityBlock] {
        try activityConnection.withLock { connection throws -> [ActivityBlock] in
            if connection == nil {
                let path = activityURL.path
                    .addingPercentEncoding(withAllowedCharacters: .urlPathAllowed)
                    ?? activityURL.path
                connection = try SQLiteDatabase(readOnlyURI: "file:\(path)?mode=ro")
            }
            return try connection!.activityBlocks(in: range, gap: gap)
        }
    }
}

extension LedgerStore {
    /// Blocks over this store's write connection; the calendar reads
    /// through Ledger's own read-only one. Kept for tests that build a
    /// store directly.
    func activityBlocks(in range: DateInterval, gap: TimeInterval) throws -> [ActivityBlock] {
        try db.activityBlocks(in: range, gap: gap)
    }
}

extension SQLiteDatabase {
    /// SQL-side, so a week's worth of responses never leaves the database:
    /// LAG marks each response against the one before it in its dim (an
    /// integer key, much cheaper to partition than the joined text), a
    /// running SUM numbers the blocks, and the aggregate joins `dims`
    /// only once per block. Per-dim blocks are then merged in Swift:
    /// consecutive blocks of one session join while the next starts
    /// within `gap` of the previous end, matching the old rule that
    /// split only on a quiet stretch longer than `gap`. Events are
    /// pulled a day past each edge so a block straddling the range keeps
    /// its real ends; the overlap check then drops the rest.
    static let edgeLookout: TimeInterval = 24 * 3600
    func activityBlocks(in range: DateInterval, gap: TimeInterval) throws -> [ActivityBlock] {
        let lo = range.start.timeIntervalSince1970 - Self.edgeLookout
        let hi = range.end.timeIntervalSince1970 + Self.edgeLookout
        let statement = try prepare("""
            WITH ordered AS (
                SELECT e.dim, e.ts,
                       LAG(e.ts) OVER (
                           PARTITION BY e.dim ORDER BY e.ts, e.key
                       ) AS prev
                FROM events e
                WHERE e.ts >= ? AND e.ts < ?
            ), numbered AS (
                SELECT dim, ts,
                       SUM(CASE WHEN prev IS NULL OR ts - prev > ? THEN 1 ELSE 0 END)
                           OVER (PARTITION BY dim ORDER BY ts) AS block
                FROM ordered
            ), blocks AS (
                SELECT dim, MIN(ts) AS start, MAX(ts) AS end, COUNT(*) AS n
                FROM numbered GROUP BY dim, block
            )
            SELECT d.harness, d.account, d.session, b.start, b.end, b.n, d.cwd
            FROM blocks b JOIN dims d ON d.id = b.dim
            ORDER BY d.harness, d.account, d.session, b.start
            """)
        statement.bind([.double(lo), .double(hi), .double(gap)])
        var merged: [ActivityBlock] = []
        var current: ActivityBlock?
        func flush() {
            guard let block = current else { return }
            current = nil
            guard block.end >= range.start, block.start < range.end else { return }
            merged.append(block)
        }
        while try statement.step() {
            var piece = ActivityBlock(
                harness: statement.text(0) ?? "", accountHome: statement.text(1) ?? "",
                sessionId: statement.text(2) ?? "",
                start: Date(timeIntervalSince1970: statement.double(3)),
                end: Date(timeIntervalSince1970: statement.double(4)),
                responses: Int(statement.int(5)),
                cwd: statement.text(6))
            if let previous = current,
               previous.harness == piece.harness,
               previous.accountHome == piece.accountHome,
               previous.sessionId == piece.sessionId,
               piece.start.timeIntervalSince(previous.end) <= gap {
                // Same session, close enough to touch: fold in.
                piece = ActivityBlock(
                    harness: piece.harness, accountHome: piece.accountHome,
                    sessionId: piece.sessionId,
                    start: min(previous.start, piece.start),
                    end: max(previous.end, piece.end),
                    responses: previous.responses + piece.responses,
                    cwd: piece.cwd ?? previous.cwd)
            } else {
                flush()
            }
            current = piece
        }
        flush()
        return merged
    }
}
