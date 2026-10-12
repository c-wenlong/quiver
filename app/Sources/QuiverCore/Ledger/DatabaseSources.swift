import Foundation

/// Harnesses that keep history in SQLite. Both are opened strictly read-only
/// and only token metadata is selected, never message bodies.
///
/// A database is skipped while it and its WAL are unchanged. Otherwise only
/// rows past a watermark are read, and every row is keyed so re-reading one
/// is harmless.
enum DatabaseSources {
    /// A read from the source database failed. `Ledger.scan` downgrades
    /// only these to a per-source note: a failure on Quiver's own cache
    /// (meta, upsert, BEGIN, COMMIT) still throws so the Overview shows a
    /// real error instead of quietly skipping.
    struct SourceRead: Error {
        var underlying: Error
    }

    /// Runs a call against the source database, tagging its failure.
    private static func reading<T>(_ body: () throws -> T) throws -> T {
        do { return try body() } catch { throw SourceRead(underlying: error) }
    }

    struct Fingerprint: Equatable {
        var text: String
        var inode: UInt64

        init?(_ url: URL) {
            guard let db = FileInfo(path: url.path) else { return nil }
            let wal = FileInfo(path: url.path + "-wal")
            inode = db.inode
            text = "\(db.inode):\(db.size):\(db.modified):\(wal?.size ?? 0):\(wal?.modified ?? 0)"
        }
    }

    /// The saved watermark, or 0 when the database is no longer the file it
    /// was taken from: a recreated or restored file starts its row ids and
    /// timestamps over, and every row is keyed, so reading it all is safe.
    static func watermark(_ metaKey: String, _ fingerprint: Fingerprint, store: LedgerStore) throws -> Int64 {
        guard try store.meta(metaKey + ":inode") == String(fingerprint.inode) else { return 0 }
        return Int64(try store.meta(metaKey + ":watermark") ?? "") ?? 0
    }

    // MARK: - OpenCode

    /// `~/.local/share/opencode/opencode.db`: one `message` row per message,
    /// its JSON `data` carrying role, model, cost and token counts. Messages
    /// are rewritten while they stream, so the watermark is `time_updated`
    /// and the key is the message id (the larger total wins).
    ///
    /// Current OpenCode stores the five token kinds disjointly
    /// (`total = input + output + reasoning + cache`); older rows folded
    /// reasoning into output (`total = input + output + cache`), which is
    /// detected per row. A recorded cost of 0 is OpenCode not knowing the
    /// price, so it is treated as no recorded cost.
    static func scanOpenCode(_ url: URL, store: LedgerStore) throws -> Bool {
        guard let fingerprint = Fingerprint(url) else { return false }
        let metaKey = "opencode:\(url.path)"
        if try store.meta(metaKey + ":fingerprint") == fingerprint.text { return false }
        let watermark = try watermark(metaKey, fingerprint, store: store)

        let source = try reading { try SQLiteDatabase(readOnly: url) }
        let statement = try reading { try source.prepare("""
            SELECT m.id, m.session_id, m.time_created, m.time_updated, s.directory,
                json_extract(m.data, '$.modelID'), json_extract(m.data, '$.cost'),
                json_extract(m.data, '$.tokens.input'), json_extract(m.data, '$.tokens.output'),
                json_extract(m.data, '$.tokens.reasoning'), json_extract(m.data, '$.tokens.cache.read'),
                json_extract(m.data, '$.tokens.cache.write'), json_extract(m.data, '$.tokens.total')
            FROM message m LEFT JOIN session s ON s.id = m.session_id
            WHERE m.time_updated >= ? AND json_extract(m.data, '$.role') = 'assistant'
            ORDER BY m.time_updated
            """) }
        statement.bind([.int(watermark)])
        let accountHome = url.deletingLastPathComponent().path
        var highest = watermark
        try store.db.execute("BEGIN IMMEDIATE")
        do {
            while try reading({ try statement.step() }) {
                highest = max(highest, statement.int(3))
                let input = Int(statement.int(7)), output = Int(statement.int(8))
                let reasoning = Int(statement.int(9)), cacheRead = Int(statement.int(10))
                let cacheWrite = Int(statement.int(11))
                let total = statement.isNull(12) ? nil : Int(statement.int(12))
                let reasoningInsideOutput = reasoning > 0 && total == input + output + cacheRead + cacheWrite
                let event = UsageEvent(
                    harness: "opencode", accountHome: accountHome,
                    sessionId: statement.text(1) ?? "unknown", cwd: statement.text(4),
                    model: statement.text(5) ?? "unknown",
                    timestamp: Date(timeIntervalSince1970: Double(statement.int(2)) / 1_000),
                    inputTokens: max(0, input),
                    outputTokens: max(0, reasoningInsideOutput ? output - reasoning : output),
                    cacheReadTokens: max(0, cacheRead), cacheWriteTokens: max(0, cacheWrite),
                    reasoningTokens: max(0, reasoning),
                    costUSD: statement.isNull(6) || statement.double(6) <= 0 ? nil : statement.double(6))
                guard event.totalTokens > 0 else { continue }
                try store.upsert(LedgerRecord(key: "opencode:\(statement.text(0) ?? "")", event: event))
            }
            try store.setMeta(metaKey + ":watermark", String(highest))
            try store.setMeta(metaKey + ":inode", String(fingerprint.inode))
            try store.setMeta(metaKey + ":fingerprint", fingerprint.text)
            try store.db.execute("COMMIT")
        } catch {
            store.rollback()
            throw error
        }
        return true
    }

