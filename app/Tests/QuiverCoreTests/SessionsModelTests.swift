import Foundation
import Testing
@testable import QuiverCore

@Suite struct SessionListTests {
    @Test func sessionsDecodeWithTheirResumeCommand() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("session-list"), stderr: ""))
        let list = try await SweClient(runner: runner).sessions(days: 90, limit: 2000)
        #expect(runner.calls == [["session", "2000", "--days=90", "--json"]])
        #expect(list.sessions.map(\.id) == ["aaa-111", "bbb-222", "ccc-333", "crush|/Users/k/code/notes|1791400000"])
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
        // Every step depends on the one before: a missing folder stops it all.
        #expect(line == "cd '/Users/k/my code' && unset CLAUDE_CONFIG_DIR && 'claude' '--resume' 'aaa 111'")
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
    private(set) var ranges: [DateInterval] = []
    func set(_ rows: [SessionUsage]) { self.rows = rows }
    func bySession(in range: DateInterval) async -> [SessionUsage] {
        ranges.append(range)
        return rows
    }
}

/// Holds the listing until released, to test what happens meanwhile.
actor GatedSessionSource: SessionListing {
    let list: SessionList
    private var gate: CheckedContinuation<Void, Never>?
    private(set) var waiting = false
    init(_ list: SessionList) { self.list = list }
    func sessions(days: Int, limit: Int) async throws -> SessionList {
        waiting = true
        await withCheckedContinuation { gate = $0 }
        return list
    }
    func release() { gate?.resume(); gate = nil }
}

