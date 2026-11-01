import Foundation
import Testing
@testable import SWECore

private let notes = "/Users/k/Library/Application Support/Claude/side-session-notes/local_parent"
private let worktree = "/Users/k/ai-engineering/.claude/worktrees/jovial"

@Suite struct SessionFoldersTests {
    @Test func theFolderWithMostEventsWinsAndNotesOnlyAsALastResort() {
        #expect(SessionFolders.isAgentNotes(notes))
        #expect(!SessionFolders.isAgentNotes(worktree))
        #expect(SessionFolders.pick([(worktree, 458), (notes, 3)]) == worktree)
        // Even outnumbered, a real folder beats the notes folder.
        #expect(SessionFolders.pick([(notes, 50), (worktree, 2)]) == worktree)
        #expect(SessionFolders.pick([("/a", 2), ("/b", 5), ("/c", 1)]) == "/b")
        // A tie goes to the later one.
        #expect(SessionFolders.pick([("/a", 2), ("/b", 2)]) == "/b")
        #expect(SessionFolders.pick([(notes, 3)]) == notes)
        #expect(SessionFolders.pick([("", 3)]) == nil)
        #expect(SessionFolders.pick([]) == nil)
    }
}

@Suite struct SideSessionFolderTests {
    private func event(_ ts: String, cwd: String, session: String = "s") -> UsageEvent {
        UsageEvent(harness: "claude", accountHome: "/h", sessionId: session, cwd: cwd,
                   model: "m", timestamp: utc(ts), outputTokens: 200)
    }

    /// A side session's shape: most of its work in a worktree, a few
    /// events writing its report into the parent's notes folder, the
    /// first of them starting after the worktree run did.
    private var sideSession: [UsageEvent] {
        [event("2026-10-05T10:00:00Z", cwd: worktree),
         event("2026-10-05T10:02:00Z", cwd: notes),
         event("2026-10-05T10:05:00Z", cwd: worktree),
         event("2026-10-05T10:10:00Z", cwd: worktree),
         event("2026-10-05T10:12:00Z", cwd: notes)]
    }

    @Test func aCalendarBlockTakesTheWorktreeNotTheNotesFolder() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.db.execute("BEGIN IMMEDIATE")
        for (i, event) in (sideSession + [event("2026-10-05T10:00:00Z", cwd: notes, session: "only-notes")])
            .enumerated() {
            try store.upsert(LedgerRecord(key: "k\(i)", event: event))
        }
        try store.db.execute("COMMIT")
        let day = DateInterval(start: utc("2026-10-05T00:00:00Z"), end: utc("2026-10-06T00:00:00Z"))
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        let side = try #require(blocks.first { $0.sessions[0].sessionId == "s" })
        #expect(side.responses == 5)
        #expect(side.sessions[0].cwd == worktree)
        // With nothing else to go on, the notes folder still names it.
        #expect(blocks.first { $0.sessions[0].sessionId == "only-notes" }?.sessions[0].cwd == notes)
    }

    @Test func aSessionsFolderIsTheOneMostOfItsEventsUsed() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let ledger = try Ledger(directory: temp.path("cache"), pricing: .empty)
        _ = try await ledger.ingest(sideSession.enumerated().map { LedgerRecord(key: "k\($0)", event: $1) })
        let sessions = await ledger.bySession(in: DateInterval(start: utc("2026-10-05T00:00:00Z"),
                                                               end: utc("2026-10-06T00:00:00Z")))
        // The last event was in the notes folder; it no longer names it.
        #expect(sessions.map(\.cwd) == [worktree])
    }
}

@Suite struct ClaudeDesktopSessionsTests {
    private func write(_ root: URL, _ id: String, _ json: String) throws {
        let dir = root.appending(path: "acct/org")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try json.write(to: dir.appending(path: "\(id).json"), atomically: true, encoding: .utf8)
    }

