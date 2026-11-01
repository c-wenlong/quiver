import Foundation
import os

/// One session's best matching message, plus the snippet to show. Mirrors
/// `SearchHit` in `src/swe/sessions/search_index.py`, so `swe session
/// --search` and the app answer the same query the same way.
public struct SearchHit: Equatable, Sendable {
    /// The `sessions` row id — the join key inside the index, useless outside.
    public var sessionRowID: Int64
    /// The session id the indexer recorded (`sessions.session_id`).
    public var sessionID: String
    /// The harness id (`sessions.agent`).
    public var agent: String
    public var title: String
    public var cwd: String
    /// The transcript the rows came from; its stem is an alternate id form
    /// (`matchKeys` covers it, since pi sessions are keyed by path).
    public var filePath: String
    public var updatedAt: String
    /// `-bm25(...)`: higher is a better match.
    public var score: Double
    /// The matching row's role: user, assistant or tool.
    public var role: String
    /// The matching row's timestamp, verbatim.
    public var ts: String
    /// The match's context as plain text, capped at `SearchSnippet.maxChars`.
    public var snippet: String
    /// The same text with U+E000/U+E001 around each matched term — the
    /// private-use marks the CLI's `marked_snippet` carries too. Empty when
    /// the marked and plain renderings can't be reconciled (see
    /// `SearchSnippet.render`).
    public var markedSnippet: String
    /// `markedSnippet` resolved for display: (text, matched) runs, whitespace
    /// collapsed. The parts come from the mark-validated rendering, so a
    /// private-use character the transcript itself wrote stays text here.
    public var snippetParts: [SearchSnippetPart]

    public init(sessionRowID: Int64, sessionID: String, agent: String, title: String,
                cwd: String, filePath: String, updatedAt: String, score: Double,
                role: String, ts: String, snippet: String, markedSnippet: String,
                snippetParts: [SearchSnippetPart]) {
        self.sessionRowID = sessionRowID
        self.sessionID = sessionID
        self.agent = agent
        self.title = title
        self.cwd = cwd
        self.filePath = filePath
        self.updatedAt = updatedAt
        self.score = score
        self.role = role
        self.ts = ts
        self.snippet = snippet
        self.markedSnippet = markedSnippet
        self.snippetParts = snippetParts
    }
}

/// The read side of the FTS5 transcript index `SearchStore` writes, adapted
/// from Orca's `session-search-retrieval.ts`/`session-search-snippet.ts` and
/// kept in step with `src/swe/sessions/search_index.py`.
///
/// The connection opens lazily, read-only (`file:...?mode=ro`), and every
/// read is a single short statement: no transaction or statement cursor is
/// ever held across an await, which is what pinned Orca's WAL open (a held
/// read snapshot grew it 10 MB → 266 MB). A failed open is retried on the
/// next call, so an index created or rebuilt while the app runs still gets
/// used; a statement error drops the cached connection so the next call
/// re-probes (a rebuild can unlink the file under a held connection).
///
/// Calls serialize on one lock — the wrapper's connection is `NOMUTEX` —
/// and are cheap enough that the Sessions page runs them off a detached
/// task.
public final class SearchEngine: @unchecked Sendable {
    public let directory: URL
    public var url: URL { directory.appending(path: SearchLocation.fileName) }

    /// The lazily opened read-only connection; nil until probed or after a
    /// failed probe (which is retried on the next call rather than cached).
    private let lock = OSAllocatedUnfairLock<Held?>(initialState: nil)

    /// A connection and the file it opened. "Clear search data" deletes
    /// the file and makes a new one; a held connection keeps reading the
    /// deleted file (an unlinked inode reads without error), so each call
    /// checks the path still names the file it opened.
    private struct Held: @unchecked Sendable {
        var db: SQLiteDatabase
        var file: FileID
    }

    private struct FileID: Equatable {
        var device: dev_t
        var inode: ino_t
    }

    private static func fileID(_ url: URL) -> FileID? {
        var info = stat()
        guard stat(url.path, &info) == 0 else { return nil }
        return FileID(device: info.st_dev, inode: info.st_ino)
    }

