import Foundation
import Testing
@testable import QuiverCore

/// End-to-end scans of the index-pure transcript sources — cursor, droid and
/// cline — through `Ledger.scan`: files discovered under `LedgerRoots`,
/// streamed or read whole, committed to the FTS index, and left alone on the
/// next pass until they move.
@Suite struct SearchHarnessScanTests {
    struct Setup {
        let temp: TempDir
        let ledger: Ledger
        let store: SearchStore
        let indexer: SearchIndexer

        init() throws {
            temp = try TempDir()
            ledger = try Ledger(directory: temp.path("cache"))
            store = try SearchStore(directory: temp.path("data/search"))
            indexer = SearchIndexer(store: store)
        }

        func attach() async { await ledger.setIndexer(indexer) }

        func write(_ text: String, to url: URL) throws {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try Data(text.utf8).write(to: url)
        }

        func fts(_ query: String) throws -> [Int64] {
            let statement = try store.db.prepare(
                "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rowid")
            statement.bind([.text(query)])
            var ids: [Int64] = []
            while try statement.step() { ids.append(statement.int(0)) }
            return ids
        }

        /// The committed `sessions` row for a harness's indexed file.
        func session(agent: String) throws -> SearchSession? {
            for file in try store.files() {
                guard let rowID = file.sessionRowID,
                      let session = try store.sessionRow(rowID),
                      session.fields.agent == agent
                else { continue }
                return session
            }
            return nil
        }

        func setMtime(_ date: Date, on url: URL) throws {
            try FileManager.default.setAttributes(
                [.modificationDate: date], ofItemAtPath: url.path)
        }
    }

    /// A real-shaped transcript; the trailing newline matters — an
    /// unterminated tail line is still being written and never indexes.
    private let cursorTranscript = """
        {"role":"user","message":{"content":[{"type":"text","text":"how do I fix the build?"}]}}
        {"role":"assistant","message":{"content":[{"type":"text","text":"Run swift build."},{"type":"tool_use","name":"Bash","input":{"command":"swift build"}}]}}
        {"role":"user","message":{"content":[{"type":"tool_result","content":[{"type":"text","text":"Build complete."}]}]}}
        """ + "\n"