    /// Records shaped like the desktop app's, with the bulky fields it
    /// also carries.
    private func fixture(_ root: URL) throws {
        try write(root, "local_parent", #"""
        {"sessionId":"local_parent","cliSessionId":"cli-parent","title":"design-systems-repo",
         "cwd":"/Users/k/ai-engineering","promptAppendSnapshot":{"append":"..."},"prs":[]}
        """#)
        try write(root, "local_side", #"""
        {"sessionId":"local_side","cliSessionId":"cli-side","priorCliSessionIds":["cli-side-old"],
         "title":"Independently verify the recaptured scorecard grades",
         "spawnedFrom":{"sessionId":"local_parent","taskId":"task_1","title":"Verify","sideSession":true}}
        """#)
        try write(root, "local_fork", #"""
        {"sessionId":"local_fork","cliSessionId":"cli-fork","title":"model names",
         "spawnedFrom":{"sessionId":"local_parent","userFork":true}}
        """#)
        // Its parent's record is gone: no title to show.
        try write(root, "local_orphan", #"""
        {"sessionId":"local_orphan","cliSessionId":"cli-orphan","spawnedFrom":{"sessionId":"local_gone"}}
        """#)
        try write(root, "local_broken", "{not json")
    }

    @Test func childrenMapToTheirParentByTranscriptId() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        try fixture(temp.url)
        let parents = ClaudeDesktopSessions(root: temp.url).parents()
        let side = SessionParent(id: "local_parent", title: "design-systems-repo", kind: .side)
        #expect(parents["cli-side"] == side)
        #expect(parents["cli-side-old"] == side)
        #expect(parents["cli-fork"]?.kind == .fork)
        #expect(parents["cli-fork"]?.label == "Forked from design-systems-repo")
        #expect(side.label == "From design-systems-repo")
        #expect(parents["cli-parent"] == nil)
        #expect(parents["cli-orphan"] == nil)
        #expect(parents.count == 3)
    }

    @Test func theCacheRereadsOnlyChangedFiles() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        try fixture(temp.url)
        let cache = temp.path("cache/claude-desktop-sessions.json")
        let reader = ClaudeDesktopSessions(root: temp.url, cacheFile: cache)
        #expect(reader.parents().count == 3)
        #expect(FileManager.default.fileExists(atPath: cache.path))
        // A renamed parent shows its new title.
        try write(temp.url, "local_parent", #"{"sessionId":"local_parent","cliSessionId":"cli-parent","title":"renamed"}"#)
        #expect(reader.parents()["cli-side"]?.title == "renamed")
        // A deleted record drops out of the cache too.
        try FileManager.default.removeItem(at: temp.url.appending(path: "acct/org/local_side.json"))
        #expect(reader.parents()["cli-side"] == nil)
        // No folder at all is no parents, not an error.
        #expect(ClaudeDesktopSessions(root: temp.path("missing")).parents().isEmpty)
    }
}

@MainActor
@Suite struct SessionParentRowTests {
    @Test func claudeRowsCarryTheirParent() async throws {
        let now = Date(timeIntervalSince1970: 1_791_700_000)
        func info(_ id: String, _ harness: String) -> SessionInfo {
            SessionInfo(id: id, harness: harness, agent: harness, cwd: "/c/x", title: id, titleSource: nil,
                        lastActiveAt: now, status: nil, resume: nil)
        }
        let parent = SessionParent(id: "local_parent", title: "design-systems-repo", kind: .side)
        let model = SessionsModel(
            source: FakeSessionSource(list: SessionList(sessions: [info("cli-side", "claude"),
                                                                   info("cli-side", "codex"),
                                                                   info("other", "claude")], failures: [])),
            ledger: FakeSessionLedger(),
            parents: { ["cli-side": parent] }, now: { now })
        await model.load()
        let byKey = Dictionary(uniqueKeysWithValues: model.rows.map { ("\($0.session.harness)|\($0.id)", $0) })
        #expect(byKey["claude|cli-side"]?.parent == parent)
        // Only Claude ids are desktop transcript ids.
        #expect(byKey["codex|cli-side"]?.parent == nil)
        #expect(byKey["claude|other"]?.parent == nil)
    }

    @Test func aRestoredListCarriesParentsToo() async throws {
        let now = Date(timeIntervalSince1970: 1_791_700_000)
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let file = SessionSnapshotFile(url: temp.path("cache/sessions.json"))
        let session = SessionInfo(id: "cli-side", harness: "claude", agent: "Claude Code", cwd: "/c/x",
                                  title: "side", titleSource: nil, lastActiveAt: now, status: nil, resume: nil)
        await file.save(SessionSnapshot(source: "/bin/swe", savedAt: now.addingTimeInterval(-10),
                                        sessions: [session], failures: [], isCapped: false, usage: []))
        let parent = SessionParent(id: "local_parent", title: "design-systems-repo", kind: .side)
        let source = FakeSessionSource(list: SessionList(sessions: [], failures: []))
        let model = SessionsModel(source: source, ledger: FakeSessionLedger(), snapshots: file,
                                  parents: { ["cli-side": parent] }, now: { now })
        await model.restore()
        // The saved list is fresh, so no listing runs after it.
        await model.loadIfStale(for: "/bin/swe")
        #expect(model.rows.map(\.id) == ["cli-side"])
        #expect(model.rows.first?.parent == parent)
    }
}