/// Counts how often the session list was fetched.
final class CountingSessionSource: SessionListing, @unchecked Sendable {
    var list: SessionList
    private(set) var calls = 0
    init(_ list: SessionList) { self.list = list }
    func sessions(days: Int, limit: Int) async throws -> SessionList {
        calls += 1
        return list
    }
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

    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        return c
    }

    func model(_ sessions: [SessionInfo], _ usage: [SessionUsage], ledger: FakeSessionLedger = FakeSessionLedger(),
               limit: Int = 100_000) async -> SessionsModel {
        await ledger.set(usage)
        return SessionsModel(source: FakeSessionSource(list: SessionList(sessions: sessions, failures: [])),
                             ledger: ledger, defaultHomes: ["/Users/k/.claude", "/Users/k/.codex"],
                             limit: limit, now: { [now] in now }, calendar: calendar)
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
        // Search covers the project and the models too.
        model.filter.search = "sonnet"
        #expect(model.visible.map(\.id) == ["c"])
        model.filter.search = "quiver"
        #expect(model.visible.map(\.id) == ["a"])
    }

    @Test func visibleSessionsAreSummedAndGroupedByDay() async throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Singapore")!
        // now is 2026-10-11 12:26 SGT; a and b fall on Oct 11, c on Oct 2.
        let model = await model([
            session("a", "claude", cwd: "/c/quiver", at: 1_791_699_000),
            session("b", "codex", cwd: "/c/site", at: 1_791_650_000),
            session("c", "claude", cwd: "/c/site", at: 1_790_900_000),
        ], [usage("a", "claude", models: ["opus"], cost: 4), usage("c", "claude", models: ["sonnet"], cost: 1.5)])
        await model.load()

        let summary = model.summary
        #expect(summary.sessions == 3)
        #expect(summary.totals.totalTokens == 200)
        #expect(summary.totals.costUSD == 5.5)

        let groups = model.groupedByDay(calendar: calendar)
        #expect(groups.map { $0.rows.map(\.id) } == [["a", "b"], ["c"]])
        #expect(groups.first?.day == Date(timeIntervalSince1970: 1_791_648_000))

        model.filter.harness = "codex"
        #expect(model.summary.sessions == 1)
        #expect(model.summary.totals.totalTokens == 0)
    }

    @Test func aSessionsDurationSpansItsFirstAndLastEvent() {
        var usage = usage("a", "claude", models: [], cost: 0)
        usage.firstTimestamp = now
        usage.lastTimestamp = now.addingTimeInterval(2 * 3600 + 5 * 60)
        #expect(SessionsModel.Row(session: session("a", "claude", cwd: nil, at: 0), usage: usage).duration == 7500)
    }

    @Test func todayStartsAtLocalMidnight() async {
        // now is 12:26 SGT; 23:00 the evening before is within 24 hours but not today.
        let model = await model([
            session("a", "claude", cwd: "/c/q", at: 1_791_699_000),
            session("y", "claude", cwd: "/c/q", at: 1_791_644_400),
        ], [])
        await model.load()
        model.filter.window = .today
        #expect(model.visible.map(\.id) == ["a"])
    }

    @Test func usageCoversTheWholeHistoryOfEachSession() async throws {
        let ledger = FakeSessionLedger()
        let model = await model([session("a", "claude", cwd: nil, at: 1_791_699_000)], [], ledger: ledger)
        await model.load()
        let range = try #require(await ledger.ranges.first)
        #expect(range.start <= Date(timeIntervalSince1970: 0))
    }

    @Test func sessionsFromExtraAccountsComeFromTheLedger() async throws {
        var work = usage("w1", "claude", models: ["opus"], cost: 3, home: "/Users/k/.quiver/accounts/claude/w/home")
        work.cwd = "/c/quiver"
        work.lastTimestamp = now.addingTimeInterval(-3600)
        var old = usage("w0", "codex", models: ["gpt"], cost: 1, home: "/Users/k/.quiver/accounts/codex/x/home")
        old.lastTimestamp = now.addingTimeInterval(-200 * 86_400)
        // A default-home session swe didn't list stays out: swe is the source there.
        let stray = usage("d1", "claude", models: [], cost: 1)
        let model = await model([session("a", "claude", cwd: nil, at: 1_791_699_000)], [work, old, stray])
        await model.load()
        #expect(model.rows.map(\.id) == ["a", "w1"])
        let row = try #require(model.rows.last)
        #expect(row.session.harness == "claude")
        #expect(row.session.cwd == "/c/quiver")
        #expect(row.session.project == "quiver")
        #expect(row.session.resume == SessionInfo.Resume(argv: ["claude", "--resume", "w1"], cwd: "/c/quiver"))
        #expect(row.session.lastActiveAt == work.lastTimestamp)
        #expect(row.usage?.accountHome == "/Users/k/.quiver/accounts/claude/w/home")
    }

    @Test func usageRefreshesWithoutListingAgain() async throws {
        let ledger = FakeSessionLedger()
        let source = CountingSessionSource(SessionList(sessions: [session("a", "claude", cwd: nil, at: 1_791_699_000)], failures: []))
        let model = SessionsModel(source: source, ledger: ledger, now: { [now] in now }, calendar: calendar)
        await model.load()
        #expect(model.rows.first?.usage == nil)
        await ledger.set([usage("a", "claude", models: ["opus"], cost: 2)])
        await model.refreshUsage()
        #expect(model.rows.first?.usage?.totals.costUSD == 2)
        #expect(source.calls == 1)
    }

    @Test func aUsageRefreshDuringTheFirstLoadDoesNotStrandIt() async throws {
        let source = GatedSessionSource(SessionList(sessions: [session("a", "claude", cwd: nil, at: 1_791_699_000)], failures: []))
        let model = SessionsModel(source: source, ledger: FakeSessionLedger(), now: { [now] in now }, calendar: calendar)
        let load = Task { await model.load() }
        while !(await source.waiting) { await Task.yield() }
        await model.refreshUsage()
        await source.release()
        await load.value
        #expect(model.rows.map(\.id) == ["a"])
        #expect(!model.isLoading)
    }

    @Test func aFullListSaysItIsCapped() async {
        let model = await model([session("a", "claude", cwd: nil, at: 1_791_699_000),
                                 session("b", "claude", cwd: nil, at: 1_791_698_000)], [], limit: 2)
        await model.load()
        #expect(model.isCapped)
    }

    @Test func aFailedListingIsShown() async {
        let model = SessionsModel(
            source: FakeSessionSource(list: SessionList(sessions: [], failures: []), error: SweError.commandFailed(status: 1, message: "boom")),
            ledger: FakeSessionLedger(), now: { [now] in now })
        await model.load()
        #expect(model.error == "boom")
    }
}
