import Foundation
import SQLite3

/// OpenCode sessions for the search index — Orca's synthetic-source design
/// (`session-scanner-opencode-sqlite-*.ts`,
/// `session-search-synthetic-sources.ts`): a session inside `opencode.db`
/// indexes under a synthetic `files.path` of `"<dbPath>#<sessionId>"` — a
/// container and an entry in one string — and is read whole on every pass
/// that owes it one, because a row in a container has no byte offset to
/// resume from.
enum SearchOpenCode {
    /// One `session` row: what the index needs to stat the synthetic file
    /// and to fill its `sessions` row.
    struct Session {
        var id: String
        var title: String
        var directory: String?
        var timeCreatedMs: Int64
        var timeUpdatedMs: Int64
        /// The synthetic `sizeBytes`: how many `message` rows the session
        /// holds. Together with the activity time it is the whole change
        /// detector — a session re-reads only when one of the two moved.
        var messageCount: Int64

        /// Last activity: `time_updated` when set, else `time_created` —
        /// Orca's effective mtime.
        var lastActivityMs: Int64 {
            timeUpdatedMs > 0 ? timeUpdatedMs : timeCreatedMs
        }

        /// The stat `readNeed` compares; dev/ino stay nil — a container row
        /// proves nothing about them.
        var stat: SearchFileStat {
            SearchFileStat(mtimeMs: Double(lastActivityMs), sizeBytes: messageCount)
        }
    }

    /// The synthetic `files.path` one session indexes under — the same
    /// `db#sessionId` spelling Orca's `buildOpenCodeSqliteCandidatePath`
    /// uses; the '#' keeps it out of any real path's namespace.
    static func syntheticPath(dbPath: String, sessionID: String) -> String {
        "\(dbPath)#\(sessionID)"
    }

    /// Milliseconds → the ISO-8601 spelling the `sessions` timestamps keep.
    static func iso(ms: Int64) -> String {
        Date(timeIntervalSince1970: Double(ms) / 1_000)
            .formatted(Date.ISO8601FormatStyle(includingFractionalSeconds: true))
    }

    /// `session`'s title, or the first-user-prompt fallback every other
    /// decoder reports when the row left none.
    static func title(of session: Session, messages: [SearchMessage]) -> String {
        if !session.title.isEmpty { return session.title }
        guard let text = messages.first(where: { $0.role == .user })?.text else { return "" }
        return SearchText.titleCandidate(text) ?? ""
    }

    /// Every indexable session the database lists — top-level and
    /// unarchived, newest activity first, matching Orca's list query.
    /// `limit` keeps only the newest N — a capped result is not a census:
    /// callers that retire on the enumeration must pass nil or see fewer
    /// rows than the cap.
    ///
    /// The schema is probed once per open: a database mid-rebuild or one
    /// whose shape this build predates throws here — one note for the
    /// source — instead of failing every session the same way.
    static func sessions(in db: SQLiteDatabase, limit: Int? = nil) throws -> [Session] {
        let sessionColumns = try columns(of: "session", in: db)
        guard sessionColumns.isSuperset(of: ["id", "time_created", "time_updated"]),
              try columns(of: "message", in: db)
                .isSuperset(of: ["id", "session_id", "time_created", "data"]),
              try columns(of: "part", in: db)
                .isSuperset(of: ["message_id", "time_created", "data"])
        else {
            throw SQLiteDatabase.Failure(
                code: SQLITE_ERROR,
                message: "lacks the session/message/part schema this build reads")
        }
        // Orca's `sessionColumnSelect`: a column the schema predates reads
        // as NULL rather than failing the query.
        let column = { (name: String) in sessionColumns.contains(name) ? "s.\(name)" : "NULL" }
        // Child sessions are subagent chatter (the exclusion `shouldIndex`
        // gives Claude `subagents/` files), and an archived session's index
        // row is the enumeration's to retire.
        let filters = (sessionColumns.contains("parent_id") ? " AND s.parent_id IS NULL" : "")
            + (sessionColumns.contains("time_archived") ? " AND s.time_archived IS NULL" : "")
        let statement = try db.prepare("""
            SELECT s.id, s.time_created, s.time_updated,
                   \(column("title")), \(column("directory")),
                   (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id)
            FROM session s
            WHERE 1=1\(filters)
            ORDER BY CASE WHEN s.time_updated > 0 THEN s.time_updated ELSE s.time_created END DESC
            \(limit.map { "LIMIT \($0)" } ?? "")
            """)
        var sessions: [Session] = []
        while try statement.step() {
            guard let id = statement.text(0) else { continue }
            sessions.append(Session(
                id: id, title: statement.text(3) ?? "", directory: statement.text(4),
                timeCreatedMs: statement.int(1), timeUpdatedMs: statement.int(2),
                messageCount: statement.int(5)))
        }
        return sessions
    }

