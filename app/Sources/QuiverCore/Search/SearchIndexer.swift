import Foundation

/// What one file's next read owes the search index — the whole decide step,
/// as a function of the `files` row and the ledger's cursor position.
///
/// Adapted from Orca's `session-search-read-decision.ts`: the same inputs
/// give the same answer on the first pass after a restart as on the
/// hundredth, which is what lets a scan end mid-pass with nothing to record.
public enum SearchReadNeed: Sendable, Equatable {
    /// Never read: an excluded path, or held out after repeated failures.
    case skip
    /// The index's rows already match the file at this stat.
    case current
    /// The index cursor is exactly where the ledger resumes: the index takes
    /// the same appended lines.
    case append
    /// Read from the start: nothing held, behind a span no append reaches,
    /// or marked `due`.
    case whole
}

/// The search index as a consumer of the ledger's transcript scan — the
/// second consumer that lets one read of each transcript feed both the usage
/// ledger and the FTS index. Adapted from Orca's
/// `session-search-index-consumer.ts`.
///
/// A class, not an actor, and not thread-safe: it only ever runs inside the
/// `Ledger` actor's `scan` calls, which is also what makes the single
/// `pending` slot safe — a scan reads one file at a time. `@unchecked
/// Sendable` so the instance can cross into the actor that owns every call.
public final class SearchIndexer: @unchecked Sendable {
    /// Failures at one unchanged mtime before a file is left alone — Orca's
    /// `SESSION_SEARCH_FAILURES_BEFORE_HELD_OUT`. Three rather than one,
    /// because a single failure is often a transcript being rewritten under
    /// the read; the stat itself is the whole release condition, so no timer
    /// is kept.
    static let failuresBeforeHoldout = 3
    /// `finishScan` drains at most this many batches of orphaned messages.
    static let drainBatchLimit = 64

    let store: SearchStore

    /// The file currently being decoded — set by `beginFile`, committed by
    /// `finishFile`, dropped by `failFile`. One scan reads one file at a
    /// time, so one slot is the whole in-flight set.
    private var pending: Pending?

    private struct Pending {
        var path: String
        var harness: SearchDecoder.Harness
        /// This read covers only the tail past the `files` row's offset; a
        /// whole read instead starts a new session generation.
        var appending: Bool
        /// The `files` row as the read began — what every commit fences on.
        var fileRow: SearchFileRow?
        var decoder: SearchDecoder
        var messages: [SearchMessage] = []
        var hints = SearchHints()
        /// First and last decoded timestamps — the session's time range.
        var firstTs: String?
        var lastTs: String?
        /// Decoded messages so far, pre-chunk — the session's messageCount.
        var decoded = 0
    }

    public init(store: SearchStore) {
        self.store = store
    }

    /// Paths that never enter the index: Claude `subagents/` transcripts are
    /// a session's internal delegation chatter (Orca skips them too). The
    /// ledger still counts their tokens.
    public func shouldIndex(path: String) -> Bool {
        !path.contains("/subagents/")
    }

    /// What the next read of `path` owes the index. `ledgerOffset` is where
    /// the ledger's own cursor sits, or nil when the ledger never read this
    /// file — index-pure sources (devin transcripts) pass nil.
    public func readNeed(path: String, stat: SearchFileStat, ledgerOffset: UInt64?) -> SearchReadNeed {
        guard shouldIndex(path: path) else { return .skip }
        guard let row = try? store.fileRow(for: path) else { return .whole }
        // Held out: failed enough times at exactly this stat. Only a change
        // to the stat itself releases it — a file nobody touches is never
        // read again.
        if row.state == .failed, row.failCount >= Self.failuresBeforeHoldout,
           row.failedMtimeMs == stat.mtimeMs {
            return .skip
        }
        // `.due` is behind on a span no append reaches; the partial sentinel
        // marks a chunked read's prefix that nothing may continue.
        if row.state == .due || row.byteOffset == SearchStore.partialFileOffset {
            return .whole
        }
        if let size = stat.sizeBytes, row.state == .current,
           row.byteOffset == size, row.sizeBytes == size, row.mtimeMs == stat.mtimeMs {
            return .current
        }
        // The index cursor is exactly where the ledger resumes, and the file
        // grew past it: the index takes the same lines the ledger appends.
        if let ledgerOffset, let size = stat.sizeBytes,
           UInt64(bitPattern: row.byteOffset) == ledgerOffset, row.byteOffset < size {
            return .append
        }
        // Anything else — a stat that moved, or an index behind a span it
        // cannot reach, like a ledger offset ahead of the index's — is read
        // whole.
        return .whole
    }

