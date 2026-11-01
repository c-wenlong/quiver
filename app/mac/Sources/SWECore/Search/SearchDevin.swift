import Foundation
import SQLite3

/// Devin CLI sessions for the search index. Every turn lives in
/// `~/.local/share/devin/cli/sessions.db` (`message_nodes`), so each session
/// indexes under a synthetic `"<dbPath>#<sessionId>"` path the way
/// `SearchOpenCode` does, read whole whenever its stat moves. The
/// `transcripts/*.json` ATIF exports beside the database cover only the
/// sessions someone exported; both are read, and search keeps one hit per
/// session id.
///
/// swe writes the same rows (`sessions/search_containers.py`): keep the
/// stat, the chain walk and the fields in step, or each writer reads the
/// other's rows as stale and replaces them on every pass.
enum SearchDevin {
    /// One visible `sessions` row.
    struct Session {
        var id: String
        var title: String
        var directory: String?
        var createdMs: Int64
        var activityMs: Int64
        /// `main_chain_id`: the head of the chain that holds every message's
        /// final version.
        var head: Int64?
        /// Every node the session holds, streamed copies included: a
        /// streamed reply is stored again as it grows, so the count moves
        /// with it. Together with the activity time it is the whole change
        /// detector.
        var nodes: Int64

        var lastActivityMs: Int64 { activityMs > 0 ? activityMs : createdMs }

        /// The stat `readNeed` compares; dev/ino stay nil — a container row
        /// proves nothing about them.
        var stat: SearchFileStat {
            SearchFileStat(mtimeMs: Double(lastActivityMs), sizeBytes: nodes)
        }
    }

    /// Devin stamps are epoch seconds; a value past `1e12` is already
    /// milliseconds (`SearchStamp`'s rule).
    static func milliseconds(_ value: Int64) -> Int64 {
        guard value > 0 else { return 0 }
        return value > 1_000_000_000_000 ? value : value * 1000
    }

    /// Every session the database shows, newest activity first. `limit`
    /// keeps only the newest N, and a capped result is not a census.
    static func sessions(in db: SQLiteDatabase, limit: Int? = nil) throws -> [Session] {
        let sessionColumns = try columns(of: "sessions", in: db)
        guard sessionColumns.isSuperset(of: ["id", "created_at", "last_activity_at"]),
              try columns(of: "message_nodes", in: db)
                .isSuperset(of: ["session_id", "node_id", "parent_node_id", "chat_message"])
        else {
            throw SQLiteDatabase.Failure(
                code: SQLITE_ERROR,
                message: "lacks the sessions/message_nodes schema this build reads")
        }
        let column = { (name: String) in sessionColumns.contains(name) ? "s.\(name)" : "NULL" }
        let hidden = sessionColumns.contains("hidden") ? " WHERE s.hidden = 0" : ""
        let statement = try db.prepare("""
            SELECT s.id, \(column("title")), \(column("working_directory")),
                   s.created_at, s.last_activity_at, \(column("main_chain_id")),
                   (SELECT COUNT(*) FROM message_nodes n WHERE n.session_id = s.id)
            FROM sessions s\(hidden)
            ORDER BY s.last_activity_at DESC, s.id
            \(limit.map { "LIMIT \($0)" } ?? "")
            """)
        var sessions: [Session] = []
        while try statement.step() {
            guard let id = statement.text(0), !id.isEmpty else { continue }
            sessions.append(Session(
                id: id, title: statement.text(1) ?? "", directory: statement.text(2),
                createdMs: milliseconds(statement.int(3)),
                activityMs: milliseconds(statement.int(4)),
                head: statement.isNull(5) ? nil : statement.int(5),
                nodes: statement.int(6)))
        }
        return sessions
    }

    /// The main chain's node ids, oldest first. Streamed copies are
    /// siblings in a forest; walking parents from the head gives one node
    /// per message, and without a head the newest node stands in. Only ids
    /// load here, never the forest's text.
    static func chain(in db: SQLiteDatabase, session: Session) throws -> [Int64] {
        let statement = try db.prepare(
            "SELECT node_id, parent_node_id FROM message_nodes WHERE session_id = ?")
        statement.bind([.text(session.id)])
        var parents: [Int64: Int64?] = [:]
        while try statement.step() {
            parents[statement.int(0)] = statement.isNull(1) ? nil : statement.int(1)
        }
        var cursor: Int64? = session.head.flatMap { parents[$0] != nil ? $0 : nil }
            ?? parents.keys.max()
        var ordered: [Int64] = []
        var seen: Set<Int64> = []
        while let node = cursor, let parent = parents[node], seen.insert(node).inserted {
            ordered.append(node)
            cursor = parent
        }
        return ordered.reversed()
    }

    /// The session's messages through the Devin decoder (its `chat_message`
    /// shape), plus the first-prompt title candidate the decoder offered.
    static func messages(
        in db: SQLiteDatabase, session: Session
    ) throws -> (messages: [SearchMessage], promptTitle: String?) {
        let statement = try db.prepare(
            "SELECT chat_message FROM message_nodes WHERE session_id = ? AND node_id = ?")
        var decoder = SearchDecoder(.devin)
        var messages: [SearchMessage] = []
        var title: String?
        for node in try chain(in: db, session: session) {
            statement.bind([.text(session.id), .int(node)])
            guard try statement.step(), let raw = statement.text(0) else { continue }
            let output = decoder.decode(line: Data(raw.utf8))
            messages += output.messages
            if title == nil { title = output.hints.title }
        }
        return (messages, title)
    }

    /// `sessions.title` unless Devin left its placeholder, else the first
    /// prompt.
    static func title(of session: Session, promptTitle: String?) -> String {
        if let title = JSONValue.string(session.title), title != "Untitled" { return title }
        return promptTitle ?? ""
    }

    private static func columns(of table: String, in db: SQLiteDatabase) throws -> Set<String> {
        let statement = try db.prepare("PRAGMA table_info(\(table))")
        var names: Set<String> = []
        while try statement.step() {
            if let name = statement.text(1) { names.insert(name) }
        }
        return names
    }
}