    /// - Parameter directory: where `search.sqlite` lives; production uses
    ///   `SearchLocation.directory(under:)` (`~/.swe/data/search/`),
    ///   tests a temp dir.
    public init(directory: URL) {
        self.directory = directory
    }

    /// Whether a usable index exists: the file opens read-only,
    /// `meta.schema_version` is `SearchSchema.version`, and `messages_fts`
    /// answers. Any failure (missing file, wrong version, no FTS5) means
    /// unavailable; nothing is created or written here.
    public var available: Bool {
        lock.withLock { connection(&$0) != nil }
    }

    /// The held connection, or a fresh probe. Nil means this call sees no
    /// usable index — and stays uncached so a later call can succeed.
    private func connection(_ held: inout Held?) -> SQLiteDatabase? {
        let current = Self.fileID(url)
        if let open = held {
            if open.file == current { return open.db }
            held = nil  // deleted or replaced since it opened
        }
        guard let current,
              let opened = try? SQLiteDatabase(readOnlyURI: Self.readOnlyURI(for: url)),
              (try? Self.checkUsable(opened)) == true
        else { return nil }
        held = Held(db: opened, file: current)
        return opened
    }

    /// The version gate plus a probe that `messages_fts` exists and answers
    /// — the two things every query relies on.
    private static func checkUsable(_ db: SQLiteDatabase) throws -> Bool {
        do {
            let meta = try db.prepare("SELECT value FROM meta WHERE key = 'schema_version'")
            guard try meta.step(),
                  meta.text(0)?.trimmingCharacters(in: .whitespaces) == "\(SearchSchema.version)"
            else { return false }
        }
        let probe = try db.prepare("SELECT rowid FROM messages_fts LIMIT 1")
        _ = try probe.step()
        return true
    }

    /// `file:` URI form for `?mode=ro`, like the ledger's read connections.
    private static func readOnlyURI(for url: URL) -> String {
        let path = url.path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? url.path
        return "file:\(path)?mode=ro"
    }

    // MARK: - Query planning