    /// Starts accumulating one file's decode. The decoder is always fresh —
    /// no decoder state is persisted in v1, so an append loses join fidelity
    /// (a devin tool result may not name the call it answers); accepted.
    /// - Parameter appending: whether this read covers only the tail past the
    ///   `files` row's offset. `finishFile` commits a tail against the row's
    ///   session and a whole read as a new session generation.
    public func beginFile(path: String, harness: SearchDecoder.Harness, appending: Bool = false) {
        pending = Pending(
            path: path, harness: harness, appending: appending,
            fileRow: try? store.fileRow(for: path), decoder: SearchDecoder(harness))
    }

    /// One transcript line — for devin, a whole `transcripts/*.json`
    /// document, since those files are pretty-printed.
    public func consume(line: Data) {
        guard pending != nil else { return }
        let output = pending!.decoder.decode(line: line)
        // Hints arrive repeatedly across a file: keep the latest non-nil.
        // That lands the explicit title wherever its record sits.
        if let value = output.hints.sessionId { pending!.hints.sessionId = value }
        if let value = output.hints.cwd { pending!.hints.cwd = value }
        if let value = output.hints.branch { pending!.hints.branch = value }
        if let value = output.hints.title { pending!.hints.title = value }
        pending!.hints.nonUserOrigin = pending!.hints.nonUserOrigin || output.hints.nonUserOrigin
        for message in output.messages {
            if let ts = message.ts {
                if pending!.firstTs == nil { pending!.firstTs = ts }
                pending!.lastTs = ts
            }
            pending!.messages.append(message)
        }
        pending!.decoded += output.messages.count
    }

    /// Commits the read in the store's per-file transaction, fenced on the
    /// `files` row as `beginFile` saw it. Returns false when the record moved
    /// under the read — nothing was written and the next pass decides again.
    ///
    /// A whole read starts a new session generation: `replaceSession` swaps
    /// the rows in one transaction, so the old conversation stops answering
    /// at once and `finishScan`'s drain reclaims its messages later. An
    /// append keeps the row's session and merges fields onto it.
    @discardableResult
    public func finishFile(path: String, stat: SearchFileStat, byteOffset: Int64) throws -> Bool {
        guard let file = pending, file.path == path else {
            pending = nil
            // A file the index never began still records as read, so a
            // transcript with nothing to decode is not retried forever.
            return try store.commitFile(
                path: path, expected: try? store.fileFence(for: path), stat: stat,
                byteOffset: byteOffset, sessionRowID: nil, messages: [])
        }
        defer { pending = nil }

        let rows = Self.messageRows(from: file.messages)
        var fields = SearchSessionFields(
            agent: file.harness.rawValue,
            sessionID: file.hints.sessionId ?? Self.stem(of: path),
            filePath: path, title: file.hints.title ?? "",
            cwd: file.hints.cwd, cwdKey: file.hints.cwd,
            createdAt: file.firstTs, updatedAt: file.lastTs,
            messageCount: file.decoded)
        // A session worth writing is an explicit session id or any message; a
        // file that decodes to neither gets only its cursor committed.
        let hasSession = file.hints.sessionId != nil || !file.messages.isEmpty
        let old = file.fileRow
        let fence = old.map { SearchFileFence(sessionRowID: $0.sessionRowID, byteOffset: $0.byteOffset) }

        if file.appending, let old {
            if let sessionRowID = old.sessionRowID {
                // The read covered only the tail; the rows continue the row's
                // session. Fields merge onto it: counts add, decoded values
                // win, and the stored ones fill what this span never saw.
                if let existing = try? store.sessionRow(sessionRowID)?.fields {
                    fields.sessionID = file.hints.sessionId ?? existing.sessionID
                    fields.title = file.hints.title ?? existing.title
                    fields.cwd = file.hints.cwd ?? existing.cwd
                    fields.cwdKey = fields.cwd
                    fields.createdAt = existing.createdAt ?? fields.createdAt
                    fields.updatedAt = fields.updatedAt ?? existing.updatedAt
                    fields.messageCount += existing.messageCount
                }
                return try commit(path: path, expected: fence, stat: stat, byteOffset: byteOffset,
                                  sessionRowID: sessionRowID, messages: rows, updating: fields,
                                  prior: old)
            }
            guard hasSession else {
                return try commit(path: path, expected: fence, stat: stat, byteOffset: byteOffset,
                                  sessionRowID: nil, messages: [], prior: old)
            }
            let sessionRowID = try store.createSession(fields)
            return try commit(path: path, expected: fence, stat: stat, byteOffset: byteOffset,
                              sessionRowID: sessionRowID, messages: rows, updating: fields,
                              prior: old)
        }

        return try commitWholeRead(
            path: path, stat: stat, byteOffset: byteOffset,
            fields: fields, rows: rows, hasSession: hasSession, old: old)
    }

