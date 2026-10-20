import Foundation
import SQLite3

/// What a `files` row still owes a reader.
///
/// `current`: the index's rows match the file at the stat the row records.
/// `due`: the index is behind on a span it cannot reach by appending, so the
/// next pass reads the file whole.
/// `failed`: the last read did not commit; `failCount` and `failedMtimeMs`
/// are what stop it being retried for ever.
public enum SearchFileState: String, Sendable, CaseIterable {
    case current, due, failed
}

/// The stat a `files` row records: what identified the file when it was read.
/// `dev`/`ino` are optional because a stat from a partial observation must
/// never overwrite a pair a real stat proved.
public struct SearchFileStat: Equatable, Sendable {
    public var dev: Int64?
    public var ino: Int64?
    public var mtimeMs: Double
    public var sizeBytes: Int64?

    public init(dev: Int64? = nil, ino: Int64? = nil, mtimeMs: Double, sizeBytes: Int64? = nil) {
        self.dev = dev
        self.ino = ino
        self.mtimeMs = mtimeMs
        self.sizeBytes = sizeBytes
    }
}

/// The pair a write fences on: the session its rows hang off and the cursor
/// they continue from. Re-read inside every write transaction — a remove or
/// another writer between the read and the commit means the buffered rows no
/// longer continue anything, and committing on top of that would resurrect a
/// deleted source or duplicate a span.
public struct SearchFileFence: Equatable, Sendable {
    public var sessionRowID: Int64?
    public var byteOffset: Int64

    public init(sessionRowID: Int64?, byteOffset: Int64) {
        self.sessionRowID = sessionRowID
        self.byteOffset = byteOffset
    }
}

/// One row of the `files` table: the indexer's whole memory between passes.
public struct SearchFileRow: Equatable, Sendable {
    public var path: String
    public var dev: Int64?
    public var ino: Int64?
    /// `SearchStore.partialFileOffset` (-1) marks a half-written file.
    public var byteOffset: Int64
    public var mtimeMs: Double
    public var sizeBytes: Int64?
    public var sessionRowID: Int64?
    public var state: SearchFileState
    public var failCount: Int
    public var failedMtimeMs: Double?
}

/// Everything a `sessions` row carries. `agent` and `filePath` are required;
/// the rest is what the parser has decoded so far — a row a chunk commits is
/// written provisionally and the finishing commit fills it in.
public struct SearchSessionFields: Equatable, Sendable {
    public var agent: String
    public var sessionID: String
    public var filePath: String
    public var title: String
    public var cwd: String?
    public var cwdKey: String?
    public var createdAt: String?
    public var updatedAt: String?
    public var messageCount: Int

    public init(agent: String, sessionID: String = "", filePath: String, title: String = "",
                cwd: String? = nil, cwdKey: String? = nil,
                createdAt: String? = nil, updatedAt: String? = nil, messageCount: Int = 0) {
        self.agent = agent
        self.sessionID = sessionID
        self.filePath = filePath
        self.title = title
        self.cwd = cwd
        self.cwdKey = cwdKey
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.messageCount = messageCount
    }
}

/// A `sessions` row read back: its id plus its fields.
public struct SearchSession: Equatable, Sendable {
    public var id: Int64
    public var fields: SearchSessionFields
}

/// One indexable unit: a row in `messages` and the FTS row with the same id.
/// Fill the one column the role searches under (user, assistant or tool
/// text); `identifiers` carries the split form of the same text — camelCase,
/// snake_case and path pieces — so code tokens match.
public struct SearchMessageRow: Equatable, Sendable {
    public var role: String
    public var ts: String?
    public var userText: String
    public var assistantText: String
    public var toolText: String
    public var identifiers: String

    public init(role: String, ts: String? = nil,
                userText: String = "", assistantText: String = "",
                toolText: String = "", identifiers: String = "") {
        self.role = role
        self.ts = ts
        self.userText = userText
        self.assistantText = assistantText
        self.toolText = toolText
        self.identifiers = identifiers
    }
}

/// The whole of the indexer's progress report: rows per file state, and the
/// session and message totals beside them.
public struct SearchStatus: Equatable, Sendable {
    public var filesByState: [SearchFileState: Int] = [:]
    /// Sessions per agent. The one number that distinguishes an agent the
    /// index has read from one it has only listed.
    public var sessionsByAgent: [String: Int] = [:]
    public var sessionCount = 0
    public var messageCount = 0

    public init() {}
}