    @Test func cursorTranscriptsIndexWithChatMetaEnrichment() async throws {
        let s = try Setup()
        await s.attach()
        let cursor = s.temp.path("cursor")
        let transcript = cursor.appendingPathComponent(
            "projects/-repo/agent-transcripts/chat-1/chat-1.jsonl")
        try s.write(cursorTranscript, to: transcript)
        // The chat's own metadata sits in the sibling `chats` tree.
        try s.write("""
            {"schemaVersion":1,"createdAtMs":1789471031400,"hasConversation":true,
             "title":"Renamed chat","updatedAtMs":1789471031500,"cwd":"/repo/demo"}
            """, to: cursor.appendingPathComponent("chats/ab12cd/chat-1/meta.json"))
        // A .jsonl outside agent-transcripts/ is not a chat transcript.
        try s.write(cursorTranscript,
                    to: cursor.appendingPathComponent("projects/-repo/mcps/stray.jsonl"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(cursorProjects: [cursor.appendingPathComponent("projects")]))

        #expect(report.filesIndexed == 1)
        #expect(try s.fts("fix").count == 1)
        #expect(try s.fts("swift").count >= 1)
        let session = try #require(try s.session(agent: "cursor"))
        // The transcript carries no id — the filename's stem names it.
        #expect(session.fields.sessionID == "chat-1")
        // The first user text keeps the title even when meta has one.
        #expect(session.fields.title == "how do I fix the build?")
        // cwd and timestamps exist only in meta.json — Orca's merge.
        #expect(session.fields.cwd == "/repo/demo")
        #expect(session.fields.createdAt == "2026-09-15T11:17:11.400Z")
        #expect(session.fields.updatedAt == "2026-09-15T11:17:11.500Z")
    }

    @Test func cursorMetaSuppliesTheTitleOnlyWhenTheTranscriptHasNone() async throws {
        let s = try Setup()
        await s.attach()
        let cursor = s.temp.path("cursor")
        try s.write(
            #"{"role":"assistant","message":{"content":[{"type":"text","text":"no user turn"}]}}"#
                + "\n",
            to: cursor.appendingPathComponent(
                "projects/-repo/agent-transcripts/chat-9/chat-9.jsonl"))
        try s.write(
            #"{"title":"Meta chat title","cwd":"/repo/x"}"#,
            to: cursor.appendingPathComponent("chats/ab12cd/chat-9/meta.json"))

        _ = try await s.ledger.scan(
            roots: LedgerRoots(cursorProjects: [cursor.appendingPathComponent("projects")]))

        let session = try #require(try s.session(agent: "cursor"))
        #expect(session.fields.title == "Meta chat title")
        #expect(session.fields.cwd == "/repo/x")
    }

    @Test func droidTranscriptsIndexUnderSessionsAndProjects() async throws {
        let s = try Setup()
        await s.attach()
        let factory = s.temp.path("factory")
        let body = """
            {"type":"session_start","id":"d-1","title":"Fix the parser","cwd":"/repo/x"}
            {"type":"message","timestamp":"2026-10-05T12:00:00Z","message":{"role":"user","content":[{"type":"text","text":"why is it failing?"}]}}
            {"type":"message","timestamp":"2026-10-05T12:00:01Z","message":{"role":"assistant","content":[{"type":"text","text":"Checking."},{"type":"tool_use","name":"Read","input":{"file_path":"/repo/x/a.swift"}}]}}
            {"type":"completion","finalText":"All sorted.","timestamp":"2026-10-05T12:00:02Z"}
            """ + "\n"
        try s.write(body, to: factory.appendingPathComponent("sessions/-repo-x/d-1.jsonl"))
        // The second droid root — `projects` — indexes identically.
        try s.write(body, to: factory.appendingPathComponent("projects/-repo-y/d-2.jsonl"))

        let report = try await s.ledger.scan(roots: LedgerRoots(droidHomes: [factory]))

        #expect(report.filesIndexed == 2)
        #expect(try s.fts("failing").count == 2)
        #expect(try s.fts("swift").count == 2)
        let session = try #require(try s.session(agent: "droid"))
        #expect(session.fields.sessionID == "d-1" || session.fields.sessionID == "d-2")
        #expect(session.fields.title == "Fix the parser")
        #expect(session.fields.cwd == "/repo/x")
        #expect(session.fields.createdAt == "2026-10-05T12:00:00Z")
        #expect(session.fields.updatedAt == "2026-10-05T12:00:02Z")
        #expect(session.fields.messageCount == 4)
    }

    @Test func clineSessionsIndexTheManifestAndMessagesPair() async throws {
        let s = try Setup()
        await s.attach()
        let sessions = s.temp.path("cline/data/sessions")
        let dir = sessions.appendingPathComponent("1789471031317_jp2hv")
        try s.write("""
            {"version":1,"session_id":"1789471031317_jp2hv","source":"cli",
             "status":"done","interactive":true,"provider":"cline",
             "model":"grok-4-1-fast-reasoning","cwd":"/Users/x/ws",
             "workspace_root":"/Users/x/ws","prompt":"say hi",
             "messages_path":"x.messages.json"}
            """, to: dir.appendingPathComponent("1789471031317_jp2hv.json"))
        try s.write("""
            {"agent":"cline","sessionId":"1789471031317_jp2hv","origin":"cli",
             "messages":[
               {"id":"m1","role":"user",
                "content":[{"type":"text","text":"please fix the bug"}],"ts":1789471031400},
               {"id":"m2","role":"assistant",
                "content":[{"type":"thinking","thinking":"hmm"},
                           {"type":"text","text":"On it."}],
                "ts":1789471031500,"modelInfo":{"id":"grok-4-1-fast-reasoning"}}
             ]}
            """, to: dir.appendingPathComponent("1789471031317_jp2hv.messages.json"))

        let report = try await s.ledger.scan(roots: LedgerRoots(clineSessions: [sessions]))

        #expect(report.filesIndexed == 1)
        #expect(try s.fts("bug").count == 1)
        let session = try #require(try s.session(agent: "cline"))
        #expect(session.fields.sessionID == "1789471031317_jp2hv")
        #expect(session.fields.title == "please fix the bug")
        #expect(session.fields.cwd == "/Users/x/ws")
        #expect(session.fields.createdAt == "2026-09-15T11:17:11.400Z")
        #expect(session.fields.updatedAt == "2026-09-15T11:17:11.500Z")
        #expect(session.fields.messageCount == 2)
    }

    @Test func clineManifestWithoutMessagesStillYieldsASession() async throws {
        let s = try Setup()
        await s.attach()
        let sessions = s.temp.path("cline/data/sessions")
        let dir = sessions.appendingPathComponent("only-meta")
        try s.write(
            #"{"session_id":"only-meta","cwd":"/ws","prompt":"the whole ask"}"#,
            to: dir.appendingPathComponent("only-meta.json"))

        let report = try await s.ledger.scan(roots: LedgerRoots(clineSessions: [sessions]))

        #expect(report.filesIndexed == 1)
        let session = try #require(try s.session(agent: "cline"))
        #expect(session.fields.title == "the whole ask")
        #expect(session.fields.messageCount == 0)
    }

    @Test func unchangedIndexPureFilesAreNotReindexed() async throws {
        let s = try Setup()
        await s.attach()
        let cursor = s.temp.path("cursor")
        let factory = s.temp.path("factory")
        let sessions = s.temp.path("cline/data/sessions")
        try s.write(cursorTranscript, to: cursor.appendingPathComponent(
            "projects/-repo/agent-transcripts/chat-1/chat-1.jsonl"))
        try s.write(#"{"type":"session_start","id":"d-1","title":"t","cwd":"/x"}"# + "\n",
                    to: factory.appendingPathComponent("sessions/-x/d-1.jsonl"))
        try s.write(#"{"session_id":"c-1","prompt":"p"}"#,
                    to: sessions.appendingPathComponent("c-1/c-1.json"))

        let roots = LedgerRoots(
            droidHomes: [factory],
            cursorProjects: [cursor.appendingPathComponent("projects")],
            clineSessions: [sessions])
        let first = try await s.ledger.scan(roots: roots)
        #expect(first.filesIndexed == 3)

        let second = try await s.ledger.scan(roots: roots)
        #expect(second.filesIndexed == 0)
        #expect(try s.store.status().sessionCount == 3)
    }

    @Test func aClineMessagesRewriteReindexesThePair() async throws {
        let s = try Setup()
        await s.attach()
        let sessions = s.temp.path("cline/data/sessions")
        let dir = sessions.appendingPathComponent("s1")
        try s.write(#"{"session_id":"s1","cwd":"/ws","prompt":"hi"}"#,
                    to: dir.appendingPathComponent("s1.json"))
        try s.write(
            #"{"sessionId":"s1","messages":[{"role":"user","content":[{"type":"text","text":"first"}],"ts":1789471031400}]}"#,
            to: dir.appendingPathComponent("s1.messages.json"))
        try await s.ledger.scan(roots: LedgerRoots(clineSessions: [sessions]))
        #expect(try s.fts("second").isEmpty)

        // Only the sibling moved — the manifest is unchanged, yet the blend
        // of its mtime into the stat still re-reads the pair (Orca's
        // contentDependencyPath).
        try s.write(
            #"{"sessionId":"s1","messages":[{"role":"user","content":[{"type":"text","text":"first"}],"ts":1789471031400},{"role":"user","content":[{"type":"text","text":"second turn"}],"ts":1789471031600}]}"#,
            to: dir.appendingPathComponent("s1.messages.json"))
        let report = try await s.ledger.scan(roots: LedgerRoots(clineSessions: [sessions]))

        #expect(report.filesIndexed == 1)
        #expect(try s.fts("second").count == 1)
    }

    @Test func recentLimitKeepsOnlyTheNewestTranscripts() async throws {
        let s = try Setup()
        await s.attach()
        let projects = s.temp.path("cursor/projects")
        let old = projects.appendingPathComponent("w/agent-transcripts/old-chat/old-chat.jsonl")
        let new = projects.appendingPathComponent("w/agent-transcripts/new-chat/new-chat.jsonl")
        try s.write(cursorTranscript, to: old)
        try s.write(cursorTranscript, to: new)
        try s.setMtime(Date(timeIntervalSince1970: 1_000), on: old)
        try s.setMtime(Date(timeIntervalSince1970: 2_000), on: new)

        let report = try await s.ledger.scan(
            roots: LedgerRoots(cursorProjects: [projects]), recentLimit: 1)

        #expect(report.filesIndexed == 1)
        #expect(try s.session(agent: "cursor")?.fields.sessionID == "new-chat")
    }
}
