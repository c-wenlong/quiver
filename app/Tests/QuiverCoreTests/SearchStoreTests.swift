import Foundation
import Testing
@testable import QuiverCore

/// The search index is a cache over the transcripts: it opens clean, rebuilds
/// when its version or file cannot be trusted, fences its writes, and
/// reclaims orphaned rows in bounded batches.
@Suite struct SearchStoreTests {
    private func store(_ temp: TempDir) throws -> SearchStore {
        try SearchStore(directory: temp.path("data/search"))
    }

    private func exists(_ url: URL, _ suffix: String = "") -> Bool {
        FileManager.default.fileExists(atPath: url.path + suffix)
    }

    private func stat(dev: Int64? = 10, ino: Int64? = 20,
                      mtimeMs: Double = 1_000, sizeBytes: Int64? = 500) -> SearchFileStat {
        SearchFileStat(dev: dev, ino: ino, mtimeMs: mtimeMs, sizeBytes: sizeBytes)
    }

    private func meta(_ store: SearchStore, _ key: String) throws -> String? {
        let statement = try store.db.prepare("SELECT value FROM meta WHERE key = ?")
        statement.bind([.text(key)])
        return try statement.step() ? statement.text(0) : nil
    }

    private func ftsMatches(_ store: SearchStore, _ query: String) throws -> [Int64] {
        let statement = try store.db.prepare(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rowid")
        statement.bind([.text(query)])
        var ids: [Int64] = []
        while try statement.step() { ids.append(statement.int(0)) }
        return ids
    }

    private let fields = SearchSessionFields(
        agent: "claude", sessionID: "abc-123", filePath: "/t/one.jsonl",
        title: "chat", cwd: "/work", cwdKey: "/work",
        createdAt: "2026-10-01T09:00:00Z", updatedAt: "2026-10-01T10:00:00Z",
        messageCount: 2)

    @Test func createsTheSchemaOnFirstOpen() throws {
        let temp = try TempDir()
        let store = try store(temp)

        #expect(store.fts5Available)
        #expect(store.url.lastPathComponent == "search.sqlite")
        for name in ["meta", "sessions", "files", "messages", "messages_fts"] {
            let found = try store.db.scalar(
                "SELECT count(*) FROM sqlite_master WHERE name = '\(name)'")
            #expect(found == 1, "\(name) is missing")
        }
        #expect(try meta(store, "schema_version") == "1")
        #expect(try meta(store, "index_incarnation") != nil)

        // The index is WAL with incremental vacuum enabled.
        let journal = try store.db.prepare("PRAGMA journal_mode")
        #expect(try journal.step() && journal.text(0) == "wal")
        #expect(try store.db.scalar("PRAGMA auto_vacuum") == 2)

        // Reopening keeps the same incarnation and every row.
        var again: SearchStore? = try self.store(temp)
        #expect(try meta(again!, "index_incarnation") == meta(store, "index_incarnation"))
        again = nil
    }

    @Test func rebuildsWhenTheSchemaVersionDoesNotMatch() throws {
        let temp = try TempDir()
        var store: SearchStore? = try self.store(temp)
        try store!.upsertFile(path: "/t/one.jsonl", stat: stat(), byteOffset: 42, sessionRowID: nil)
        try store!.db.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        store = nil

        let reopened = try self.store(temp)

        // The stale index was unlinked and recreated, not migrated.
        #expect(try reopened.fileRow(for: "/t/one.jsonl") == nil)
        #expect(try reopened.status() == SearchStatus())
        #expect(try meta(reopened, "schema_version") == "1")
    }

    @Test func rebuildsAnUnreadableDatabaseFile() throws {
        let temp = try TempDir()
        let url = temp.path("data/search/search.sqlite")
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("this is not sqlite".utf8).write(to: url)

        let store = try self.store(temp)

        #expect(try meta(store, "schema_version") == "1")
        #expect(try store.status() == SearchStatus())
    }

    @Test func fileCursorRoundTripsIncludingThePartialSentinel() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/one.jsonl"

        try store.upsertFile(path: path, stat: stat(), byteOffset: 256, sessionRowID: nil)
        var row = try #require(try store.fileRow(for: path))
        #expect(row == SearchFileRow(
            path: path, dev: 10, ino: 20, byteOffset: 256, mtimeMs: 1_000,
            sizeBytes: 500, sessionRowID: nil, state: .current,
            failCount: 0, failedMtimeMs: nil))

        // The -1 sentinel round-trips: a chunk of a read that never finished.
        try store.upsertFile(path: path, stat: stat(mtimeMs: 2_000, sizeBytes: 900),
                             byteOffset: SearchStore.partialFileOffset, sessionRowID: nil)
        row = try #require(try store.fileRow(for: path))
        #expect(row.byteOffset == SearchStore.partialFileOffset)
        #expect(row.mtimeMs == 2_000)

        // A partial observation keeps the dev/ino pair a real stat proved.
        try store.upsertFile(path: path, stat: stat(dev: nil, ino: nil),
                             byteOffset: 300, sessionRowID: nil)
        row = try #require(try store.fileRow(for: path))
        #expect(row.dev == 10 && row.ino == 20)

        #expect(try store.files().map(\.path) == [path])
    }

    @Test func fencedCommitWritesMessagesAndAdvancesTheCursor() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/one.jsonl"
        let session = try store.createSession(fields)
        try store.upsertFile(path: path, stat: stat(), byteOffset: 128, sessionRowID: session)

        let written = try store.commitFile(
            path: path, expected: SearchFileFence(sessionRowID: session, byteOffset: 128),
            stat: stat(mtimeMs: 1_500, sizeBytes: 900), byteOffset: 640,
            session: .existing(session),
            messages: [
                SearchMessageRow(role: "user", ts: "2026-10-01T09:00:00Z", userText: "hello world"),
                SearchMessageRow(role: "assistant", ts: "2026-10-01T09:00:01Z", assistantText: "hi there"),
                SearchMessageRow(role: "tool", toolText: "ran ls", identifiers: "ls"),
            ])

        #expect(written)
        let row = try #require(try store.fileRow(for: path))
        #expect(row.byteOffset == 640 && row.sessionRowID == session && row.mtimeMs == 1_500)
        #expect(try store.db.scalar("SELECT count(*) FROM messages") == 3)
        #expect(try store.db.scalar("SELECT count(*) FROM messages_fts") == 3)

        // The FTS rowid is the messages row id, so a hit names the message row.
        let hits = try ftsMatches(store, "hello")
        #expect(hits.count == 1)
        let worldHits = try ftsMatches(store, "world")
        #expect(hits == worldHits)
        let messageID = try store.db.scalar("SELECT id FROM messages WHERE role = 'user'")
        #expect(hits[0] == messageID)
    }

    @Test func aMovedFileRecordFencesTheCommit() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/one.jsonl"
        let session = try store.createSession(fields)
        try store.upsertFile(path: path, stat: stat(), byteOffset: 100, sessionRowID: session)

        // The cursor moved under the read: nothing lands.
        #expect(try store.commitFile(
            path: path, expected: SearchFileFence(sessionRowID: session, byteOffset: 200),
            stat: stat(), byteOffset: 300, session: .existing(session),
            messages: [SearchMessageRow(role: "user", userText: "hello")]) == false)
        // A first read that expected no row is fenced the same way.
        #expect(try store.commitFile(
            path: path, expected: nil,
            stat: stat(), byteOffset: 300, session: .existing(session), messages: []) == false)

        #expect(try store.db.scalar("SELECT count(*) FROM messages") == 0)
        #expect(try store.fileRow(for: path)?.byteOffset == 100)
    }

    @Test func commitFileUpdatesTheSessionInTheSameTransaction() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/one.jsonl"
        let session = try store.createSession(SearchSessionFields(agent: "claude", filePath: path))

        #expect(try store.commitFile(
            path: path, expected: nil, stat: stat(), byteOffset: 10,
            session: .existing(session), messages: [SearchMessageRow(role: "user", userText: "hi")],
            updating: fields))

        #expect(try store.sessionRow(session)?.fields == fields)
    }

    @Test func updateSessionFillsInAProvisionalRow() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let id = try store.createSession(SearchSessionFields(agent: "claude", filePath: "/t/one.jsonl"))
        #expect(try store.sessionRow(id)?.fields.title == "")

        try store.updateSession(id, fields)

        #expect(try store.sessionRow(id)?.fields == fields)
    }

    @Test func replaceSessionCutsTheOldGenerationLoose() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/one.jsonl"
        let old = try store.createSession(fields)
        try store.upsertFile(path: path, stat: stat(), byteOffset: 10, sessionRowID: old)
        try store.commitFile(
            path: path, expected: SearchFileFence(sessionRowID: old, byteOffset: 10),
            stat: stat(), byteOffset: 20, session: .existing(old),
            messages: [
                SearchMessageRow(role: "user", userText: "hello"),
                SearchMessageRow(role: "assistant", assistantText: "hi"),
            ])

        let fresh = try #require(try store.replaceSession(
            for: path, expected: SearchFileFence(sessionRowID: old, byteOffset: 20),
            fields: fields))

        #expect(fresh != old)
        #expect(try store.fileRow(for: path)?.sessionRowID == fresh)
        // Only the old sessions row is gone; its messages wait for the drain.
        #expect(try store.sessionRow(old) == nil)
        #expect(try store.sessionRow(fresh) != nil)
        #expect(try store.db.scalar("SELECT count(*) FROM messages") == 2)
        #expect(try ftsMatches(store, "hello").count == 1)

        // A stale expectation does not swap sessions.
        #expect(try store.replaceSession(
            for: path, expected: SearchFileFence(sessionRowID: old, byteOffset: 20),
            fields: fields) == nil)
    }

    @Test func drainReclaimsOrphanedMessagesInBoundedBatches() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/one.jsonl"
        let old = try store.createSession(fields)
        try store.commitFile(
            path: path, expected: nil, stat: stat(), byteOffset: 20, session: .existing(old),
            messages: (0..<300).map { SearchMessageRow(role: "user", userText: "word\($0)") })
        _ = try store.replaceSession(
            for: path, expected: SearchFileFence(sessionRowID: old, byteOffset: 20),
            fields: fields)

        #expect(try store.drainOrphanedMessages() == 256)
        #expect(try store.drainOrphanedMessages() == 44)
        #expect(try store.drainOrphanedMessages() == 0)
        #expect(try store.status().messageCount == 0)
        #expect(try store.db.scalar("SELECT count(*) FROM messages_fts") == 0)
    }

    @Test func setFileStateTracksFailuresAndResetsThem() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let path = "/t/bad.jsonl"

        // A file the index never held still records the failure.
        try store.setFileState(path, .failed, atMtimeMs: 7)
        try store.setFileState(path, .failed, atMtimeMs: 7)
        var row = try #require(try store.fileRow(for: path))
        #expect(row.state == .failed && row.failCount == 2 && row.failedMtimeMs == 7)

        try store.setFileState(path, .current)
        row = try #require(try store.fileRow(for: path))
        #expect(row.state == .current && row.failCount == 0 && row.failedMtimeMs == nil)

        try store.setFileState(path, .due)
        #expect(try store.fileRow(for: path)?.state == .due)
    }

    @Test func statusCountsFilesSessionsAndMessages() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let session = try store.createSession(fields)
        try store.commitFile(
            path: "/t/one.jsonl", expected: nil, stat: stat(), byteOffset: 20,
            session: .existing(session),
            messages: [SearchMessageRow(role: "user", userText: "hi"),
                       SearchMessageRow(role: "assistant", assistantText: "hey")])
        try store.setFileState("/t/bad.jsonl", .failed, atMtimeMs: 3)

        let status = try store.status()

        #expect(status.filesByState == [.current: 1, .failed: 1])
        #expect(status.sessionsByAgent == ["claude": 1])
        #expect(status.sessionCount == 1)
        #expect(status.messageCount == 2)
    }

    @Test func clearRemovesEveryDatabaseFile() throws {
        let temp = try TempDir()
        let store = try store(temp)
        let url = store.url
        try store.upsertFile(path: "/t/one.jsonl", stat: stat(), byteOffset: 1, sessionRowID: nil)

        store.clear()

        for suffix in ["", "-wal", "-shm", "-journal"] {
            #expect(!exists(url, suffix), "search.sqlite\(suffix) survived clear()")
        }
    }
}
