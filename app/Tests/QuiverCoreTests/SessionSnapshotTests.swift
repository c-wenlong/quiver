import Foundation
import Testing
@testable import QuiverCore

/// Keeps the last snapshot in memory.
actor MemorySnapshots: SessionSnapshotStoring {
    var saved: SessionSnapshot?
    private(set) var saves = 0
    init(_ saved: SessionSnapshot? = nil) { self.saved = saved }
    func load() async -> SessionSnapshot? { saved }
    func save(_ snapshot: SessionSnapshot) async {
        saved = snapshot
        saves += 1
    }
}

@MainActor
@Suite struct SessionSnapshotTests {
    let start = Date(timeIntervalSince1970: 1_791_700_000)

    func session(_ id: String, at: Double = 1_791_699_000) -> SessionInfo {
        SessionInfo(id: id, harness: "claude", agent: "Claude", cwd: "/c/quiver", title: "Session \(id)",
                    titleSource: nil, lastActiveAt: Date(timeIntervalSince1970: at), status: "done",
                    resume: .init(argv: ["claude", "--resume", id], cwd: "/c/quiver"))
    }

    func usage(_ id: String, cost: Double) -> SessionUsage {
        var totals = UsageTotals()
        totals.inputTokens = 100
        totals.costUSD = cost
        totals.unpricedModels = ["mystery"]
        return SessionUsage(sessionId: id, harness: "claude", accountHome: "/Users/k/.claude", cwd: "/c/quiver",
                            models: ["claude-opus-5-5"], totals: totals,
                            firstTimestamp: start.addingTimeInterval(-600), lastTimestamp: start)
    }

    func model(source: SessionListing, ledger: FakeSessionLedger, snapshots: SessionSnapshotStoring,
               clock: TestClock) -> SessionsModel {
        SessionsModel(source: source, ledger: ledger, defaultHomes: ["/Users/k/.claude", "/Users/k/.codex"],
                      snapshots: snapshots, maxAge: 60, now: { clock.now })
    }

    @Test func aLoadedListIsShownAtOnceByTheNextLaunch() async throws {
        let snapshots = MemorySnapshots()
        let ledger = FakeSessionLedger()
        await ledger.set([usage("a", cost: 2.5)])
        let clock = TestClock(start)
        let first = model(source: FakeSessionSource(list: SessionList(sessions: [session("a"), session("b")],
                                                                      failures: ["droid: unreadable"])),
                          ledger: ledger, snapshots: snapshots, clock: clock)
        await first.load(for: "/bin/swe")
        #expect(await snapshots.saves == 1)

        // The next launch: swe hasn't answered yet, the saved list shows.
        let gated = GatedSessionSource(SessionList(sessions: [], failures: []))
        let next = model(source: gated, ledger: FakeSessionLedger(), snapshots: snapshots, clock: clock)
        await next.restore()
        #expect(next.rows == first.rows)
        #expect(next.rows.first?.usage?.totals.costUSD == 2.5)
        #expect(next.failures == ["droid: unreadable"])
        #expect(await gated.waiting == false)
    }

    @Test func aFreshListIsNotFetchedAgain() async throws {
        let source = CountingSessionSource(SessionList(sessions: [session("a")], failures: []))
        let clock = TestClock(start)
        let model = model(source: source, ledger: FakeSessionLedger(), snapshots: MemorySnapshots(), clock: clock)

        await model.loadIfStale(for: "/bin/swe")
        await model.loadIfStale(for: "/bin/swe")
        #expect(source.calls == 1)

        clock.advance(61)
        await model.loadIfStale(for: "/bin/swe")
        #expect(source.calls == 2)

        // A different swe lists again at once.
        await model.loadIfStale(for: "/other/swe")
        #expect(source.calls == 3)

        // Refresh always lists.
        await model.load()
        #expect(source.calls == 4)
    }

    @Test func aSavedListIsRefreshedOnceItIsOld() async throws {
        let old = SessionSnapshot(source: "/bin/swe", savedAt: start.addingTimeInterval(-3600),
                                  sessions: [session("old")], failures: [], isCapped: false, usage: [])
        let source = CountingSessionSource(SessionList(sessions: [session("new")], failures: []))
        let model = model(source: source, ledger: FakeSessionLedger(), snapshots: MemorySnapshots(old),
                          clock: TestClock(start))
        await model.restore()
        #expect(model.rows.map(\.id) == ["old"])
        await model.loadIfStale(for: "/bin/swe")
        #expect(source.calls == 1)
        #expect(model.rows.map(\.id) == ["new"])
    }

