import Foundation
import Testing
@testable import QuiverCore

/// OpenCode sessions live inside `opencode.db`, not in transcript files:
/// each indexes under a synthetic `<db>#<id>` path whose stat is the
/// session's last-activity time and message count, and is read whole on
/// every pass that owes it one.
@Suite struct SearchOpenCodeTests {
    /// The schema the reads name — `session`, `message`, `part` as a real
    /// opencode.db declares them, trimmed to the columns the index and the
    /// token scan rely on.
    private static let schema = """
        CREATE TABLE `session` (`id` text PRIMARY KEY, `parent_id` text,
          `directory` text NOT NULL, `title` text NOT NULL,
          `time_created` integer NOT NULL, `time_updated` integer NOT NULL,
          `time_archived` integer);
        CREATE TABLE `message` (`id` text PRIMARY KEY, `session_id` text NOT NULL,
          `time_created` integer NOT NULL, `time_updated` integer NOT NULL,
          `data` text NOT NULL);
        CREATE TABLE `part` (`id` text PRIMARY KEY, `message_id` text NOT NULL,
          `session_id` text NOT NULL, `time_created` integer NOT NULL,
          `time_updated` integer NOT NULL, `data` text NOT NULL);
        CREATE INDEX `message_session_time_created_id_idx`
          ON `message` (`session_id`,`time_created`,`id`);
        CREATE INDEX `part_message_id_id_idx` ON `part` (`message_id`,`id`);
        """

    /// A temp-dir opencode.db plus the ledger and index wired to it.
    private struct Fixture {
        let temp: TempDir
        let ledger: Ledger
        let store: SearchStore
        let source: SQLiteDatabase
        let db: URL
        /// The path the scan keys on — temp dirs sit under /var, a symlink.
        var dbPath: String { db.resolvingSymlinksInPath().path }
        var roots: LedgerRoots { LedgerRoots(openCodeDatabases: [db]) }

        init() throws {
            temp = try TempDir()
            db = temp.path("opencode/opencode.db")
            try FileManager.default.createDirectory(
                at: db.deletingLastPathComponent(), withIntermediateDirectories: true)
            source = try SQLiteDatabase(url: db)
            try source.execute(SearchOpenCodeTests.schema)
            ledger = try Ledger(directory: temp.path("cache"), pricing: .empty)
            store = try SearchStore(directory: temp.path("data/search"))
        }

        func attach() async { await ledger.setIndexer(SearchIndexer(store: store)) }
        func scan() async throws -> ScanReport { try await ledger.scan(roots: roots) }

        func addSession(_ id: String, title: String, directory: String = "/tmp/oc",
                        created: Int64, updated: Int64) throws {
            try source.prepare("INSERT INTO session VALUES (?, NULL, ?, ?, ?, ?, NULL)")
                .bind([.text(id), .text(directory), .text(title),
                       .int(created), .int(updated)])
                .run()
        }