    // MARK: - Devin

    /// `~/.local/share/devin/cli/sessions.db`: usage sits on assistant
    /// `message_nodes` under `chat_message.metadata` (`request_id`,
    /// `generation_model`, `created_at`, `metrics`). Replay, compaction and
    /// forks copy nodes, so several rows share one `request_id`; that id is
    /// the key across the whole install (TokenTracker's finding). Metrics
    /// are already disjoint: `input_tokens` excludes cache reads and writes.
    ///
    /// Rows are read past a `row_id` watermark. An assistant row still
    /// waiting for its metrics holds the watermark back so it is read again
    /// once Devin fills them in, unless a later row of the same session has
    /// metrics, which means it was abandoned.
    static func scanDevin(_ url: URL, store: LedgerStore) throws -> Bool {
        guard let fingerprint = Fingerprint(url) else { return false }
        let metaKey = "devin:\(url.path)"
        if try store.meta(metaKey + ":fingerprint") == fingerprint.text { return false }
        var watermark = try watermark(metaKey, fingerprint, store: store)

        let source = try reading { try SQLiteDatabase(readOnly: url) }
        // Row ids only grow, so a highest id below the watermark means the
        // file was rebuilt in place.
        let top = try reading { try source.prepare("SELECT COALESCE(MAX(row_id), 0) FROM message_nodes") }
        if try reading({ try top.step() }), top.int(0) < watermark { watermark = 0 }
        let hasSessions = try reading { try source.prepare(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'").step() }
        let statement = try reading { try source.prepare("""
            SELECT n.row_id, n.session_id, n.created_at, \(hasSessions ? "s.working_directory" : "NULL"),
                json_extract(n.chat_message, '$.metadata.request_id'),
                json_extract(n.chat_message, '$.metadata.generation_model'),
                json_extract(n.chat_message, '$.metadata.created_at'),
                json_extract(n.chat_message, '$.metadata.metrics.input_tokens'),
                json_extract(n.chat_message, '$.metadata.metrics.output_tokens'),
                json_extract(n.chat_message, '$.metadata.metrics.cache_read_tokens'),
                json_extract(n.chat_message, '$.metadata.metrics.cache_creation_tokens')
            FROM message_nodes n \(hasSessions ? "LEFT JOIN sessions s ON s.id = n.session_id" : "")
            WHERE n.row_id > ? AND json_valid(n.chat_message)
                AND json_extract(n.chat_message, '$.role') = 'assistant'
                AND json_extract(n.chat_message, '$.metadata.request_id') IS NOT NULL
            ORDER BY n.row_id
            """) }
        statement.bind([.int(watermark)])
        let accountHome = url.deletingLastPathComponent().path
        var highest = watermark
        // Session to its earliest row still waiting for metrics.
        var pending: [String: Int64] = [:]
        try store.db.execute("BEGIN IMMEDIATE")
        do {
            while try reading({ try statement.step() }) {
                let rowID = statement.int(0)
                let session = statement.text(1) ?? "unknown"
                highest = max(highest, rowID)
                guard !statement.isNull(7), !statement.isNull(8) else {
                    if pending[session] == nil { pending[session] = rowID }
                    continue
                }
                pending[session] = nil
                let timestamp = statement.text(6).flatMap(Timestamp.parse)
                    ?? Date(timeIntervalSince1970: Double(statement.int(2)))
                let event = UsageEvent(
                    harness: "devin", accountHome: accountHome,
                    sessionId: session, cwd: statement.text(3),
                    model: statement.text(5) ?? "unknown", timestamp: timestamp,
                    inputTokens: max(0, Int(statement.int(7))), outputTokens: max(0, Int(statement.int(8))),
                    cacheReadTokens: max(0, Int(statement.int(9))), cacheWriteTokens: max(0, Int(statement.int(10))))
                guard event.totalTokens > 0, let requestID = statement.text(4) else { continue }
                try store.upsert(LedgerRecord(key: "devin:\(requestID)", event: event))
            }
            let pendingFloor = pending.values.min().map { $0 - 1 }
            try store.setMeta(metaKey + ":watermark", String(pendingFloor ?? highest))
            try store.setMeta(metaKey + ":inode", String(fingerprint.inode))
            try store.setMeta(metaKey + ":fingerprint", fingerprint.text)
            try store.db.execute("COMMIT")
        } catch {
            store.rollback()
            throw error
        }
        return true
    }
}