/// The persisted search index: per-file cursors, session rows, and message
/// text in an FTS5 table. Adapted from Orca's `session-search-store.ts` and
/// `session-search-index-writer.ts`.
///
/// Not thread-safe — like `LedgerStore`, one owner serializes calls.
///
/// Two conventions come from Orca and are load-bearing:
///
/// - `messages_fts.rowid` equals `messages.id`. Both are inserted by hand in
///   the same transaction; there are no sync triggers and no foreign keys.
/// - Replacing a file's session mints a NEW `sessions` row and deletes only
///   the OLD row. Every retrieval joins `sessions`, so the old generation
///   stops answering the moment that commits; its messages are reclaimed
///   afterwards by `drainOrphanedMessages`, never deleted inline — deleting a
///   whole conversation inside a write transaction sizes it by the session
///   being replaced rather than by the chunk being written.
public final class SearchStore {
    /// `files.byte_offset` for a read that has not reached the end of the
    /// file. Not a byte offset, so an append is declined and the file is
    /// re-read whole; the rows it did write still answer searches.
    public static let partialFileOffset: Int64 = -1

    /// Quiver's own schema version (see `SearchSchema`).
    public static var schemaVersion: Int { SearchSchema.version }

    public let url: URL
    public let directory: URL
    /// Whether this build of SQLite has FTS5. When false the rest of the
    /// schema still exists and callers should degrade: skip indexing, answer
    /// no searches.
    public let fts5Available: Bool

    /// The write connection; nil after `clear()`. Internal so tests can read
    /// through it under `@testable`.
    var db: SQLiteDatabase!

    /// - Parameter directory: where `search.sqlite` lives; production uses
    ///   `SearchLocation.directory(under:)` (`~/.quiver/data/search/`).
    public init(directory: URL) throws {
        self.directory = directory
        url = directory.appending(path: SearchLocation.fileName)
        let opened = try SearchSchema.open(url: url)
        db = opened.connection
        fts5Available = opened.fts5Available
    }

    // MARK: - Files

    /// Every field the index holds for `path`, or nil when it holds nothing.
    public func fileRow(for path: String) throws -> SearchFileRow? {
        let statement = try db.prepare("""
            SELECT path, dev, ino, byte_offset, mtime_ms, size_bytes, session_row_id,
                   state, fail_count, failed_mtime_ms
            FROM files WHERE path = ?
            """)
        statement.bind([.text(path)])
        guard try statement.step() else { return nil }
        return Self.fileRow(statement)
    }

    /// Every row this index holds, path-ordered for a deterministic listing.
    public func files() throws -> [SearchFileRow] {
        let statement = try db.prepare("""
            SELECT path, dev, ino, byte_offset, mtime_ms, size_bytes, session_row_id,
                   state, fail_count, failed_mtime_ms
            FROM files ORDER BY path
            """)
        var rows: [SearchFileRow] = []
        while try statement.step() {
            rows.append(Self.fileRow(statement))
        }
        return rows
    }

    /// Just the pair a write fences on, for the cheap read outside a
    /// transaction.
    public func fileFence(for path: String) throws -> SearchFileFence? {
        let statement = try db.prepare("SELECT session_row_id, byte_offset FROM files WHERE path = ?")
        statement.bind([.text(path)])
        guard try statement.step() else { return nil }
        return SearchFileFence(sessionRowID: statement.isNull(0) ? nil : statement.int(0),
                               byteOffset: statement.int(1))
    }

    private static func fileRow(_ row: Statement) -> SearchFileRow {
        SearchFileRow(
            path: row.text(0) ?? "",
            dev: row.isNull(1) ? nil : row.int(1),
            ino: row.isNull(2) ? nil : row.int(2),
            byteOffset: row.int(3),
            mtimeMs: row.double(4),
            sizeBytes: row.isNull(5) ? nil : row.int(5),
            sessionRowID: row.isNull(6) ? nil : row.int(6),
            state: SearchFileState(rawValue: row.text(7) ?? "") ?? .current,
            failCount: Int(row.int(8)),
            failedMtimeMs: row.isNull(9) ? nil : row.double(9))
    }