    @Test func aRecentSavedListFromTheSameSweIsUsedAsIs() async throws {
        let recent = SessionSnapshot(source: "/bin/swe", savedAt: start.addingTimeInterval(-20),
                                     sessions: [session("a")], failures: [], isCapped: false, usage: [])
        let source = CountingSessionSource(SessionList(sessions: [], failures: []))
        let model = model(source: source, ledger: FakeSessionLedger(), snapshots: MemorySnapshots(recent),
                          clock: TestClock(start))
        await model.restore()
        await model.loadIfStale(for: "/bin/swe")
        #expect(source.calls == 0)
        #expect(model.rows.map(\.id) == ["a"])
    }

    @Test func theSavedListNeverReplacesOneAlreadyListed() async throws {
        let saved = SessionSnapshot(source: "/bin/swe", savedAt: start, sessions: [session("saved")],
                                    failures: [], isCapped: false, usage: [])
        let model = model(source: FakeSessionSource(list: SessionList(sessions: [session("live")], failures: [])),
                          ledger: FakeSessionLedger(), snapshots: MemorySnapshots(saved), clock: TestClock(start))
        await model.load(for: "/bin/swe")
        await model.restore()
        #expect(model.rows.map(\.id) == ["live"])
    }

    @Test func aFailedListingKeepsTheLastListAndTriesAgainNextTime() async throws {
        struct Down: Error {}
        let saved = SessionSnapshot(source: "/bin/swe", savedAt: start.addingTimeInterval(-3600),
                                    sessions: [session("a")], failures: [], isCapped: false, usage: [])
        let snapshots = MemorySnapshots(saved)
        let model = model(source: FakeSessionSource(list: SessionList(sessions: [], failures: []), error: Down()),
                          ledger: FakeSessionLedger(), snapshots: snapshots, clock: TestClock(start))
        await model.restore()
        await model.loadIfStale(for: "/bin/swe")
        #expect(model.rows.map(\.id) == ["a"])
        #expect(model.error != nil)
        #expect(await snapshots.saves == 0)
    }

    @Test func newUsageFromAScanIsSavedToo() async throws {
        let snapshots = MemorySnapshots()
        let ledger = FakeSessionLedger()
        let model = model(source: FakeSessionSource(list: SessionList(sessions: [session("a")], failures: [])),
                          ledger: ledger, snapshots: snapshots, clock: TestClock(start))
        await model.load(for: "/bin/swe")
        await ledger.set([usage("a", cost: 7)])
        await model.refreshUsage()
        #expect(await snapshots.saved?.usage.first?.totals.costUSD == 7)
    }
}

@Suite struct SessionSnapshotFileTests {
    func temporaryURL() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "quiver-snapshot-\(UUID().uuidString)/cache/sessions.json")
    }

    @Test func aSnapshotRoundTripsThroughAPrivateFile() async throws {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent().deletingLastPathComponent()) }
        let file = SessionSnapshotFile(url: url)
        var totals = UsageTotals()
        totals.cacheReadTokens = 42
        totals.costUSD = 1.25
        totals.unpricedModels = ["x"]
        let snapshot = SessionSnapshot(
            source: "/bin/swe", savedAt: Date(timeIntervalSince1970: 1_791_700_000.25),
            sessions: [SessionInfo(id: "a", harness: "codex", agent: "Codex", cwd: nil, title: nil, titleSource: "first",
                                   lastActiveAt: Date(timeIntervalSince1970: 1_791_699_000.5), status: nil,
                                   resume: .init(argv: ["codex", "resume", "a"], cwd: nil))],
            failures: ["droid: unreadable"], isCapped: true,
            usage: [SessionUsage(sessionId: "a", harness: "codex", accountHome: "/h", cwd: "/p", models: ["gpt"],
                                 totals: totals, firstTimestamp: .init(timeIntervalSince1970: 1),
                                 lastTimestamp: .init(timeIntervalSince1970: 2))])
        await file.save(snapshot)
        #expect(await file.load() == snapshot)
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
    }

    @Test func aMissingOrDamagedFileIsNoSnapshot() async throws {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent().deletingLastPathComponent()) }
        let file = SessionSnapshotFile(url: url)
        #expect(await file.load() == nil)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("{not json".utf8).write(to: url)
        #expect(await file.load() == nil)
        try Data(#"{"version": 99}"#.utf8).write(to: url)
        #expect(await file.load() == nil)
    }
}