    /// One session's transcript, folded the way Orca's capture does
    /// (`readOpenCodeSessionMessages`): a turn's `text` and `reasoning`
    /// parts join into the turn's one message — the way every other
    /// provider hands a consumer one message per turn — and each `tool`
    /// part is its own `.tool` message after the turn's words.
    static func messages(in db: SQLiteDatabase, sessionID: String) throws -> [SearchMessage] {
        let statement = try db.prepare("""
            SELECT m.id, json_extract(m.data, '$.role'),
                   json_extract(p.data, '$.type'), p.data, m.time_created
            FROM message m JOIN part p ON p.message_id = m.id
            WHERE m.session_id = ?
              AND json_extract(m.data, '$.role') IN ('user','assistant')
              AND json_extract(p.data, '$.type') IN ('text','reasoning','tool')
            ORDER BY m.time_created, m.id, p.time_created, p.rowid
            """)
        statement.bind([.text(sessionID)])
        var messages: [SearchMessage] = []
        var openID = ""
        var openRole: SearchRole?
        var openTS: String?
        var words: [String] = []
        var tools: [SearchMessage] = []
        // The turn's own words lead, its tool calls follow — the order a
        // content-block decode produces for every file provider.
        func flush() {
            if let openRole, let text = SearchText.bounded(words.joined(separator: "\n")) {
                messages.append(SearchMessage(role: openRole, text: text, ts: openTS))
            }
            messages.append(contentsOf: tools)
            words = []
            tools = []
        }
        while try statement.step() {
            let messageID = statement.text(0) ?? ""
            if messageID != openID {
                flush()
                openID = messageID
                openRole = statement.text(1).flatMap { SearchRole(rawValue: $0) }
                openTS = iso(ms: statement.int(4))
            }
            guard let data = statement.text(3) else { continue }
            if statement.text(2) == "tool" {
                if let text = toolText(data) {
                    tools.append(SearchMessage(role: .tool, text: text, ts: openTS))
                }
            } else if let text = partText(data) {
                words.append(text)
            }
        }
        flush()
        return messages
    }

    /// The text a `text` or `reasoning` part carries — Orca's
    /// `extractPartText`; every other part shape yields nil.
    private static func partText(_ data: String) -> String? {
        guard let object = (try? JSONSerialization.jsonObject(with: Data(data.utf8)))
                as? [String: Any]
        else { return nil }
        return object["text"] as? String
    }

    /// One `tool` part as the text a consumer searches: what ran, then what
    /// came back (Orca's `toolPartText`). The wording of each half is the
    /// shared `SearchContent` one on purpose: a search for a command finds
    /// it whichever agent ran it.
    private static func toolText(_ data: String) -> String? {
        guard let part = (try? JSONSerialization.jsonObject(with: Data(data.utf8)))
                as? [String: Any]
        else { return nil }
        let state = part["state"] as? [String: Any]
        // OpenCode spells the file argument `filePath`; the shared key list
        // spells it `file_path` (Orca's `sharedToolInputSpelling`).
        var input = state?["input"]
        if var record = input as? [String: Any],
           record["filePath"] is String, !(record["file_path"] is String) {
            record["file_path"] = record["filePath"]
            input = record
        }
        let text = [SearchContent.toolCallText(name: part["tool"], input: input), toolOutcome(state)]
            .compactMap { $0 }
            .joined(separator: "\n")
        return SearchText.bounded(text)
    }

    /// The outcome half of a tool part: `state.error` when the call failed
    /// or was cancelled, else `state.output` (Orca's `toolOutcome`).
    private static func toolOutcome(_ state: [String: Any]?) -> String? {
        if let error = state?["error"] as? String,
           !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return error
        }
        return state?["output"] as? String
    }

    /// The columns `table` declares — Orca's `columnExists` probe. The
    /// table names are fixed literals; PRAGMA takes no binds.
    private static func columns(of table: String, in db: SQLiteDatabase) throws -> Set<String> {
        let statement = try db.prepare("PRAGMA table_info(\(table))")
        var names: Set<String> = []
        while try statement.step() {
            if let name = statement.text(1) { names.insert(name) }
        }
        return names
    }
}