        func addMessage(_ id: String, session: String, role: String, created: Int64) throws {
            try source.prepare("INSERT INTO message VALUES (?, ?, ?, ?, ?)")
                .bind([.text(id), .text(session), .int(created), .int(created),
                       .text(#"{"role":"\#(role)","time":{"created":\#(created)}}"#)])
                .run()
        }

        func addPart(_ id: String, message: String, session: String,
                     created: Int64, data: String) throws {
            try source.prepare("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)")
                .bind([.text(id), .text(message), .text(session),
                       .int(created), .int(created), .text(data)])
                .run()
        }

        /// Two sessions: `ses_1` with user text, a reasoning+text assistant
        /// turn and a completed tool call; `ses_2` with a user text and a
        /// failed tool call (`filePath` spelled OpenCode's way).
        func seedTwoSessions() throws {
            try addSession("ses_1", title: "Fix the flaky test", directory: "/tmp/oc1",
                           created: 1_791_100_000_000, updated: 1_791_100_003_000)
            try addMessage("m_u1", session: "ses_1", role: "user", created: 1_791_100_000_000)
            try addPart("p_u1", message: "m_u1", session: "ses_1", created: 1_791_100_000_000,
                        data: #"{"type":"text","text":"fix the flaky zebrafish test"}"#)
            try addMessage("m_a1", session: "ses_1", role: "assistant", created: 1_791_100_001_000)
            try addPart("p_a1", message: "m_a1", session: "ses_1", created: 1_791_100_001_000,
                        data: #"{"type":"reasoning","text":"reasoning about the quokka bug"}"#)
            try addPart("p_a2", message: "m_a1", session: "ses_1", created: 1_791_100_001_500,
                        data: #"{"type":"text","text":"found the flake"}"#)
            try addPart("p_a3", message: "m_a1", session: "ses_1", created: 1_791_100_002_000,
                        data: #"{"type":"tool","tool":"bash","state":{"status":"completed","input":{"command":"swift test --filter FlakyTest"},"output":"one test passed"}}"#)
            try addMessage("m_u2", session: "ses_1", role: "user", created: 1_791_100_003_000)
            try addPart("p_u2", message: "m_u2", session: "ses_1", created: 1_791_100_003_000,
                        data: #"{"type":"text","text":"thanks axolotl"}"#)

            try addSession("ses_2", title: "Deploy otter service", directory: "/tmp/oc2",
                           created: 1_791_200_000_000, updated: 1_791_200_001_000)
            try addMessage("m_b1", session: "ses_2", role: "user", created: 1_791_200_000_000)
            try addPart("p_b1", message: "m_b1", session: "ses_2", created: 1_791_200_000_000,
                        data: #"{"type":"text","text":"deploy the capybara service"}"#)
            try addMessage("m_b2", session: "ses_2", role: "assistant", created: 1_791_200_001_000)
            try addPart("p_b2", message: "m_b2", session: "ses_2", created: 1_791_200_001_000,
                        data: #"{"type":"tool","tool":"read","state":{"status":"error","input":{"filePath":"/repo/deploy.yml"},"error":"Error: File not found"}}"#)
        }

        /// OpenCode 2.x's tables: new sessions only in `session_v2`, every
        /// turn one `session_message` row holding its whole content.
        func addV2Tables() throws {
            try source.execute("""
                CREATE TABLE `session_v2` (`id` text PRIMARY KEY, `parent_id` text,
                  `directory` text NOT NULL, `title` text,
                  `time_created` integer NOT NULL, `time_updated` integer NOT NULL,
                  `time_archived` integer);
                CREATE TABLE `session_message` (`id` text PRIMARY KEY, `session_id` text NOT NULL,
                  `type` text NOT NULL, `seq` integer NOT NULL, `time_created` integer NOT NULL,
                  `time_updated` integer NOT NULL, `data` text NOT NULL);
                """)
        }

        func addV2Session(_ id: String, title: String?, directory: String,
                          created: Int64, updated: Int64) throws {
            try source.prepare("INSERT INTO session_v2 VALUES (?, NULL, ?, ?, ?, ?, NULL)")
                .bind([.text(id), .text(directory), title.map { .text($0) } ?? .null,
                       .int(created), .int(updated)])
                .run()
        }

        func addV2Row(_ id: String, session: String, type: String, seq: Int64,
                      created: Int64, data: String) throws {
            try source.prepare("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)")
                .bind([.text(id), .text(session), .text(type), .int(seq),
                       .int(created), .int(created), .text(data)])
                .run()
        }

        func fts(_ query: String) throws -> [Int64] {
            try store.ftsRowIDs(query)
        }
    }

    @Test func aScanIndexesEverySessionInTheDatabase() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        await f.attach()

        let report = try await f.scan()

        #expect(report.databasesRead == 1)
        #expect(report.filesIndexed == 2)
        #expect(report.notes.isEmpty)
        // User, reasoning-folded-into-turn and tool text all answer.
        #expect(try f.fts("zebrafish").count == 1)
        #expect(try f.fts("quokka").count == 1)
        #expect(try f.fts("capybara").count == 1)
        #expect(try f.fts("passed").count == 1)
        // A file-path tool argument hits through identifier pieces.
        #expect(try f.fts("deploy").count >= 1)
        // Synthetic paths, one per session — never a real file's spelling.
        let paths = Set(try f.store.files().map(\.path))
        #expect(paths == [f.dbPath + "#ses_1", f.dbPath + "#ses_2"])
        #expect(try f.store.fileRow(for: f.dbPath) == nil)

        let row = try #require(try f.store.fileRow(for: f.dbPath + "#ses_1"))
        #expect(row.state == .current)
        #expect(row.byteOffset == 3)            // the session's message count
        #expect(row.sizeBytes == 3)
        #expect(row.mtimeMs == 1_791_100_003_000)
        let session = try #require(try f.store.sessionRow(row.sessionRowID ?? -1))
        #expect(session.fields.agent == "opencode")
        #expect(session.fields.sessionID == "ses_1")
        #expect(session.fields.filePath == f.dbPath + "#ses_1")
        #expect(session.fields.title == "Fix the flaky test")
        #expect(session.fields.cwd == "/tmp/oc1")
        #expect(session.fields.createdAt == SearchOpenCode.iso(ms: 1_791_100_000_000))
        #expect(session.fields.updatedAt == SearchOpenCode.iso(ms: 1_791_100_003_000))
        // Two user turns, one folded assistant turn, one tool row.
        #expect(session.fields.messageCount == 4)
    }

    /// A session OpenCode 2.x made lives only in `session_v2` and
    /// `session_message`; one the migration copied there stays a legacy
    /// session, its copied turns read once.
    @Test func openCode2SessionsIndexFromTheV2Tables() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        try f.addV2Tables()
        // ses_1 as the 2.x migration left it: a v2 row with no title and a
        // copied turn under its legacy id.
        try f.addV2Session("ses_1", title: nil, directory: "/tmp/oc1",
                           created: 1_791_100_000_000, updated: 1_791_100_003_000)
        try f.addV2Row("m_u1", session: "ses_1", type: "user", seq: 0, created: 1_791_100_000_000,
                       data: #"{"text":"fix the flaky zebrafish test"}"#)
        try f.addV2Session("ses_v2", title: "Greeting", directory: "/tmp/v2",
                           created: 1_791_300_000_000, updated: 1_791_300_002_000)
        try f.addV2Row("v_u1", session: "ses_v2", type: "user", seq: 4, created: 1_791_300_000_000,
                       data: #"{"text":"say hello to the pangolin","files":[]}"#)
        try f.addV2Row("v_s1", session: "ses_v2", type: "system", seq: 5, created: 1_791_300_000_100,
                       data: #"{"text":"Instructions updated: narwhal"}"#)
        try f.addV2Row("v_a1", session: "ses_v2", type: "assistant", seq: 7, created: 1_791_300_001_000,
                       data: #"{"finish":"stop","content":[{"type":"reasoning","text":"thinking about tapirs"},{"type":"text","text":"hello there"},{"type":"tool","id":"c1","name":"shell","state":{"status":"completed","input":{"command":"echo okapi"},"content":[{"type":"text","text":"okapi printed"}]}}]}"#)
        try f.addV2Row("v_i1", session: "ses_v2", type: "idle", seq: 12, created: 1_791_300_002_000,
                       data: #"{"outcome":"succeeded"}"#)
        await f.attach()

        let report = try await f.scan()

        #expect(report.notes.isEmpty)
        #expect(report.filesIndexed == 3)
        #expect(try f.fts("pangolin").count == 1)
        #expect(try f.fts("tapirs").count == 1)
        #expect(try f.fts("okapi").count == 1)
        #expect(try f.fts("narwhal").isEmpty)
        // The copied turn is not indexed a second time.
        #expect(try f.fts("zebrafish").count == 1)
        let ses1 = try #require(try f.store.fileRow(for: f.dbPath + "#ses_1"))
        #expect(ses1.sizeBytes == 3)
        #expect(try f.store.sessionRow(ses1.sessionRowID ?? -1)?.fields.title == "Fix the flaky test")

        let row = try #require(try f.store.fileRow(for: f.dbPath + "#ses_v2"))
        #expect(row.sizeBytes == 4)
        #expect(row.mtimeMs == 1_791_300_002_000)
        let session = try #require(try f.store.sessionRow(row.sessionRowID ?? -1))
        #expect(session.fields.title == "Greeting")
        #expect(session.fields.cwd == "/tmp/v2")
        // The prompt, the folded assistant turn and its tool call.
        #expect(session.fields.messageCount == 3)
    }

    @Test func anUnchangedDatabaseDoesNotReindex() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        await f.attach()
        _ = try await f.scan()
        let before = try f.store.files().map(\.sessionRowID)

        let report = try await f.scan()

        #expect(report.filesIndexed == 0)
        #expect(try f.store.files().map(\.sessionRowID) == before)
        #expect(try f.store.status().messageCount == 6)
    }

    @Test func aSessionThatGrewReindexesAlone() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        await f.attach()
        _ = try await f.scan()
        let ses1 = try #require(try f.store.fileRow(for: f.dbPath + "#ses_1"))
        let ses2 = try #require(try f.store.fileRow(for: f.dbPath + "#ses_2"))

        // ses_1 gains a turn; its activity time moves with it. ses_2 is
        // untouched.
        try f.addMessage("m_u3", session: "ses_1", role: "user", created: 1_791_100_004_000)
        try f.addPart("p_u3", message: "m_u3", session: "ses_1", created: 1_791_100_004_000,
                      data: #"{"type":"text","text":"wombat followup"}"#)
        try f.source.prepare("UPDATE session SET time_updated = ? WHERE id = 'ses_1'")
            .bind([.int(1_791_100_004_000)]).run()

        let report = try await f.scan()

        #expect(report.filesIndexed == 1)
        let after = try #require(try f.store.fileRow(for: f.dbPath + "#ses_1"))
        // The whole read minted a new session generation; the old is gone.
        #expect(after.sessionRowID != ses1.sessionRowID)
        #expect(try f.store.sessionRow(ses1.sessionRowID ?? -1) == nil)
        #expect(after.byteOffset == 4)
        #expect(try f.store.sessionRow(after.sessionRowID ?? -1)?.fields.messageCount == 5)
        // ses_2 kept its generation and was not re-read.
        #expect(try f.store.fileRow(for: f.dbPath + "#ses_2")?.sessionRowID == ses2.sessionRowID)
        #expect(try f.fts("wombat").count == 1)
        #expect(try f.store.status().messageCount == 7)
    }

    @Test func aMissingDatabaseReadsNothing() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"), pricing: .empty)
        let store = try SearchStore(directory: temp.path("data/search"))
        await ledger.setIndexer(SearchIndexer(store: store))

        let report = try await ledger.scan(roots: LedgerRoots(
            openCodeDatabases: [temp.path("no/such/opencode.db")]))

        // The file-exists gate skips it for both consumers: no note, no rows.
        #expect(report.databasesRead == 0)
        #expect(report.notes.isEmpty)
        #expect(try store.files().isEmpty)
    }

    @Test func aDatabaseWithoutTheSchemaBecomesANote() async throws {
        let f = try Fixture()
        // A database caught mid-rebuild: opens, holds none of the tables.
        try f.source.execute("DROP TABLE part; DROP TABLE message; DROP TABLE session;")
        try f.source.execute("CREATE TABLE unrelated (id INTEGER)")
        await f.attach()

        let report = try await f.scan()

        // The token scan and the index read each note it once; neither aborts.
        #expect(report.databasesRead == 0)
        #expect(report.notes.count == 2)
        #expect(report.notes.allSatisfy { $0.contains("OpenCode") && $0.contains(f.db.path) })
        #expect(try f.store.files().isEmpty)
    }

    @Test func aLockedDatabaseBecomesANoteAndDoesNotAbort() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        await f.attach()
        // A writer holding an exclusive lock makes every read wait out the
        // busy timeout and fail — both consumers note it and move on.
        let lock = try SQLiteDatabase(url: f.db)
        try lock.execute("BEGIN EXCLUSIVE")
        defer { try? lock.execute("ROLLBACK") }

        let report = try await f.scan()

        #expect(report.databasesRead == 0)
        #expect(report.notes.count == 2)
        #expect(try f.store.files().isEmpty)
    }

    @Test func aSessionThatAlwaysFailsHoldsOutAfterThreeScans() async throws {
        let f = try Fixture()
        // ses_1 carries a part whose data is not JSON: the capture SELECT's
        // json_extract throws mid-read, failing only that session.
        try f.addSession("ses_1", title: "Broken parts",
                         created: 1_791_100_000_000, updated: 1_791_100_000_000)
        try f.addMessage("m_u1", session: "ses_1", role: "user", created: 1_791_100_000_000)
        try f.addPart("p_bad", message: "m_u1", session: "ses_1",
                      created: 1_791_100_000_000, data: "this is not json")
        try f.addSession("ses_2", title: "Deploy otter service",
                         created: 1_791_200_000_000, updated: 1_791_200_000_000)
        try f.addMessage("m_b1", session: "ses_2", role: "user", created: 1_791_200_000_000)
        try f.addPart("p_b1", message: "m_b1", session: "ses_2", created: 1_791_200_000_000,
                      data: #"{"type":"text","text":"deploy the capybara service"}"#)
        await f.attach()

        for round in 1...3 {
            let report = try await f.scan()
            #expect(report.notes.contains { $0.contains("#ses_1") }, "scan \(round)")
            let row = try #require(try f.store.fileRow(for: f.dbPath + "#ses_1"))
            #expect(row.state == .failed)
            #expect(row.failCount == round)
            #expect(try f.store.fileRow(for: f.dbPath + "#ses_2")?.state == .current)
        }

        // Three failures at one unchanged stat hold the session out: a
        // fourth scan adds no fourth failure and no note.
        let report = try await f.scan()
        #expect(report.notes.isEmpty)
        #expect(report.filesIndexed == 0)
        #expect(try f.store.fileRow(for: f.dbPath + "#ses_1")?.failCount == 3)

        // Only a stat move releases it: fix the part, bump the session, and
        // the next scan indexes it whole.
        try f.source.prepare("UPDATE part SET data = ? WHERE id = 'p_bad'")
            .bind([.text(#"{"type":"text","text":"now readable zorilla text"}"#)]).run()
        try f.source.prepare("UPDATE session SET time_updated = ? WHERE id = 'ses_1'")
            .bind([.int(1_791_100_009_000)]).run()
        let healed = try await f.scan()
        #expect(healed.filesIndexed == 1)
        #expect(try f.fts("zorilla").count == 1)
        #expect(try f.store.fileRow(for: f.dbPath + "#ses_1")?.state == .current)
    }

    @Test func aSessionDeletedFromTheDatabaseIsRetired() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        await f.attach()
        _ = try await f.scan()
        #expect(try f.store.status().sessionCount == 2)

        try f.source.execute("""
            DELETE FROM part WHERE session_id = 'ses_2';
            DELETE FROM message WHERE session_id = 'ses_2';
            DELETE FROM session WHERE id = 'ses_2';
            """)

        let report = try await f.scan()

        // A complete enumeration proves ses_2 gone: its synthetic row, its
        // session row and its messages all retire.
        #expect(report.filesIndexed == 0)
        #expect(try f.store.files().map(\.path) == [f.dbPath + "#ses_1"])
        #expect(try f.store.status().sessionCount == 1)
        #expect(try f.store.status().messageCount == 4)
        #expect(try f.fts("capybara").isEmpty)
    }

    @Test func anEmptiedDatabaseRetiresNothingItCannotVerify() async throws {
        let f = try Fixture()
        try f.seedTwoSessions()
        await f.attach()
        _ = try await f.scan()

        try f.source.execute("DELETE FROM part; DELETE FROM message; DELETE FROM session;")

        let report = try await f.scan()

        // An empty container proves nothing (the census rule): the rows
        // stay and still answer for the old sessions.
        #expect(report.notes.isEmpty)
        #expect(try f.store.files().count == 2)
        #expect(try f.store.status().sessionCount == 2)
        #expect(try f.fts("capybara").count == 1)
    }
}
