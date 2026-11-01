import Foundation

/// One stretch of consecutive work in a project: the first to the last
/// response where no two consecutive responses are more than `gap` apart.
/// It is the Calendar page's event; the sessions it came from are what a
/// click opens.
public struct ActivityBlock: Sendable, Equatable, Identifiable, Codable {
    /// One session's share of a project block: its own first and last
    /// response inside the block and how many it added.
    public struct SessionRef: Sendable, Equatable, Codable {
        public var sessionId: String
        /// The session's working directory, when the source knows one.
        public var cwd: String?
        public var start: Date
        public var end: Date
        public var responses: Int

        public init(sessionId: String, cwd: String? = nil, start: Date,
                    end: Date, responses: Int) {
            self.sessionId = sessionId
            self.cwd = cwd
            self.start = start
            self.end = end
            self.responses = responses
        }
    }

    /// "claude", "codex", "opencode" or "devin".
    public var harness: String
    /// The config root the responses were read from, so two accounts of
    /// the same harness make two blocks.
    public var accountHome: String
    /// What the block groups and identifies by: the repository's root
    /// path, so same-named folders of different repos stay apart. "" for
    /// no folder and for providers' ungrouped blocks.
    public var projectKey: String
    /// The project the sessions ran in, for display. Providers leave it
    /// empty and the grouping step resolves it from each session's
    /// folder.
    public var project: String
    /// The sessions inside the block, earliest first.
    public var sessions: [SessionRef]
    /// The first response of the block.
    public var start: Date
    /// The last response of the block.
    public var end: Date
    /// How many responses the block holds.
    public var responses: Int

    public var id: String {
        "\(harness)|\(accountHome)|\(projectKey.isEmpty ? project : projectKey)|\(start.timeIntervalSince1970)"
    }

    public init(harness: String, accountHome: String, project: String,
                sessions: [SessionRef], start: Date, end: Date, responses: Int,
                projectKey: String = "") {
        self.harness = harness
        self.accountHome = accountHome
        self.projectKey = projectKey
        self.project = project
        self.sessions = sessions
        self.start = start
        self.end = end
        self.responses = responses
    }

    /// The one-session form every provider emits; the grouping step
    /// resolves `project` and merges these into project blocks.
    public init(harness: String, accountHome: String, sessionId: String,
                start: Date, end: Date, responses: Int, cwd: String? = nil) {
        self.init(harness: harness, accountHome: accountHome, project: "",
                  sessions: [.init(sessionId: sessionId, cwd: cwd,
                                   start: start, end: end, responses: responses)],
                  start: start, end: end, responses: responses)
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

extension Ledger {
    /// Harnesses whose ledger events drive the calendar. Pi and Droid have
    /// their own file activity sources and Cline has none, so letting the
    /// ledger answer for them would draw the same sessions twice (or draw
    /// Cline where nothing is wanted). Grok Bot comes from Cursor's usage
    /// feed and has no other source; plain `cursor` events stay out because
    /// CursorAppActivity already reads the editor's own state.
    static let calendarHarnesses: Set<String> = ["claude", "codex", "opencode", "devin", "grok-bot"]

    /// The trivial-session drop for file-read sessions (Pi and Droid):
    /// their logs reach the calendar without going through the block
    /// query, so the same whole-history `SessionFilter` rule runs here
    /// over the ledger's per-session stats, under the same setting.
    /// A session the ledger has no rows for stays: absent rows prove
    /// nothing about a file's responses.
    ///
    /// The whole-history read takes seconds on a large ledger, so it runs
    /// on the pooled readers off the actor, like the other figure queries:
    /// on the actor it waited between scanned files and held up the scan.
    @concurrent
    public nonisolated func filterTrivialActivitySessions(
        _ sessions: [ActivityFileIndex.IndexedSession], harness: String
    ) async -> [ActivityFileIndex.IndexedSession] {
        guard !sessions.isEmpty, trivialGate.withLock({ $0 }) else { return sessions }
        struct Key: Hashable { var account, session: String }
        var usage: [Key: SessionUsage] = [:]
        for row in sessionUsage(in: DateInterval(start: Date(timeIntervalSince1970: 0),
                                                 end: .distantFuture),
                                harness: harness) {
            let key = Key(account: row.accountHome, session: row.sessionId)
            if var existing = usage[key] {
                existing.totals.add(row.totals)
                existing.firstTimestamp = min(existing.firstTimestamp, row.firstTimestamp)
                existing.lastTimestamp = max(existing.lastTimestamp, row.lastTimestamp)
                usage[key] = existing
            } else {
                usage[key] = row
            }
        }
        return sessions.filter { session in
            guard let totals = usage[Key(account: session.accountHome,
                                         session: session.sessionId)]
            else { return true }
            return !SessionFilter.isTrivial(usage: totals)
        }
    }
}

/// Models a harness calls for its own bookkeeping, not user work: Devin
/// runs `summarizer` and `compactor` calls in their own sessions, and a
/// session containing nothing else must not draw a calendar block.
/// Helper events inside a real session still count — they are part of
/// that work.
public enum HelperModels {
    public static let all: Set<String> = ["summarizer", "compactor"]
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
            let hideTrivial = trivialGate.withLock { $0 }
            return try connection!.activityBlocks(in: range, gap: gap, hideTrivial: hideTrivial)
                .filter { Self.calendarHarnesses.contains($0.harness) }
        }
    }
}