    /// Search text split the way `unicode61 tokenchars '_.-/+'` tokens run:
    /// letters, numbers, marks and private-use plus `_ . - / +`, so
    /// `foo-bar`, `Ledger.swift` and `C++` each stay one term — the same
    /// split `search_index.py::_terms` makes (`[\w./+-]+`) and Orca pinned
    /// against `fts5vocab`.
    private static let termPattern = try! NSRegularExpression(
        pattern: #"[\p{L}\p{N}\p{M}\p{Co}_.+/\-]+"#)

    /// The query's terms as the index would tokenize them. "—" alone is a
    /// separator and never a term.
    public static func terms(_ query: String) -> [String] {
        termPattern.matches(in: query, range: NSRange(query.startIndex..., in: query))
            .compactMap { Range($0.range, in: query).map { String(query[$0]) } }
    }

    /// Escape a term's quotes: unquoted, `foo-bar` reads as `foo` minus
    /// `bar` and `C++` is a syntax error, so every term goes into the MATCH
    /// expression inside double quotes.
    private static func escape(_ term: String) -> String {
        term.replacingOccurrences(of: "\"", with: "\"\"")
    }

    /// MATCH expressions, strictest first: one quoted string holding every
    /// term is the phrase query (`"t1 t2"`, not `"t1" "t2"` — that is only
    /// an implicit AND), then every term ANDed, then ORed. One term makes
    /// the phrase the only rung.
    static func ladder(_ terms: [String]) -> [String] {
        let escaped = terms.map(escape)
        var variants = ["\"" + escaped.joined(separator: " ") + "\""]
        if escaped.count > 1 {
            let quoted = escaped.map { "\"\($0)\"" }
            variants.append(quoted.joined(separator: " AND "))
            variants.append(quoted.joined(separator: " OR "))
        }
        return variants
    }

    /// One row per session, the best matching message kept. `max(score)` is
    /// the only aggregate, so SQLite's bare-column rule makes rowid, role
    /// and ts come from the highest-scoring row of the group; the session
    /// fields are functionally dependent on the group key, so which row
    /// supplies them doesn't matter. `bm25` takes the table name, never an
    /// alias. Orca measured the 3/2/1/1 weights (user/assistant/tool/
    /// identifiers); the CLI uses the same, so app and CLI agree. The join
    /// to `sessions` is what keeps orphaned message rows — a replaced file's
    /// old generation, waiting on the drain — from answering.
    private static let matchSQL = """
        WITH matched AS MATERIALIZED (
            SELECT messages_fts.rowid AS rowid,
                   -bm25(messages_fts, 3.0, 2.0, 1.0, 1.0) AS score,
                   m.session_row_id AS session_row_id,
                   m.role AS role,
                   m.ts AS ts,
                   s.session_id AS session_id,
                   s.agent AS agent,
                   s.title AS title,
                   s.cwd AS cwd,
                   s.file_path AS file_path,
                   s.updated_at AS updated_at
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            JOIN sessions s ON s.id = m.session_row_id
            WHERE messages_fts MATCH ?{AGENT_CLAUSE}
        )
        SELECT rowid, max(score) AS score, session_row_id, role, ts,
               session_id, agent, title, cwd, file_path, updated_at
        FROM matched
        GROUP BY session_row_id
        ORDER BY score DESC
        LIMIT ?
        """

    /// Plain and marked renderings of the same snippet window. The `IN
    /// (SELECT ?)` is deliberate: next to MATCH the FTS5 planner silently
    /// ignores a bound `rowid = ?` and `snippet()` runs against the wrong
    /// row (an Orca gotcha the CLI keeps too).
    private static let snippetSQL = """
        SELECT snippet(messages_fts, -1, '', '', '…', 12),
               snippet(messages_fts, -1, char(0xE000), char(0xE001), '…', 12)
        FROM messages_fts
        WHERE messages_fts MATCH ? AND messages_fts.rowid IN (SELECT ?)
        """

    /// BM25-ranked sessions whose transcript text matches `query`.
    ///
    /// The ladder tries an exact phrase first, then all terms ANDed, then
    /// ORed, stopping at the first rung with hits — "ledger cursor" prefers
    /// sessions where the words sit together but still answers when they
    /// only appear separately. `agents` filters `sessions.agent` (the
    /// harness id). An unavailable index or an empty plan is an empty
    /// result, not an error.
    public func search(query: String, limit: Int = 50, agents: [String]? = nil) throws -> [SearchHit] {
        try lock.withLock { db throws -> [SearchHit] in
            guard let conn = connection(&db) else { return [] }
            let terms = Self.terms(query)
            guard !terms.isEmpty else { return [] }
            let limit = max(1, limit)
            let agents = agents?.filter { !$0.isEmpty }
            do {
                for expression in Self.ladder(terms) {
                    let rows = try match(conn, expression: expression, limit: limit, agents: agents)
                    guard !rows.isEmpty else { continue }
                    return hits(from: rows, expression: expression, db: conn)
                }
                return []
            } catch {
                db = nil
                throw error
            }
        }
    }

    private struct MatchedRow {
        var rowid: Int64
        var score: Double
        var sessionRowID: Int64
        var role: String
        var ts: String
        var sessionID: String
        var agent: String
        var title: String
        var cwd: String
        var filePath: String
        var updatedAt: String
    }

    /// One rung of the ladder: the grouped match query.
    private func match(_ db: SQLiteDatabase, expression: String, limit: Int,
                       agents: [String]?) throws -> [MatchedRow] {
        var clause = ""
        if let agents, !agents.isEmpty {
            clause = " AND s.agent IN (" + agents.map { _ in "?" }.joined(separator: ",") + ")"
        }
        let statement = try db.prepare(
            Self.matchSQL.replacingOccurrences(of: "{AGENT_CLAUSE}", with: clause))
        statement.bind([.text(expression)] + (agents ?? []).map(SQLiteValue.text) + [.int(limit)])
        var rows: [MatchedRow] = []
        while try statement.step() {
            rows.append(MatchedRow(
                rowid: statement.int(0), score: statement.double(1),
                sessionRowID: statement.int(2), role: statement.text(3) ?? "",
                ts: statement.text(4) ?? "", sessionID: statement.text(5) ?? "",
                agent: statement.text(6) ?? "", title: statement.text(7) ?? "",
                cwd: statement.text(8) ?? "", filePath: statement.text(9) ?? "",
                updatedAt: statement.text(10) ?? ""))
        }
        return rows
    }

    /// The surviving rung's rows as hits, one snippet query each — bounded
    /// by `limit`, so a page of 50 costs 50 short statements.
    private func hits(from rows: [MatchedRow], expression: String, db: SQLiteDatabase) -> [SearchHit] {
        var seen: Set<String> = []
        var hits: [SearchHit] = []
        for row in rows {
            // Two `sessions` rows can share one `session_id` (an id reuse);
            // the first, best-scored one stands.
            guard seen.insert(row.sessionID).inserted else { continue }
            let snippet = snippet(db, expression: expression, rowid: row.rowid)
            hits.append(SearchHit(
                sessionRowID: row.sessionRowID, sessionID: row.sessionID,
                agent: row.agent, title: row.title, cwd: row.cwd,
                filePath: row.filePath, updatedAt: row.updatedAt,
                score: row.score, role: row.role, ts: row.ts,
                snippet: snippet.text, markedSnippet: snippet.marked,
                snippetParts: snippet.parts))
        }
        return hits
    }

    /// The match's context in its row, rendered through `SearchSnippet`
    /// (mark-validated, capped, marks resolved into parts). A snippet that
    /// fails to query or validate degrades to empty text, never an error —
    /// the hit already proved the match.
    private func snippet(_ db: SQLiteDatabase, expression: String, rowid: Int64)
        -> (text: String, marked: String, parts: [SearchSnippetPart]) {
        do {
            let statement = try db.prepare(Self.snippetSQL)
            statement.bind([.text(expression), .int(rowid)])
            guard try statement.step() else { return ("", "", []) }
            return SearchSnippet.render(plain: statement.text(0) ?? "",
                                        marked: statement.text(1) ?? "")
        } catch {
            return ("", "", [])
        }
    }

    // MARK: - Joining hits to sessions

    /// A codex rollout stem's thread uuid, lower-cased — the FIRST uuid in
    /// `rollout-<iso>-<thread uuid>[_<rollout uuid>]`, matching
    /// `parsers.py::codex_thread_id` (`re.search`, not end-anchored: the
    /// trailing uuid of a resumed rollout only names the file).
    private static let codexUUID = try! NSRegularExpression(
        pattern: #"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"#,
        options: [.caseInsensitive])

    /// The id forms one session can be known by, for hit-to-session joining
    /// — mirrors `search_index.py::match_keys`. The index's `session_id` is
    /// whatever the writer recorded (a claude uuid, a codex thread uuid,
    /// pi's `id`), while listed sessions key on other forms (a codex rollout
    /// stem, a pi file path): so an id counts under itself, its file stem,
    /// and the codex thread uuid inside it. Safe on any string — a bare
    /// `abc-123` yields just `{"abc-123"}`.
    public static func matchKeys(_ sessionID: String) -> Set<String> {
        // `URL(fileURLWithPath: "")` resolves to the working directory —
        // an empty id has no keys at all.
        guard !sessionID.isEmpty else { return [] }
        var keys: Set<String> = [sessionID]
        let stem = URL(fileURLWithPath: sessionID).deletingPathExtension().lastPathComponent
        if !stem.isEmpty { keys.insert(stem) }
        if let found = codexUUID.firstMatch(
            in: sessionID, range: NSRange(sessionID.startIndex..., in: sessionID)),
           let range = Range(found.range, in: sessionID) {
            keys.insert(String(sessionID[range]).lowercased())
        }
        keys.remove("")
        return keys
    }
}