    /// Records where `path` was read up to. `byteOffset` may be
    /// `partialFileOffset`: the rows are a coherent prefix but nothing may
    /// append to them.
    ///
    /// Partial observations must never create a dev/ino pair that no stat
    /// proved, so a missing pair keeps the stored one rather than erasing it.
    public func upsertFile(path: String, stat: SearchFileStat,
                           byteOffset: Int64, sessionRowID: Int64?) throws {
        try db.prepare("""
            INSERT INTO files(path, dev, ino, byte_offset, mtime_ms, size_bytes, session_row_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              dev = CASE WHEN excluded.dev IS NOT NULL AND excluded.ino IS NOT NULL
                    THEN excluded.dev ELSE files.dev END,
              ino = CASE WHEN excluded.dev IS NOT NULL AND excluded.ino IS NOT NULL
                    THEN excluded.ino ELSE files.ino END,
              byte_offset = excluded.byte_offset, mtime_ms = excluded.mtime_ms,
              size_bytes = excluded.size_bytes, session_row_id = excluded.session_row_id
            """).bind([
            .text(path), stat.dev.map(SQLiteValue.int) ?? .null,
            stat.ino.map(SQLiteValue.int) ?? .null,
            .int(byteOffset), .double(stat.mtimeMs),
            stat.sizeBytes.map(SQLiteValue.int) ?? .null,
            sessionRowID.map(SQLiteValue.int) ?? .null,
        ]).run()
    }

    /// Moves a row's read state.
    ///
    /// `failed` also counts the failure and records the stat it happened at,
    /// which is what lets the next pass tell "this file has never worked"
    /// from "this file changed since it last failed". A path with no row
    /// still gets one on `failed`: the common unreadable file is one the
    /// index never managed to hold, and with nowhere to write the count it
    /// would be retried on every pass for the life of the process. `current`
    /// and `due` only move an existing row.
    public func setFileState(_ path: String, _ state: SearchFileState, atMtimeMs: Double? = nil) throws {
        if state == .failed {
            try db.prepare("""
                INSERT INTO files(path, byte_offset, mtime_ms, state, fail_count, failed_mtime_ms)
                VALUES (?, 0, ?, 'failed', 1, ?)
                ON CONFLICT(path) DO UPDATE SET
                  state = 'failed',
                  fail_count = files.fail_count + 1,
                  failed_mtime_ms = excluded.failed_mtime_ms
                """).bind([.text(path), .double(atMtimeMs ?? 0), .optionalDouble(atMtimeMs)]).run()
            return
        }
        try db.prepare("""
            UPDATE files SET state = ?, fail_count = 0, failed_mtime_ms = NULL WHERE path = ?
            """).bind([.text(state.rawValue), .text(path)]).run()
    }

    /// Deletes `path`'s row and the session it pointed at, in one
    /// transaction — a source proven gone. Its messages stop answering at
    /// once (every retrieval joins `sessions`) and the orphan drain
    /// reclaims them in its own bounded passes, the same lifecycle a
    /// `replaceSession` leaves behind. Orca's writer calls this
    /// `removeFile`.
    public func removeFile(_ path: String) throws {
        try db.execute("BEGIN IMMEDIATE")
        do {
            if let sessionRowID = try fileFence(for: path)?.sessionRowID {
                try db.prepare("DELETE FROM sessions WHERE id = ?")
                    .bind([.int(sessionRowID)]).run()
            }
            try db.prepare("DELETE FROM files WHERE path = ?").bind([.text(path)]).run()
            try db.execute("COMMIT")
        } catch {
            try? db.execute("ROLLBACK")
            throw error
        }
    }

    // MARK: - Sessions

    /// Inserts a `sessions` row and returns its id. `sessionID` and `title`
    /// may be empty: a row a chunk commits is written before the parser has
    /// said what the session is, and `updateSession` fills it in later.
    @discardableResult
    public func createSession(_ fields: SearchSessionFields) throws -> Int64 {
        try db.prepare("""
            INSERT INTO sessions(agent, session_id, file_path, title, cwd, cwd_key,
                                 created_at, updated_at, message_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """).bind(Self.sessionValues(fields)).run()
        return db.lastInsertRowID
    }

    /// Rewrites every field of a `sessions` row — the decoded session always
    /// wins over the provisional values a chunk wrote.
    public func updateSession(_ rowID: Int64, _ fields: SearchSessionFields) throws {
        try db.prepare("""
            UPDATE sessions SET agent = ?, session_id = ?, file_path = ?, title = ?,
                cwd = ?, cwd_key = ?, created_at = ?, updated_at = ?, message_count = ?
            WHERE id = ?
            """).bind(Self.sessionValues(fields) + [.int(rowID)]).run()
    }

    private static func sessionValues(_ fields: SearchSessionFields) -> [SQLiteValue?] {
        [
            .text(fields.agent), .text(fields.sessionID), .text(fields.filePath),
            .text(fields.title), fields.cwd.map(SQLiteValue.text) ?? .null,
            fields.cwdKey.map(SQLiteValue.text) ?? .null,
            fields.createdAt.map(SQLiteValue.text) ?? .null,
            fields.updatedAt.map(SQLiteValue.text) ?? .null,
            .int(fields.messageCount),
        ]
    }