    /// Commits one session read whole out of a container — the synthetic
    /// counterpart of `finishFile`, for sources like OpenCode's
    /// `opencode.db` whose sessions are rows rather than files. `fields`
    /// comes from the container's own session row, `messages` from its
    /// transcript rows; `byteOffset` carries the synthetic size, so the
    /// `.current` check (offset == size) can hold on a later pass.
    @discardableResult
    public func finishSession(
        path: String, stat: SearchFileStat,
        fields: SearchSessionFields, messages: [SearchMessage]
    ) throws -> Bool {
        try commitWholeRead(
            path: path, stat: stat, byteOffset: stat.sizeBytes ?? 0,
            fields: fields, rows: Self.messageRows(from: messages),
            hasSession: true, old: try? store.fileRow(for: path))
    }

    /// A read that never committed. Counted against the mtime it failed at:
    /// three failures at the same stat hold the file out until the stat
    /// itself moves.
    public func failFile(path: String, mtimeMs: Double) {
        if pending?.path == path { pending = nil }
        guard shouldIndex(path: path) else { return }
        try? store.setFileState(path, .failed, atMtimeMs: mtimeMs)
    }

    /// Reclaims messages orphaned by session replacements, in bounded batches
    /// so a scan's tail never stalls on one giant delete.
    public func finishScan() throws {
        for _ in 0..<Self.drainBatchLimit {
            if try store.drainOrphanedMessages(limit: 256) == 0 { break }
        }
    }

    /// `SearchChunker.rows` mapped to store rows — the one text column the
    /// role searches under plus identifier shadow text. Shared by
    /// `finishFile` and `finishSession`.
    private static func messageRows(from messages: [SearchMessage]) -> [SearchMessageRow] {
        SearchChunker.rows(from: messages).map { chunk -> SearchMessageRow in
            var row = SearchMessageRow(
                role: chunk.role.rawValue, ts: chunk.ts,
                identifiers: identifierShadowText(chunk.text))
            switch chunk.role {
            case .user: row.userText = chunk.text
            case .assistant: row.assistantText = chunk.text
            case .tool: row.toolText = chunk.text
            }
            return row
        }
    }

    /// The whole-read commit `finishFile` and `finishSession` share: a row
    /// that already holds a session gets a new generation (`replaceSession`
    /// swaps it in one transaction), a first session is created, and a read
    /// that found none commits only its cursor.
    private func commitWholeRead(
        path: String, stat: SearchFileStat, byteOffset: Int64,
        fields: SearchSessionFields, rows: [SearchMessageRow],
        hasSession: Bool, old: SearchFileRow?
    ) throws -> Bool {
        let fence = old.map { SearchFileFence(sessionRowID: $0.sessionRowID, byteOffset: $0.byteOffset) }
        if let old, old.sessionRowID != nil {
            guard let sessionRowID = try store.replaceSession(for: path, expected: fence, fields: fields)
            else { return false }
            return try commit(
                path: path,
                expected: SearchFileFence(sessionRowID: sessionRowID, byteOffset: old.byteOffset),
                stat: stat, byteOffset: byteOffset,
                sessionRowID: sessionRowID, messages: rows, updating: fields,
                prior: old)
        }
        guard hasSession else {
            return try commit(path: path, expected: fence, stat: stat, byteOffset: byteOffset,
                              sessionRowID: nil, messages: [], prior: old)
        }
        let sessionRowID = try store.createSession(fields)
        return try commit(path: path, expected: fence, stat: stat, byteOffset: byteOffset,
                          sessionRowID: sessionRowID, messages: rows, updating: fields,
                          prior: old)
    }

    /// `commitFile` plus what it does not do: move the row's state on. A
    /// successful commit clears `failed`/`due`, so a recovered file stops
    /// re-reading on every pass.
    private func commit(path: String, expected: SearchFileFence?, stat: SearchFileStat,
                        byteOffset: Int64, sessionRowID: Int64?, messages: [SearchMessageRow],
                        updating fields: SearchSessionFields? = nil, prior: SearchFileRow?) throws -> Bool {
        guard try store.commitFile(
            path: path, expected: expected, stat: stat, byteOffset: byteOffset,
            sessionRowID: sessionRowID, messages: messages, updating: fields)
        else { return false }
        if let prior, prior.state != .current {
            try? store.setFileState(path, .current)
        }
        return true
    }

    /// The session id a file reports when no line carried one.
    private static func stem(of path: String) -> String {
        URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
    }
}