extension LedgerStore {
    /// Blocks over this store's write connection; the calendar reads
    /// through Ledger's own read-only one. Kept for tests that build a
    /// store directly.
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        hideTrivial: Bool = true) throws -> [ActivityBlock] {
        try db.activityBlocks(in: range, gap: gap, hideTrivial: hideTrivial)
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
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        hideTrivial: Bool = true) throws -> [ActivityBlock] {
        let lo = range.start.timeIntervalSince1970 - Self.edgeLookout
        let hi = range.end.timeIntervalSince1970 + Self.edgeLookout
        // Sessions whose every event is a helper call (Devin's
        // summarizer/compactor) produce no blocks at all. "Every" means
        // the session's whole stored history, not just this range: a
        // real response earlier keeps later helper events on the
        // calendar as part of that work. `hideTrivial` drops accidental
        // sessions the same way, judged on the session's whole stored
        // history (SessionFilter): zero tokens, zero responses, or a
        // one-shot probe — except for harnesses without per-reply
        // detail, where only emptiness counts.
        let helpers = HelperModels.all.sorted()
        // The candidate sessions first: distinct harness/account/session
        // triples with an event in range and a non-helper event anywhere
        // in their stored history.
        let candidates = try prepare("""
            SELECT DISTINCT d.harness, d.account, d.session
            FROM events e JOIN dims d ON d.id = e.dim
            WHERE e.ts >= ? AND e.ts < ?
              AND EXISTS (
                  SELECT 1 FROM events e2 JOIN dims d2 ON d2.id = e2.dim
                  WHERE d2.harness = d.harness AND d2.account = d.account
                        AND d2.session = d.session
                        AND d2.model NOT IN (\(helpers.map { _ in "?" }.joined(separator: ",")))
              )
            """)
        candidates.bind([SQLiteValue.double(lo), .double(hi)]
                        + helpers.map { SQLiteValue.text($0) })
        var candidateKeys: [[String]] = []
        while try candidates.step() {
            candidateKeys.append([candidates.text(0) ?? "", candidates.text(1) ?? "",
                                  candidates.text(2) ?? ""])
        }
        // One grouped pass computes every candidate's whole-history
        // totals once, restricted through a temp table of the candidates;
        // `events_dim` keeps each dim's events an index lookup away.
        var real: Set<String> = []
        if !candidateKeys.isEmpty {
            try execute("""
                CREATE TEMP TABLE IF NOT EXISTS calendar_candidates (
                    harness TEXT, account TEXT, session TEXT)
                """)
            try execute("DELETE FROM calendar_candidates")
            let insert = try prepare(
                "INSERT INTO calendar_candidates VALUES (?, ?, ?)")
            for key in candidateKeys {
                try insert.bind([.text(key[0]), .text(key[1]), .text(key[2])]).run()
            }
            let stats = try prepare("""
                SELECT d.harness, d.account, d.session, COUNT(*),
                       SUM(e.input + e.output + e.cache_read + e.cache_write + e.reasoning),
                       MAX(e.ts) - MIN(e.ts), SUM(e.output + e.reasoning)
                FROM dims d JOIN events e ON e.dim = d.id
                WHERE EXISTS (
                    SELECT 1 FROM calendar_candidates c
                    WHERE c.harness = d.harness AND c.account = d.account
                          AND c.session = d.session)
                GROUP BY d.harness, d.account, d.session
                """)
            while try stats.step() {
                let harness = stats.text(0) ?? ""
                let key = harness + "\u{0}" + (stats.text(1) ?? "")
                    + "\u{0}" + (stats.text(2) ?? "")
                let count = stats.int(3)
                let total = stats.int(4)
                let span = stats.double(5)
                let outAndReasoning = stats.int(6)
                let trivial = count == 0 || total == 0
                    || (!SessionFilter.noReplyDetailHarnesses.contains(harness)
                        && count <= SessionFilter.maxProbeResponses
                        && span < SessionFilter.maxProbeSpan
                        && outAndReasoning < SessionFilter.maxProbeOutputTokens)
                if !hideTrivial || !trivial { real.insert(key) }
            }
            try execute("DELETE FROM calendar_candidates")
        }
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
        // The current block's responses per folder: the block takes the
        // folder most of its responses came from (SessionFolders).
        var votes: [(cwd: String, events: Int)] = []
        func vote(_ cwd: String?, _ responses: Int) {
            guard let cwd, !cwd.isEmpty else { return }
            if let index = votes.firstIndex(where: { $0.cwd == cwd }) {
                votes[index].events += responses
            } else {
                votes.append((cwd, responses))
            }
        }
        func flush() {
            guard var block = current else { return }
            current = nil
            if let cwd = SessionFolders.pick(votes), !block.sessions.isEmpty {
                block.sessions[0].cwd = cwd
            }
            votes = []
            guard block.end >= range.start, block.start < range.end else { return }
            merged.append(block)
        }
        func sessionKey(of block: ActivityBlock) -> String {
            "\(block.harness)\u{0}\(block.accountHome)\u{0}"
                + (block.sessions.first?.sessionId ?? "")
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
               previous.sessions.first?.sessionId == piece.sessions.first?.sessionId,
               piece.start.timeIntervalSince(previous.end) <= gap {
                // Same session, close enough to touch: fold in.
                piece = ActivityBlock(
                    harness: piece.harness, accountHome: piece.accountHome,
                    sessionId: piece.sessions.first?.sessionId ?? "",
                    start: min(previous.start, piece.start),
                    end: max(previous.end, piece.end),
                    responses: previous.responses + piece.responses)
            } else {
                flush()
            }
            vote(statement.text(6), Int(statement.int(5)))
            current = piece
        }
        flush()
        return merged.filter { real.contains(sessionKey(of: $0)) }
    }
}