    /// A `sessions` row and its id, or nil when it is gone.
    public func sessionRow(_ id: Int64) throws -> SearchSession? {
        let statement = try db.prepare("""
            SELECT agent, session_id, file_path, title, cwd, cwd_key,
                   created_at, updated_at, message_count
            FROM sessions WHERE id = ?
            """)
        statement.bind([.int(id)])
        guard try statement.step() else { return nil }
        return SearchSession(id: id, fields: SearchSessionFields(
            agent: statement.text(0) ?? "", sessionID: statement.text(1) ?? "",
            filePath: statement.text(2) ?? "", title: statement.text(3) ?? "",
            cwd: statement.text(4), cwdKey: statement.text(5),
            createdAt: statement.text(6), updatedAt: statement.text(7),
            messageCount: Int(statement.int(8))))
    }

    /// Points `path` at a fresh `sessions` row and deletes the old row only,
    /// in one transaction. The old generation's messages stop answering the
    /// moment this commits — every retrieval joins `sessions` — and the
    /// orphan drain reclaims them later.
    ///
    /// Fenced like `commitFile`: the record is re-read under the write lock
    /// and nothing happens when it moved. Returns the new row id, or nil when
    /// there is no `files` row or it no longer matches `expected`. A caller
    /// follows with `commitFile` expected at `(new id, stored offset)`, which
    /// a whole-file re-read commits atomically per Orca's writer.
    @discardableResult
    public func replaceSession(for path: String, expected: SearchFileFence?,
                               fields: SearchSessionFields) throws -> Int64? {
        try db.execute("BEGIN IMMEDIATE")
        do {
            guard let actual = try fileFence(for: path),
                  actual.sessionRowID == expected?.sessionRowID,
                  actual.byteOffset == expected?.byteOffset else {
                try db.execute("ROLLBACK")
                return nil
            }
            let rowID = try createSession(fields)
            try db.prepare("UPDATE files SET session_row_id = ? WHERE path = ?")
                .bind([.int(rowID), .text(path)]).run()
            if let old = actual.sessionRowID {
                try db.prepare("DELETE FROM sessions WHERE id = ?").bind([.int(old)]).run()
            }
            try db.execute("COMMIT")
            return rowID
        } catch {
            try? db.execute("ROLLBACK")
            throw error
        }
    }

    // MARK: - Per-file write transaction

    /// Commits one read's rows for `path` in a single `BEGIN IMMEDIATE`
    /// transaction: re-reads the `files` row and verifies it still equals
    /// `expected` (nil = no row expected), writes the message and FTS rows
    /// under `sessionRowID`, and advances the cursor — all or nothing.
    ///
    /// - Returns: false when the record moved under the read — it was removed,
    ///   or another writer moved the cursor these rows continue from — and
    ///   nothing was written. A read that never calls this leaves the index
    ///   exactly as it found it.
    /// - Parameters:
    ///   - sessionRowID: the session the messages hang off and the files row
    ///     points at; nil writes only the cursor (a file read through that
    ///     held no session). Required when `messages` is non-empty.
    ///   - updating: when set, rewrites the session row's fields in the same
    ///     transaction, so a finishing commit publishes the decoded session
    ///     together with its last rows.
    @discardableResult
    public func commitFile(
        path: String,
        expected: SearchFileFence?,
        stat: SearchFileStat,
        byteOffset: Int64,
        sessionRowID: Int64?,
        messages: [SearchMessageRow],
        updating fields: SearchSessionFields? = nil
    ) throws -> Bool {
        if sessionRowID == nil && (!messages.isEmpty || fields != nil) {
            throw SQLiteDatabase.Failure(
                code: SQLITE_MISUSE, message: "commitFile: messages and fields need a sessionRowID")
        }
        try db.execute("BEGIN IMMEDIATE")
        do {
            let actual = try fileFence(for: path)
            guard actual?.sessionRowID == expected?.sessionRowID,
                  actual?.byteOffset == expected?.byteOffset else {
                try db.execute("ROLLBACK")
                return false
            }
            if let sessionRowID {
                try insertMessages(under: sessionRowID, messages)
                if let fields {
                    try updateSession(sessionRowID, fields)
                }
            }
            try upsertFile(path: path, stat: stat, byteOffset: byteOffset, sessionRowID: sessionRowID)
            try db.execute("COMMIT")
            return true
        } catch {
            try? db.execute("ROLLBACK")
            throw error
        }
    }

