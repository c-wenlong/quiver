import Foundation

/// One stretch of consecutive work in a session: the first to the last
/// response where no two consecutive responses are more than `gap` apart.
/// It is the Calendar page's event; the session it came from is what a
/// click opens.
public struct ActivityBlock: Sendable, Equatable, Identifiable {
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

    public var id: String {
        "\(harness)|\(accountHome)|\(sessionId)|\(start.timeIntervalSince1970)"
    }

    public init(harness: String, accountHome: String, sessionId: String,
                start: Date, end: Date, responses: Int) {
        self.harness = harness
        self.accountHome = accountHome
        self.sessionId = sessionId
        self.start = start
        self.end = end
        self.responses = responses
    }
}

/// Anything that can say when a harness was actually working. The ledger
/// answers from logged responses; providers for harnesses the ledger does
/// not read are added the same way.
public protocol ActivityProviding: Sendable {
    /// Every block overlapping `range`, whole: a block that straddles an
    /// edge keeps its real start and end. `gap` is the silence, in seconds,
    /// that splits one session's responses into separate blocks.
    func activityBlocks(in range: DateInterval, gap: TimeInterval) async -> [ActivityBlock]
}

extension Ledger: ActivityProviding {
    /// Blocks over the ledger's response rows. `events` only reaches back
    /// for logs the app still has, but rows the scanner already committed
    /// answer for as long as they persist.
    public func activityBlocks(in range: DateInterval, gap: TimeInterval) -> [ActivityBlock] {
        (try? store.activityBlocks(in: range, gap: gap)) ?? []
    }
}

extension LedgerStore {
    /// SQL-side, so a week's worth of responses never leaves the database:
    /// LAG marks each response against the one before it in its session, a
    /// running SUM numbers the blocks, and the aggregate keeps first, last
    /// and the response count. Events are pulled `gap` past each edge so a
    /// block straddling the range keeps its real ends; the overlap check
    /// below then drops the ones that never touch it.
    func activityBlocks(in range: DateInterval, gap: TimeInterval) throws -> [ActivityBlock] {
        let lo = range.start.timeIntervalSince1970 - gap
        let hi = range.end.timeIntervalSince1970 + gap
        let statement = try db.prepare("""
            WITH ordered AS (
                SELECT d.harness, d.account, d.session, e.ts,
                       LAG(e.ts) OVER (
                           PARTITION BY d.harness, d.account, d.session
                           ORDER BY e.ts, e.key
                       ) AS prev
                FROM events e JOIN dims d ON d.id = e.dim
                WHERE e.ts >= ? AND e.ts < ?
            ), numbered AS (
                SELECT harness, account, session, ts,
                       SUM(CASE WHEN prev IS NULL OR ts - prev > ? THEN 1 ELSE 0 END)
                           OVER (PARTITION BY harness, account, session
                                 ORDER BY ts) AS block
                FROM ordered
            )
            SELECT harness, account, session, MIN(ts), MAX(ts), COUNT(*)
            FROM numbered
            GROUP BY harness, account, session, block
            ORDER BY MIN(ts)
            """)
        statement.bind([.double(lo), .double(hi), .double(gap)])
        var blocks: [ActivityBlock] = []
        while try statement.step() {
            let start = statement.double(3), end = statement.double(4)
            guard end >= range.start.timeIntervalSince1970,
                  start < range.end.timeIntervalSince1970 else { continue }
            blocks.append(ActivityBlock(
                harness: statement.text(0) ?? "", accountHome: statement.text(1) ?? "",
                sessionId: statement.text(2) ?? "",
                start: Date(timeIntervalSince1970: start),
                end: Date(timeIntervalSince1970: end),
                responses: Int(statement.int(5))))
        }
        return blocks
    }
}
