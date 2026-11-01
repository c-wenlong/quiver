import Foundation
import Testing
@testable import SWECore

/// The FTS5 read side: the phrase → AND → OR ladder, per-session bm25
/// grouping, snippet marks, the agent filter, orphan invisibility, and the
/// `matchKeys` that join a hit back to a listed session.
@Suite struct SearchEngineTests {
    private func store(_ temp: TempDir) throws -> SearchStore {
        try SearchStore(directory: temp.path("data/search"))
    }

    private func engine(_ temp: TempDir) -> SearchEngine {
        SearchEngine(directory: temp.path("data/search"))
    }

    /// Commits one file's session and messages the way the indexer does.
    @discardableResult
    private func commit(_ store: SearchStore, path: String,
                        fields: SearchSessionFields,
                        messages: [SearchMessageRow]) throws -> Int64 {
        let session = try store.createSession(fields)
        #expect(try store.commitFile(
            path: path, expected: nil, stat: stat(), byteOffset: 1,
            session: .existing(session), messages: messages, updating: fields))
        return session
    }

    private func stat() -> SearchFileStat { SearchFileStat(mtimeMs: 1_000, sizeBytes: 500) }

    /// Three live sessions across agents plus one orphaned message set
    /// (a `sessions` row deleted after commit, like a replaced file's
    /// drained-away generation).
    private func seeded(_ temp: TempDir) throws -> SearchStore {
        let store = try store(temp)
        try commit(store, path: "/t/claude/a.jsonl",
                   fields: SearchSessionFields(
                       agent: "claude", sessionID: "sess-claude-1",
                       filePath: "/t/claude/a.jsonl", title: "Deploy fix",
                       cwd: "/c/quiver", updatedAt: "2026-10-01T10:00:00Z", messageCount: 2),
                   messages: [
                       SearchMessageRow(role: "user", ts: "2026-10-01T09:00:00Z",
                                        userText: "please fix the ledger cursor stall"),
                       SearchMessageRow(role: "assistant", ts: "2026-10-01T09:00:05Z",
                                        assistantText: "patched the resume path"),
                   ])
        try commit(store, path: "/t/codex/b.jsonl",
                   fields: SearchSessionFields(
                       agent: "codex", sessionID: "0199abcd-1111-2222-3333-444455556666",
                       filePath: "/t/codex/rollout-2026-10-01T10-00-00-0199ABCD-1111-2222-3333-444455556666.jsonl",
                       title: "Parser refactor", cwd: "/c/site",
                       updatedAt: "2026-10-02T10:00:00Z", messageCount: 1),
                   messages: [
                       SearchMessageRow(role: "assistant", ts: "2026-10-02T09:00:00Z",
                                        assistantText: "refactored the parser; Ledger.swift now reports ledger usage"),
                   ])
        try commit(store, path: "/t/pi/c.jsonl",
                   fields: SearchSessionFields(
                       agent: "pi", sessionID: "pi-9f8e7d",
                       filePath: "/t/pi/c.jsonl", title: "Build quirk",
                       cwd: "/c/pi", updatedAt: "2026-10-03T10:00:00Z", messageCount: 2),
                   messages: [
                       SearchMessageRow(role: "user", ts: "2026-10-03T09:00:00Z",
                                        userText: "the foo-bar flag broke the build"),
                       SearchMessageRow(role: "tool", ts: "2026-10-03T09:00:02Z",
                                        toolText: "compiled the C++ driver"),
                   ])
        // Orphaned: messages whose sessions row is gone must never answer.
        let orphan = try store.createSession(SearchSessionFields(
            agent: "claude", sessionID: "sess-ghost", filePath: "/t/claude/ghost.jsonl"))
        #expect(try store.commitFile(
            path: "/t/claude/ghost.jsonl", expected: nil, stat: stat(), byteOffset: 1,
            session: .existing(orphan),
            messages: [SearchMessageRow(role: "user", userText: "ledger ghostword everywhere")]))
        try store.db.prepare("DELETE FROM sessions WHERE id = ?").bind([.int(orphan)]).run()
        return store
    }

    @Test func anUnavailableIndexAnswersEmpty() throws {
        let temp = try TempDir()
        let engine = engine(temp)

        #expect(!engine.available)
        #expect(try engine.search(query: "ledger") == [])
        // …and once an index appears, the same engine picks it up.
        _ = try store(temp)
        #expect(engine.available)
    }

    /// "Clear search data" deletes the file and makes a new one. A held
    /// connection kept reading the deleted file, so cleared transcripts
    /// still answered until the app restarted.
    @Test func aReplacedIndexIsReopened() throws {
        let temp = try TempDir()
        _ = try seeded(temp)
        let engine = engine(temp)
        #expect(try engine.search(query: "ledger cursor").map(\.sessionID) == ["sess-claude-1"])

        SearchLocation.removeDatabase(at: engine.url)
        _ = try store(temp)
        #expect(engine.available)
        #expect(try engine.search(query: "ledger cursor") == [])

        SearchLocation.removeDatabase(at: engine.url)
        #expect(!engine.available)
    }

    @Test func aPhraseHitKeepsTheBestRowPerSession() throws {
        let temp = try TempDir()
        _ = try seeded(temp)

        let hits = try engine(temp).search(query: "ledger cursor")

        #expect(hits.map(\.sessionID) == ["sess-claude-1"])
        let hit = try #require(hits.first)
        #expect(hit.agent == "claude" && hit.title == "Deploy fix" && hit.cwd == "/c/quiver")
        #expect(hit.role == "user" && hit.ts == "2026-10-01T09:00:00Z")
        #expect(hit.score > 0)
        #expect(hit.snippet.contains("ledger cursor"))
        #expect(hit.markedSnippet.contains("\u{E000}"))
        let matched = hit.snippetParts.filter(\.matched).map(\.text).joined()
        #expect(matched.contains("ledger cursor"))
    }

    @Test func separatedTermsStillMatchThroughAnd() throws {
        let temp = try TempDir()
        _ = try seeded(temp)

        // "ledger" and "parser" are never adjacent; the phrase rung misses,
        // the AND rung answers.
        let hits = try engine(temp).search(query: "ledger parser")

        #expect(hits.map(\.sessionID) == ["0199abcd-1111-2222-3333-444455556666"])
        #expect(hits.first?.role == "assistant")
    }

    @Test func theOrRungAnswersWhenNoSessionHasEveryTerm() throws {
        let temp = try TempDir()
        _ = try seeded(temp)

        let hits = try engine(temp).search(query: "ledger nosuchtermzzz")

        #expect(Set(hits.map(\.sessionID)) ==
            ["sess-claude-1", "0199abcd-1111-2222-3333-444455556666"])
        // The orphan's "ledger ghostword" row never joins a session.
        #expect(!hits.contains { $0.sessionID == "sess-ghost" })
    }

    @Test func orphanRowsNeverAnswer() throws {
        let temp = try TempDir()
        _ = try seeded(temp)

        #expect(try engine(temp).search(query: "ghostword") == [])
    }

    @Test func punctuationTermsSearchVerbatim() throws {
        let temp = try TempDir()
        _ = try seeded(temp)
        let engine = engine(temp)

        // `foo-bar` is one token to the tokenizer; unquoted it would read
        // as `foo` minus `bar` — the quoting keeps it verbatim.
        #expect(try engine.search(query: "foo-bar").map(\.sessionID) == ["pi-9f8e7d"])
        // `C++` unquoted is an FTS syntax error; quoted it hits tool text.
        let cpp = try engine.search(query: "C++")
        #expect(cpp.map(\.sessionID) == ["pi-9f8e7d"])
        #expect(cpp.first?.snippet.contains("compiled") == true)
        // `.` is a tokenchar too: the file name is one token.
        #expect(try engine.search(query: "Ledger.swift").map(\.sessionID) ==
            ["0199abcd-1111-2222-3333-444455556666"])
    }

    @Test func theAgentFilterNarrowsHits() throws {
        let temp = try TempDir()
        _ = try seeded(temp)
        let engine = engine(temp)

        #expect(try engine.search(query: "ledger", agents: ["claude"]).map(\.sessionID)
            == ["sess-claude-1"])
        #expect(try engine.search(query: "ledger", agents: ["devin"]) == [])
    }

    @Test func limitCapsTheHits() throws {
        let temp = try TempDir()
        _ = try seeded(temp)

        // Two seeded sessions match; a limit below one still answers one.
        #expect(try engine(temp).search(query: "ledger").count == 2)
        #expect(try engine(temp).search(query: "ledger", limit: 1).count == 1)
        #expect(try engine(temp).search(query: "ledger", limit: 0).count == 1)
    }

    @Test func anEmptyOrSeparatorQueryAnswersNothing() throws {
        let temp = try TempDir()
        _ = try seeded(temp)
        let engine = engine(temp)

        #expect(try engine.search(query: "") == [])
        #expect(try engine.search(query: "   ") == [])
        #expect(try engine.search(query: "… —") == [])
    }

    // MARK: - matchKeys

    @Test func matchKeysCoverTheIdFormsSweAndTheIndexUse() {
        #expect(SearchEngine.matchKeys("abc-123") == ["abc-123"])
        // Pi sessions are keyed by transcript path: the stem counts.
        #expect(SearchEngine.matchKeys("/u/.pi/sessions/xyz-9.jsonl") ==
            ["/u/.pi/sessions/xyz-9.jsonl", "xyz-9"])
        // A codex rollout stem names the FIRST uuid — the thread — which is
        // what the index records from session_meta.
        let keys = SearchEngine.matchKeys(
            "rollout-2026-10-01T10-00-00-0199ABCD-1111-2222-3333-444455556666_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        #expect(keys.contains("0199abcd-1111-2222-3333-444455556666"))
        #expect(!keys.contains("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        #expect(SearchEngine.matchKeys("") == [])
    }

    // MARK: - Snippet rendering

    @Test func aSnippetNeverCutsInsideAnOpenMark() {
        let long = String(repeating: "x", count: 600)
        let marked = "\u{E000}\(long)\u{E001}"
        let (text, markedOut, parts) = SearchSnippet.render(plain: long, marked: marked)

        // The cap lands inside the one marked run: all three renderings
        // roll back to before the open mark rather than dangle it.
        #expect(!markedOut.contains("\u{E000}"))
        #expect(text.isEmpty && markedOut.isEmpty && parts == [])
    }

    @Test func aTranscriptsOwnPrivateUseGlyphStaysText() {
        // A Nerd-Font PUA scalar in the content is not a mark: it appears in
        // both renderings, so it rides along as text instead of highlighting.
        let plain = "icons \u{E005} here"
        let (text, _, parts) = SearchSnippet.render(plain: plain, marked: plain)

        #expect(text == plain)
        #expect(parts == [.init(text: plain, matched: false)])
    }
}

