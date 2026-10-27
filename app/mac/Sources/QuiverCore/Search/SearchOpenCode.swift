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
    /// One `session` (or `session_v2`) row: what the index needs to stat the synthetic file
    /// and to fill its `sessions` row.
    struct Session {
        var id: String
        var title: String
        var directory: String?
        var timeCreatedMs: Int64
        var timeUpdatedMs: Int64
        /// The synthetic `sizeBytes`: how many `message` rows the session
        /// holds, plus its 2.x `session_message` rows not copied from them. Together with the activity time it is the whole change
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
        // OpenCode 2.x writes new sessions only to `session_v2` and their
        // messages only to `session_message` (see `v2Messages`). A session
        // in both tables reads its v2 row, the one 2.x keeps current, with
        // the legacy title and directory as fallbacks.
        let v2Columns = try columns(of: "session_v2", in: db)
        let hasV2 = v2Columns.isSuperset(of: ["id", "time_created", "time_updated"])
        let v2Count = try columns(of: "session_message", in: db).isSuperset(of: ["id", "session_id"])
            ? { (id: String) in "(SELECT COUNT(*) FROM session_message sm WHERE sm.session_id = \(id)"
                + " AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id))" }
            : { (_: String) in "0" }
        let legacy = """
            SELECT s.id AS id, s.time_created AS time_created, s.time_updated AS time_updated,
                   \(column("title")) AS title, \(column("directory")) AS directory,
                   (SELECT COUNT(*) FROM message m WHERE m.session_id = s.id) + \(v2Count("s.id")) AS n
            FROM session s
            WHERE 1=1\(filters)
            """
        let v2Column = { (name: String) in v2Columns.contains(name) ? "v.\(name)" : "NULL" }
        let v2Filters = (v2Columns.contains("parent_id") ? " AND v.parent_id IS NULL" : "")
            + (v2Columns.contains("time_archived") ? " AND v.time_archived IS NULL" : "")
        let rows = hasV2 ? """
            SELECT v.id AS id, v.time_created AS time_created,
                   MAX(v.time_updated, COALESCE(s.time_updated, 0)) AS time_updated,
                   COALESCE(NULLIF(\(v2Column("title")), ''), \(column("title"))) AS title,
                   COALESCE(NULLIF(\(v2Column("directory")), ''), \(column("directory"))) AS directory,
                   (SELECT COUNT(*) FROM message m WHERE m.session_id = v.id) + \(v2Count("v.id")) AS n
            FROM session_v2 v LEFT JOIN session s ON s.id = v.id
            WHERE 1=1\(v2Filters)
            UNION ALL
            \(legacy) AND NOT EXISTS (SELECT 1 FROM session_v2 v WHERE v.id = s.id)
            """ : legacy
        let statement = try db.prepare("""
            SELECT id, time_created, time_updated, title, directory, n FROM (\(rows))
            ORDER BY CASE WHEN time_updated > 0 THEN time_updated ELSE time_created END DESC
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
        return messages + (try v2Messages(in: db, sessionID: sessionID))
    }

    /// The session's OpenCode 2.x turns, after its legacy ones: one
    /// `session_message` row per turn, its JSON `data` holding the whole
    /// turn. A prompt (`user`, `synthetic`) is its `text`; an assistant
    /// row's `text` and `reasoning` blocks join into one message and each
    /// `tool` block follows as its own, like a legacy turn's parts. Rows
    /// the 2.x migration copied from `message` keep their ids and were
    /// read as legacy rows; system and compaction rows are bookkeeping.
    private static func v2Messages(in db: SQLiteDatabase, sessionID: String) throws -> [SearchMessage] {
        guard try columns(of: "session_message", in: db)
                .isSuperset(of: ["id", "session_id", "type", "seq", "time_created", "data"])
        else { return [] }
        let statement = try db.prepare("""
            SELECT sm.type, sm.data, sm.time_created FROM session_message sm
            WHERE sm.session_id = ? AND sm.type IN ('user','synthetic','assistant')
              AND NOT EXISTS (SELECT 1 FROM message m WHERE m.id = sm.id)
            ORDER BY sm.seq
            """)
        statement.bind([.text(sessionID)])
        var messages: [SearchMessage] = []
        while try statement.step() {
            guard let raw = statement.text(1),
                  let data = (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any]
            else { continue }
            let ts = iso(ms: statement.int(2))
            if statement.text(0) != "assistant" {
                if let text = (data["text"] as? String).flatMap({ SearchText.bounded($0) }) {
                    messages.append(SearchMessage(role: .user, text: text, ts: ts))
                }
                continue
            }
            var words: [String] = []
            var tools: [SearchMessage] = []
            for block in data["content"] as? [[String: Any]] ?? [] {
                switch block["type"] as? String {
                case "text", "reasoning":
                    if let text = block["text"] as? String { words.append(text) }
                case "tool":
                    // The legacy part's shape: `tool` names it, and the
                    // output is the text of `state.content`'s blocks.
                    var state = block["state"] as? [String: Any] ?? [:]
                    if state["output"] == nil, let content = state["content"] as? [[String: Any]] {
                        state["output"] = content.compactMap { $0["text"] as? String }.joined(separator: "\n")
                    }
                    if let text = toolText(["tool": block["name"] as Any, "state": state]) {
                        tools.append(SearchMessage(role: .tool, text: text, ts: ts))
                    }
                default: break
                }
            }
            if let text = SearchText.bounded(words.joined(separator: "\n")) {
                messages.append(SearchMessage(role: .assistant, text: text, ts: ts))
            }
            messages.append(contentsOf: tools)
        }
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
        return toolText(part)
    }

    private static func toolText(_ part: [String: Any]) -> String? {
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