    /// Rows in `messages` and the FTS rows with the same ids, in the caller's
    /// transaction, so a message is never present in one and absent in the
    /// other.
    private func insertMessages(under sessionRowID: Int64, _ messages: [SearchMessageRow]) throws {
        guard !messages.isEmpty else { return }
        let insertMessage = try db.prepare(
            "INSERT INTO messages(session_row_id, role, ts) VALUES (?, ?, ?)")
        let insertFTS = fts5Available ? try db.prepare("""
            INSERT INTO messages_fts(rowid, user_text, assistant_text, tool_text, identifiers)
            VALUES (?, ?, ?, ?, ?)
            """) : nil
        for message in messages {
            try insertMessage.bind([.int(sessionRowID), .text(message.role),
                                    message.ts.map(SQLiteValue.text) ?? .null]).run()
            if let insertFTS {
                try insertFTS.bind([.int(db.lastInsertRowID), .text(message.userText),
                                    .text(message.assistantText), .text(message.toolText),
                                    .text(message.identifiers)]).run()
            }
        }
    }

    // MARK: - Orphan drain

    /// Reclaims up to `limit` rows of one orphaned session's messages — a set
    /// a replace, a removed source or an interrupted earlier drain leaves
    /// behind — in one transaction, then hands pages back in a bounded step.
    /// `auto_vacuum = INCREMENTAL` holds every freed page until asked, and
    /// asking in step with the deletes is many short stalls instead of one
    /// long one at the end.
    ///
    /// Nothing records the work: rows whose session row is gone are the whole
    /// record, so a crash before or during a drain is found by the next one.
    ///
    /// - Returns: rows deleted this call; call repeatedly until it returns 0.
    @discardableResult
    public func drainOrphanedMessages(limit: Int = 256) throws -> Int {
        // Scoped: an unfinalized statement that read a row keeps a cursor on
        // messages open, which locks the deletes below with SQLITE_LOCKED.
        let orphan: Int64? = try {
            let probe = try db.prepare("""
                SELECT session_row_id FROM messages
                WHERE session_row_id NOT IN (SELECT id FROM sessions) LIMIT 1
                """)
            return try probe.step() ? probe.int(0) : nil
        }()
        guard let orphan else {
            try db.execute("PRAGMA incremental_vacuum(2000)")
            return 0
        }
        try db.execute("BEGIN IMMEDIATE")
        do {
            let ids: [Int64] = try {
                let pick = try db.prepare("SELECT id FROM messages WHERE session_row_id = ? LIMIT ?")
                pick.bind([.int(orphan), .int(limit)])
                var ids: [Int64] = []
                while try pick.step() { ids.append(pick.int(0)) }
                return ids
            }()
            let deleteFTS = fts5Available
                ? try db.prepare("DELETE FROM messages_fts WHERE rowid = ?") : nil
            let deleteMessage = try db.prepare("DELETE FROM messages WHERE id = ?")
            for id in ids {
                if let deleteFTS {
                    try deleteFTS.bind([.int(id)]).run()
                }
                try deleteMessage.bind([.int(id)]).run()
            }
            try db.execute("COMMIT")
            try db.execute("PRAGMA incremental_vacuum(2000)")
            return ids.count
        } catch {
            try? db.execute("ROLLBACK")
            throw error
        }
    }

    // MARK: - Status and teardown

    /// Rows per file state and the session and message totals. The status is
    /// these queries; a caller polls it rather than tracking progress itself.
    public func status() throws -> SearchStatus {
        var status = SearchStatus()
        let states = try db.prepare("SELECT state, count(*) FROM files GROUP BY state")
        while try states.step() {
            if let state = SearchFileState(rawValue: states.text(0) ?? "") {
                status.filesByState[state] = Int(states.int(1))
            }
        }
        let agents = try db.prepare("SELECT agent, count(*) FROM sessions GROUP BY agent")
        while try agents.step() {
            if let agent = agents.text(0) {
                status.sessionsByAgent[agent] = Int(agents.int(1))
            }
        }
        status.sessionCount = Int(try db.scalar("SELECT count(*) FROM sessions"))
        status.messageCount = Int(try db.scalar("SELECT count(*) FROM messages"))
        return status
    }

    /// Closes the connection and deletes the database with every journal file
    /// beside it. The store is unusable afterwards.
    public func clear() {
        db = nil
        SearchLocation.removeDatabase(at: url)
    }
}
