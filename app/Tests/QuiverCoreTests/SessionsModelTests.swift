import Foundation
import Testing
@testable import QuiverCore

@Suite struct SessionListTests {
    @Test func sessionsDecodeWithTheirResumeCommand() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("session-list"), stderr: ""))
        let list = try await SweClient(runner: runner).sessions(days: 90, limit: 2000)
        #expect(runner.calls == [["session", "2000", "--days=90", "--json"]])
        #expect(list.sessions.map(\.id) == ["aaa-111", "bbb-222", "ccc-333"])
        let first = try #require(list.sessions.first)
        #expect(first.status == "active")
        #expect(first.lastActiveAt == Date(timeIntervalSince1970: 1_791_694_778.5))
        #expect(first.resume?.argv == ["claude", "--resume", "aaa-111"])
        #expect(list.sessions[2].resume == nil)
        #expect(list.sessions[2].cwd == nil)
        #expect(list.failures == ["droid: unreadable"])
    }

    @Test func aWorktreeBelongsToItsRepository() {
        #expect(SessionInfo.project(of: "/Users/k/code/quiver/.claude/worktrees/m4-sessions") == "quiver")
        #expect(SessionInfo.project(of: "/Users/k/code/quiver/.worktrees/x") == "quiver")
        #expect(SessionInfo.project(of: "/Users/k/code/site") == "site")
        #expect(SessionInfo.project(of: nil) == nil)
    }
}

@Suite struct ResumeCommandTests {
    let resume = SessionInfo.Resume(argv: ["claude", "--resume", "aaa 111"], cwd: "/Users/k/my code")

    @Test func theDefaultAccountClearsAnyOverride() {
        let line = SessionResume.shellLine(resume, harness: "claude", accountHome: "/Users/k/.claude", isDefaultHome: true)
        #expect(line == "cd '/Users/k/my code' && unset CLAUDE_CONFIG_DIR; 'claude' '--resume' 'aaa 111'")
    }

    @Test func anotherAccountResumesInItsOwnHome() {
        let line = SessionResume.shellLine(resume, harness: "claude", accountHome: "/Users/k/.quiver/accounts/claude/w/home",
                                           isDefaultHome: false)
        #expect(line == "cd '/Users/k/my code' && CLAUDE_CONFIG_DIR='/Users/k/.quiver/accounts/claude/w/home' 'claude' '--resume' 'aaa 111'")
        let codex = SessionResume.shellLine(.init(argv: ["codex", "resume", "x"], cwd: nil), harness: "codex",
                                            accountHome: "/h", isDefaultHome: false)
        #expect(codex == "CODEX_HOME='/h' 'codex' 'resume' 'x'")
    }

    @Test func otherHarnessesRunAsGiven() {
        let line = SessionResume.shellLine(.init(argv: ["droid", "-r", "x"], cwd: "/p"), harness: "droid",
                                           accountHome: nil, isDefaultHome: true)
        #expect(line == "cd '/p' && 'droid' '-r' 'x'")
    }
}

/// Lists canned sessions.
struct FakeSessionSource: SessionListing {
    var list: SessionList
    var error: Error?
    func sessions(days: Int, limit: Int) async throws -> SessionList {
        if let error { throw error }
        return list
    }
}

actor FakeSessionLedger: SessionUsageQuerying {
    var rows: [SessionUsage] = []
    func set(_ rows: [SessionUsage]) { self.rows = rows }
    func bySession(in range: DateInterval) async -> [SessionUsage] { rows }
}

@MainActor
@Suite struct SessionsModelTests {
    let now = Date(timeIntervalSince1970: 1_791_700_000)

    func session(_ id: String, _ harness: String, cwd: String?, at: Double, title: String? = nil) -> SessionInfo {
        SessionInfo(id: id, harness: harness, agent: harness.capitalized, cwd: cwd, title: title ?? id,
                    titleSource: nil, lastActiveAt: Date(timeIntervalSince1970: at), status: nil,
                    resume: .init(argv: [harness, id], cwd: cwd))
    }

    func usage(_ id: String, _ harness: String, models: [String], cost: Double, home: String = "/Users/k/.claude") -> SessionUsage {
        var t = UsageTotals()
        t.inputTokens = 100
        t.costUSD = cost
        return SessionUsage(sessionId: id, harness: harness, accountHome: home, cwd: nil, models: models, totals: t,
                            firstTimestamp: now, lastTimestamp: now)
    }

    func model(_ sessions: [SessionInfo], _ usage: [SessionUsage]) async -> SessionsModel {
        let ledger = FakeSessionLedger()
        await ledger.set(usage)
        return SessionsModel(source: FakeSessionSource(list: SessionList(sessions: sessions, failures: [])),
                             ledger: ledger, now: { [now] in now })
    }

    @Test func sessionsCarryTheirTokensAndCost() async throws {
        let model = await model(
            [session("a", "claude", cwd: "/c/quiver", at: 1_791_699_000), session("b", "codex", cwd: "/c/site", at: 1_791_698_000)],
            [usage("a", "claude", models: ["claude-opus-5-5"], cost: 4.5)])
        await model.load()
        #expect(model.rows.map(\.id) == ["a", "b"])
        #expect(model.rows[0].usage?.totals.costUSD == 4.5)
        #expect(model.rows[1].usage == nil)
        #expect(model.models == ["claude-opus-5-5"])
        #expect(model.projects == ["quiver", "site"])
        #expect(model.harnesses == ["claude", "codex"])
    }

    @Test func filtersNarrowTheList() async throws {
        let model = await model([
            session("a", "claude", cwd: "/c/quiver", at: 1_791_699_000, title: "Ledger work"),
            session("b", "codex", cwd: "/c/site", at: 1_791_698_000),
            session("c", "claude", cwd: "/c/site", at: 1_791_000_000),
        ], [usage("a", "claude", models: ["opus"], cost: 1), usage("c", "claude", models: ["sonnet"], cost: 1)])
        await model.load()

        model.filter.harness = "claude"
        #expect(model.visible.map(\.id) == ["a", "c"])
        model.filter.window = .week
        #expect(model.visible.map(\.id) == ["a"])
        model.filter = .init()
        model.filter.project = "site"
        #expect(model.visible.map(\.id) == ["b", "c"])
        model.filter = .init()
        model.filter.model = "sonnet"
        #expect(model.visible.map(\.id) == ["c"])
        model.filter = .init()
        model.filter.search = "ledger"
        #expect(model.visible.map(\.id) == ["a"])
    }

    @Test func aFailedListingIsShown() async {
        let model = SessionsModel(
            source: FakeSessionSource(list: SessionList(sessions: [], failures: []), error: SweError.commandFailed(status: 1, message: "boom")),
            ledger: FakeSessionLedger(), now: { [now] in now })
        await model.load()
        #expect(model.error == "boom")
    }
}
