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
            try store.ftsRowIDs(query)
        }

        /// The committed `sessions` row for a harness's indexed file; when
        /// several sessions share an agent, `sessionID` picks one.
        func session(agent: String, sessionID: String? = nil) throws -> SearchSession? {
            for file in try store.files() {
                guard let rowID = file.sessionRowID,
                      let session = try store.sessionRow(rowID),
                      session.fields.agent == agent,
                      sessionID == nil || session.fields.sessionID == sessionID
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

    // MARK: - transcriptRoots sources

    @Test func qoderAndCodebuddyTranscriptsIndex() async throws {
        let s = try Setup()
        await s.attach()
        let qoder = s.temp.path("qoder/projects")
        try s.write("""
            {"type":"workspace-directories","directories":["/repo/q"]}
            {"type":"user","timestamp":"2026-10-01T00:00:00Z","sessionId":"q-1","message":{"role":"user","content":[{"type":"text","text":"qoder prompt"}]}}
            {"type":"assistant","timestamp":"2026-10-01T00:00:01Z","message":{"role":"assistant","content":"qoder reply"}}
            """ + "\n", to: qoder.appendingPathComponent("-repo-q/s-1.jsonl"))
        let codebuddy = s.temp.path("codebuddy/projects")
        try s.write("""
            {"type":"message","sessionId":"cb-1","cwd":"/repo/cb","role":"user","content":[{"type":"input_text","text":"codebuddy prompt"}]}
            {"type":"summary","summary":"Buddy title"}
            """ + "\n", to: codebuddy.appendingPathComponent("-repo-cb/s-2.jsonl"))

        let report = try await s.ledger.scan(roots: LedgerRoots(
            transcriptRoots: [.qoder: [qoder], .codebuddy: [codebuddy]]))

        #expect(report.filesIndexed == 2)
        let qoderSession = try #require(try s.session(agent: "qoder"))
        #expect(qoderSession.fields.sessionID == "q-1")
        #expect(qoderSession.fields.title == "qoder prompt")
        #expect(qoderSession.fields.cwd == "/repo/q")
        let buddy = try #require(try s.session(agent: "codebuddy"))
        #expect(buddy.fields.title == "Buddy title")
        #expect(buddy.fields.cwd == "/repo/cb")
    }

    @Test func copilotHermesAndJcodeIndex() async throws {
        let s = try Setup()
        await s.attach()
        let copilot = s.temp.path("copilot/session-state")
        try s.write("""
            {"type":"session.start","data":{"sessionId":"cp-1"}}
            {"type":"session.info","data":{"kind":"folder_trust","message":"Folder /repo/app has been added to trusted folders."}}
            {"type":"user.message","data":{"transformedContent":"copilot ask"},"timestamp":"2026-10-01T00:00:00Z"}
            """ + "\n", to: copilot.appendingPathComponent("cp-1.jsonl"))
        let hermes = s.temp.path("hermes/sessions")
        try s.write(#"{"session_id":"h-1","cwd":"/repo/h","messages":[{"role":"user","content":"hermes ask"}]}"#,
                    to: hermes.appendingPathComponent("session_h-1.json"))
        let jcode = s.temp.path("jcode/sessions")
        try s.write(#"{"id":"jc-1","working_dir":"/repo/j","messages":[{"role":"user","content":"jcode ask","timestamp":"t1"}]}"#,
                    to: jcode.appendingPathComponent("session_jc-1.json"))

        let report = try await s.ledger.scan(roots: LedgerRoots(transcriptRoots: [
            .copilot: [copilot], .hermes: [hermes], .jcode: [jcode]]))

        #expect(report.filesIndexed == 3)
        let copilotSession = try #require(try s.session(agent: "copilot"))
        #expect(copilotSession.fields.sessionID == "cp-1")
        #expect(copilotSession.fields.cwd == "/repo/app")
        #expect(copilotSession.fields.title == "copilot ask")
        #expect(try s.session(agent: "hermes")?.fields.title == "hermes ask")
        #expect(try s.session(agent: "jcode")?.fields.sessionID == "jc-1")
    }

    @Test func geminiJsonAndJsonlBothIndex() async throws {
        let s = try Setup()
        await s.attach()
        let gemini = s.temp.path("gemini/tmp/proj/chats")
        try s.write(#"{"sessionId":"g-json","messages":[{"type":"user","content":"document ask","timestamp":"t1"}]}"#,
                    to: gemini.appendingPathComponent("checkpoint-g.json"))
        try s.write("""
            {"type":"user","content":"stream ask","timestamp":"t1","sessionId":"g-l"}
            {"type":"gemini","content":"stream reply","timestamp":"t2"}
            """ + "\n", to: gemini.appendingPathComponent("session-g.jsonl"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.gemini: [s.temp.path("gemini/tmp")]]))

        #expect(report.filesIndexed == 2)
        #expect(try s.session(agent: "gemini") != nil)
        #expect(try s.fts("document").count == 1)
        #expect(try s.fts("stream").count == 2)  // both turn records' text
        #expect(try s.store.status().sessionCount == 2)
    }

    @Test func rovoPairIndexesAsOneSession() async throws {
        let s = try Setup()
        await s.attach()
        let sessions = s.temp.path("rovodev/sessions")
        let dir = sessions.appendingPathComponent("sess-7")
        try s.write(#"{"title":"Rovo named","workspace_path":"/repo/r","created_at":"2026-10-01T00:00:00Z"}"#,
                    to: dir.appendingPathComponent("metadata.json"))
        try s.write(#"{"message_history":[{"kind":"request","parts":[{"part_kind":"user-prompt","content":"rovo ask"}],"timestamp":"t1"},{"kind":"response","parts":[{"part_kind":"text","content":"rovo reply"}],"timestamp":"t2"}]}"#,
                    to: dir.appendingPathComponent("session_context.json"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.rovo: [sessions]]))

        #expect(report.filesIndexed == 1)
        let session = try #require(try s.session(agent: "rovo"))
        // The records never carry the id — the session directory names it.
        #expect(session.fields.sessionID == "sess-7")
        #expect(session.fields.title == "Rovo named")
        #expect(session.fields.cwd == "/repo/r")
        #expect(session.fields.messageCount == 2)
        #expect(try s.fts("rovo").count == 2)
    }

    @Test func rovoContextRewriteReindexesThePair() async throws {
        let s = try Setup()
        await s.attach()
        let sessions = s.temp.path("rovodev/sessions")
        let dir = sessions.appendingPathComponent("sess-8")
        try s.write(#"{"title":"t","workspace":"/r"}"#,
                    to: dir.appendingPathComponent("metadata.json"))
        try s.write(#"{"messages":[{"role":"user","content":"first"}]}"#,
                    to: dir.appendingPathComponent("session_context.json"))
        try await s.ledger.scan(roots: LedgerRoots(transcriptRoots: [.rovo: [sessions]]))
        #expect(try s.fts("second").isEmpty)

        // Only the context sibling moved — its mtime blends into the stat.
        try s.write(#"{"messages":[{"role":"user","content":"first"},{"role":"user","content":"second turn"}]}"#,
                    to: dir.appendingPathComponent("session_context.json"))
        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.rovo: [sessions]]))

        #expect(report.filesIndexed == 1)
        #expect(try s.fts("second").count == 1)
    }

    @Test func kiroPairIndexesAndSkipsSubagentManifests() async throws {
        let s = try Setup()
        await s.attach()
        let cli = s.temp.path("kiro/sessions/cli")
        let uuid = "123e4567-e89b-42d3-a456-426614174000"
        try s.write(#"{"cwd":"/repo/k","title":"Kiro chat","created_at":"2026-09-15T11:00:00Z"}"#,
                    to: cli.appendingPathComponent("\(uuid).json"))
        try s.write("""
            {"kind":"Prompt","data":{"content":[{"kind":"text","data":"kiro ask"}],"meta":{"timestamp":1789471031}}}
            {"kind":"AssistantMessage","data":{"content":[{"kind":"text","data":"kiro reply"}]}}
            """ + "\n", to: cli.appendingPathComponent("\(uuid).jsonl"))
        // A child session with a parent is reached from its parent, not listed.
        let child = "223e4567-e89b-42d3-a456-426614174000"
        try s.write(#"{"parent_session_id":"\#(uuid)","title":"child"}"#,
                    to: cli.appendingPathComponent("\(child).json"))
        try s.write(#"{"kind":"Prompt","data":{"content":[{"kind":"text","data":"child ask"}]}}"# + "\n",
                    to: cli.appendingPathComponent("\(child).jsonl"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.kiro: [cli]]))

        #expect(report.filesIndexed == 2)
        let session = try #require(try s.session(agent: "kiro"))
        #expect(session.fields.sessionID == uuid)
        #expect(session.fields.title == "Kiro chat")
        #expect(session.fields.messageCount == 2)
        // The manifest's created_at folds into the timeline (earliest wins).
        #expect(session.fields.createdAt == "2026-09-15T11:00:00Z")
        #expect(session.fields.updatedAt == "2026-09-15T11:17:11.000Z")
    }

    @Test func grokPairIndexes() async throws {
        let s = try Setup()
        await s.attach()
        let dir = s.temp.path("grok/sessions/-repo/sess-3")
        try s.write(#"{"info":{"id":"gk-3","cwd":"/repo/g"},"generated_title":"Grok chat","head_branch":"main"}"#,
                    to: dir.appendingPathComponent("summary.json"))
        try s.write("""
            {"type":"user","timestamp":"t1","content":"<user_info>ctx</user_info><user_query>grok ask</user_query>"}
            {"type":"assistant","timestamp":"t2","content":"grok reply"}
            """ + "\n", to: dir.appendingPathComponent("chat_history.jsonl"))
        // A summary without info.id names itself after its directory.
        let bare = s.temp.path("grok/sessions/-repo/dir-named")
        try s.write(#"{"generated_title":"Bare"}"#,
                    to: bare.appendingPathComponent("summary.json"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.grok: [s.temp.path("grok/sessions")]]))

        #expect(report.filesIndexed == 2)
        let session = try #require(try s.session(agent: "grok", sessionID: "gk-3"))
        #expect(session.fields.sessionID == "gk-3")
        #expect(session.fields.title == "Grok chat")
        // `head_branch` decodes to a hint like every decoder's branch hint,
        // but SearchSessionFields has no branch column — it is not persisted.
        #expect(session.fields.messageCount == 2)
        #expect(try s.fts("ctx").isEmpty)  // the bootstrap dump is suppressed
    }

    @Test func kimiSessionIndexesManifestAndWire() async throws {
        let s = try Setup()
        await s.attach()
        let kimiHome = s.temp.path("kimi-code")
        let session = kimiHome.appendingPathComponent("sessions/wd_ws_ab12/session_abc")
        try s.write(#"{"title":"Kimi named","lastPrompt":"fallback ask","agents":{"main":{"type":"main"},"sub":{"type":"task","parentAgentId":"main"}}}"#,
                    to: session.appendingPathComponent("state.json"))
        try s.write("""
            {"type":"context.append_message","message":{"role":"user","origin":{"kind":"user"},"content":[{"type":"text","text":"kimi ask"}]}}
            {"type":"context.append_loop_event","event":{"type":"content.part","part":{"type":"text","text":"kimi "}}}
            {"type":"context.append_loop_event","event":{"type":"content.part","part":{"type":"text","text":"reply"}}}
            {"type":"context.append_loop_event","event":{"type":"step.end"}}
            """ + "\n", to: session.appendingPathComponent("agents/main/wire.jsonl"))
        try s.write(#"{"sessionId":"session_abc","workDir":"/repo/kimi"}"# + "\n",
                    to: kimiHome.appendingPathComponent("session_index.jsonl"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.kimi: [kimiHome.appendingPathComponent("sessions")]]))

        #expect(report.filesIndexed == 1)
        let found = try #require(try s.session(agent: "kimi"))
        #expect(found.fields.sessionID == "session_abc")
        #expect(found.fields.title == "Kimi named")
        // The shared session index supplies the cwd.
        #expect(found.fields.cwd == "/repo/kimi")
        #expect(found.fields.messageCount == 2)
        #expect(try s.fts("reply").count == 1)
    }

    @Test func kimiPrefersTheMainAgentWithoutAParent() async throws {
        let s = try Setup()
        await s.attach()
        let session = s.temp.path("kimi-code/sessions/wd_x/session_p")
        // The primary agent is the `main` one with no parent — not the first
        // key in `agents`, which here is the task child.
        try s.write(#"{"agents":{"sub":{"type":"main","parentAgentId":"real"},"real":{"type":"main"}}}"#,
                    to: session.appendingPathComponent("state.json"))
        try s.write(#"{"type":"context.append_message","message":{"role":"user","origin":{"kind":"user"},"content":"real wire"}}"# + "\n",
                    to: session.appendingPathComponent("agents/real/wire.jsonl"))
        try s.write(#"{"type":"context.append_message","message":{"role":"user","origin":{"kind":"user"},"content":"sub wire"}}"# + "\n",
                    to: session.appendingPathComponent("agents/sub/wire.jsonl"))

        try await s.ledger.scan(roots: LedgerRoots(
            transcriptRoots: [.kimi: [s.temp.path("kimi-code/sessions")]]))

        #expect(try s.fts("real").count == 1)
        #expect(try s.fts("sub").isEmpty)
    }

    @Test func museSessionJsonlIndexes() async throws {
        let s = try Setup()
        await s.attach()
        let dir = s.temp.path("muse/sessions/2026/11/02/uuid-9")
        try s.write("""
            {"payload_type":"runtime.session.metadata","recorded_at":1789471031400000,"payload":{"record":{"workspace_root":"/repo/m"}}}
            {"payload_type":"runtime.user_intent.accepted","recorded_at":1789471031500000,"payload":{"refill_blocks":[{"text":"muse ask"}]}}
            {"payload_type":"runtime.session","recorded_at":1789471031600000,"payload":{"event":{"kind":"started","prompt":"muse ask"}}}
            {"payload_type":"runtime.session","recorded_at":1789471031700000,"payload":{"event":{"kind":"assistant_message_committed","text":"muse reply"}}}
            """ + "\n", to: dir.appendingPathComponent("session.jsonl"))
        // The .log/.sqlite3 sidecars are not transcripts.
        try s.write("noise", to: dir.appendingPathComponent("session.log"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.muse: [s.temp.path("muse/sessions")]]))

        #expect(report.filesIndexed == 1)
        let session = try #require(try s.session(agent: "muse"))
        #expect(session.fields.sessionID == "uuid-9")
        #expect(session.fields.cwd == "/repo/m")
        #expect(session.fields.title == "muse ask")
        // The started record deduped against the intent — 2 turns, not 3.
        #expect(session.fields.messageCount == 2)
    }

    @Test func antigravityHiddenTranscriptIndexesWithPathSessionId() async throws {
        let s = try Setup()
        await s.attach()
        let brain = s.temp.path("gemini/antigravity-cli/brain")
        let logs = brain.appendingPathComponent("conv-42/.system_generated/logs")
        try s.write("""
            {"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>antigravity ask</USER_REQUEST>","created_at":"t1"}
            {"source":"MODEL","type":"PLANNER_RESPONSE","content":"antigravity plan","created_at":"t2"}
            """ + "\n", to: logs.appendingPathComponent("transcript_full.jsonl"))
        // The compact head transcript is deduped away beside the full one.
        try s.write(#"{"source":"USER","type":"USER_INPUT","content":"dup"}"# + "\n",
                    to: logs.appendingPathComponent("transcript.jsonl"))

        let report = try await s.ledger.scan(
            roots: LedgerRoots(transcriptRoots: [.antigravity: [brain]]))

        #expect(report.filesIndexed == 1)
        let session = try #require(try s.session(agent: "antigravity"))
        // The conversation directory names the session — the records carry
        // no id, cwd or title.
        #expect(session.fields.sessionID == "conv-42")
        #expect(session.fields.title == "antigravity ask")
        #expect(session.fields.messageCount == 2)
        #expect(try s.fts("dup").isEmpty)
    }

    @Test func ompPrimeAgentAndOpenclawIndex() async throws {
        let s = try Setup()
        await s.attach()
        let omp = s.temp.path("omp/agent/sessions")
        try s.write("""
            {"type": "session", "id": "o-1", "cwd": "/repo/o"}
            {"type": "message", "timestamp": "t1", "message": {"role": "user", "content": [{"type": "text", "text": "omp ask"}]}}
            {"type": "title", "v": 1, "title": "Omp chosen", "source": "user", "updatedAt": "t2"}
            """ + "\n", to: omp.appendingPathComponent("ws/o-1.jsonl"))
        // An artifact dir named like a session is pruned (Orca #9330).
        let artifact = omp.appendingPathComponent("ws/2026-01-02T03-04-05-678Z_123e4567-e89b-42d3-a456-426614174000")
        try s.write(#"{"type": "message", "message": {"role": "user", "content": "inner"}}"# + "\n",
                    to: artifact.appendingPathComponent("inner.jsonl"))
        let prime = s.temp.path("prime/agent/sessions")
        try s.write("""
            {"type": "session", "id": "p-1", "cwd": "/repo/p"}
            {"type": "message", "message": {"role": "user", "content": "prime ask"}}
            """ + "\n", to: prime.appendingPathComponent("p-1.jsonl"))
        let claw = s.temp.path("openclaw/agents")
        try s.write("""
            {"type": "session", "id": "c-1", "cwd": "/repo/c"}
            {"type": "message", "message": {"role": "user", "content": "claw ask"}}
            """ + "\n", to: claw.appendingPathComponent("bot/sessions/c-1.jsonl"))

        let report = try await s.ledger.scan(roots: LedgerRoots(transcriptRoots: [
            .omp: [omp], .primeAgent: [prime], .openclaw: [claw]]))

        #expect(report.filesIndexed == 3)
        let ompSession = try #require(try s.session(agent: "omp"))
        #expect(ompSession.fields.title == "Omp chosen")
        #expect(try s.session(agent: "prime-agent")?.fields.title == "prime ask")
        #expect(try s.session(agent: "openclaw")?.fields.title == "claw ask")
        #expect(try s.fts("inner").isEmpty)
    }
}