/// The Sessions page's union: a transcript hit keeps a session the
/// metadata substring match doesn't name — `swe session --search`'s rule.
@MainActor
@Suite struct SessionsModelSearchTests {
    let now = Date(timeIntervalSince1970: 1_791_700_000)

    private func session(_ id: String, at: Double, title: String) -> SessionInfo {
        SessionInfo(id: id, harness: "claude", agent: "Claude", cwd: "/c/quiver",
                    title: title, titleSource: nil,
                    lastActiveAt: Date(timeIntervalSince1970: at), status: nil,
                    resume: nil)
    }

    /// A real index under `temp` holding one claude session whose
    /// transcript mentions "cursor" while its title never does.
    private func index(_ temp: TempDir) throws {
        let store = try SearchStore(directory: temp.path("data/search"))
        let sid = try store.createSession(SearchSessionFields(
            agent: "claude", sessionID: "sess-claude-1", filePath: "/t/a.jsonl",
            title: "Deploy fix", cwd: "/c/quiver",
            updatedAt: "2026-10-01T10:00:00Z", messageCount: 1))
        #expect(try store.commitFile(
            path: "/t/a.jsonl", expected: nil,
            stat: SearchFileStat(mtimeMs: 1_000, sizeBytes: 500), byteOffset: 1,
            session: .existing(sid),
            messages: [SearchMessageRow(role: "user",
                                        userText: "the ledger cursor stalled")]))
    }

    private func model(_ temp: TempDir, indexing: Bool) -> SessionsModel {
        SessionsModel(
            source: FakeSessionSource(list: SessionList(sessions: [
                session("sess-claude-1", at: 1_791_699_000, title: "Deploy fix"),
                session("sess-cursor", at: 1_791_699_500, title: "cursor settings"),
                session("sess-other", at: 1_791_698_000, title: "Notes"),
            ], failures: [])),
            ledger: FakeSessionLedger(),
            now: { [now] in now },
            searchIndex: temp.path("data/search"), searchIndexingEnabled: indexing)
    }

    @Test func aTranscriptHitKeepsASessionTheMetadataDoesNotName() async throws {
        let temp = try TempDir()
        try index(temp)
        let model = model(temp, indexing: true)
        await model.load()

        model.filter.search = "cursor"
        await model.searchTask?.value

        // "cursor" names the metadata hit and the transcript hit alike.
        #expect(Set(model.visible.map(\.session.id)) == ["sess-claude-1", "sess-cursor"])
        let hit = try #require(model.searchHit(for: "sess-claude-1"))
        #expect(hit.snippet.contains("cursor"))
        #expect(model.searchHit(for: "sess-other") == nil)
    }

    @Test func indexingOffLeavesTheSearchMetadataOnly() async throws {
        let temp = try TempDir()
        try index(temp)
        let model = model(temp, indexing: false)
        await model.load()

        model.filter.search = "cursor"
        await model.searchTask?.value

        #expect(model.visible.map(\.session.id) == ["sess-cursor"])
        #expect(model.searchHit(for: "sess-claude-1") == nil)
    }

    @Test func hitsClearTheMomentTheNeedleChanges() async throws {
        let temp = try TempDir()
        try index(temp)
        let model = model(temp, indexing: true)
        await model.load()

        model.filter.search = "cursor"
        await model.searchTask?.value
        #expect(model.searchHit(for: "sess-claude-1") != nil)

        // A new needle drops the old hits at once — a stale hit must never
        // keep a row it doesn't name.
        model.filter.search = "notes"
        #expect(model.searchHit(for: "sess-claude-1") == nil)
        #expect(model.visible.map(\.session.id) == ["sess-other"])
    }
}
