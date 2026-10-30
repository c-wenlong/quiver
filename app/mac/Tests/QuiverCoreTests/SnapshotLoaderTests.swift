import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// A page's value, as small as a test needs.
struct Names: SnapshotValue {
    static let snapshotVersion = 1
    var names: [String]
}

/// Keeps the last snapshot in memory.
actor MemorySnapshotStore<Content: SnapshotContent>: SnapshotStoring {
    var saved: Content?
    private(set) var saves = 0
    init(_ saved: Content? = nil) { self.saved = saved }
    func load() async -> Content? { saved }
    func save(_ content: Content) async {
        saved = content
        saves += 1
    }
}

/// A saved snapshot whose read waits until released.
actor PausedReadStore<Content: SnapshotContent>: SnapshotStoring {
    let saved: Content
    private var gate: CheckedContinuation<Void, Never>?
    /// Bumped once a read is parked, so a test waits for it without spinning.
    let parks = CountGate()
    init(_ saved: Content) { self.saved = saved }
    func load() async -> Content? {
        let parks = self.parks
        await withCheckedContinuation {
            gate = $0
            Task { await parks.bump() }
        }
        return saved
    }
    func save(_ content: Content) async {}
    func release() { gate?.resume(); gate = nil }
}

/// Answers each fetch with the next value, counting calls.
actor NamesSource {
    private var replies: [Result<Names, Error>]
    private(set) var calls = 0
    private var gate: CheckedContinuation<Void, Never>?
    private let gated: Bool
    /// Bumped once a gated fetch is parked, so a test waits for it without
    /// spinning.
    let parks = CountGate()

    init(_ replies: [Result<Names, Error>], gated: Bool = false) {
        self.replies = replies
        self.gated = gated
    }

    func fetch() async throws -> Names {
        calls += 1
        if gated {
            let parks = self.parks
            await withCheckedContinuation {
                gate = $0
                Task { await parks.bump() }
            }
        }
        try Task.checkCancellation()
        return try (replies.count > 1 ? replies.removeFirst() : replies[0]).get()
    }

    func release() { gate?.resume(); gate = nil }
}

@MainActor
@Suite struct SnapshotLoaderTests {
    struct Down: Error {}
    let start = Date(timeIntervalSince1970: 1_791_700_000)

    func loader(_ store: MemorySnapshotStore<Snapshot<Names>>? = MemorySnapshotStore(), clock: TestClock,
                timeout: Duration = .seconds(60)) -> SnapshotLoader<Names> {
        SnapshotLoader(snapshots: store, maxAge: 60, timeout: timeout, now: { clock.now })
    }

    func names(_ loader: SnapshotLoader<Names>) -> [String]? {
        if case .loaded(let value) = loader.state { return value.names }
        return nil
    }

    @Test func aLoadedValueIsShownAtOnceByTheNextLaunch() async throws {
        let store = MemorySnapshotStore<Snapshot<Names>>()
        let clock = TestClock(start)
        let first = loader(store, clock: clock)
        let source = NamesSource([.success(Names(names: ["a", "b"]))])
        await first.load(from: "/bin/swe") { try await source.fetch() }
        #expect(await store.saves == 1)

        let next = loader(store, clock: clock)
        #expect(next.state == .idle)
        await next.restore()
        #expect(names(next) == ["a", "b"])
    }

    @Test func aFreshValueIsNotFetchedAgain() async throws {
        let source = NamesSource([.success(Names(names: ["a"]))])
        let clock = TestClock(start)
        let loader = loader(clock: clock)
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        #expect(await source.calls == 1)

        clock.advance(61)
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        #expect(await source.calls == 2)

        // A different swe fetches again at once.
        await loader.loadIfStale(from: "/other/swe") { try await source.fetch() }
        #expect(await source.calls == 3)

        // Refresh always fetches.
        await loader.load(from: nil) { try await source.fetch() }
        #expect(await source.calls == 4)
    }

    @Test func leavingThePageMidFetchDoesNotLoseTheValue() async throws {
        let source = NamesSource([.success(Names(names: ["a"]))], gated: true)
        let loader = loader(clock: TestClock(start))
        // The page opens, then closes while swe is still running.
        let first = Task { await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() } }
        try #require(await source.parks.reach(1))
        first.cancel()
        #expect(loader.isLoading)
        // Reopened before swe answers: it joins the fetch in flight.
        let reopened = Task { await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() } }
        await source.release()
        await reopened.value
        #expect(names(loader) == ["a"])
        #expect(!loader.isLoading)
        #expect(await source.calls == 1)
    }

    @Test func aFetchThatNeverAnswersGivesUpAndCanBeRetried() async throws {
        let calls = Mutex(0)
        // A swe that never answers, until it is cancelled.
        let hang: @Sendable () async throws -> Names = {
            calls.withLock { $0 += 1 }
            try await Task.sleep(for: .seconds(3600))
            return Names(names: [])
        }
        let loader = loader(clock: TestClock(start), timeout: .milliseconds(50))
        await loader.load(from: "/bin/swe", fetch: hang)
        #expect(!loader.isLoading)
        guard case .failed(let message) = loader.state else { Issue.record("\(loader.state)"); return }
        #expect(message.contains("didn't answer"))
        await loader.loadIfStale(from: "/bin/swe", fetch: hang)
        #expect(calls.withLock { $0 } == 2)
    }

    @Test func aSavedValueIsRefreshedOnceItIsOld() async throws {
        let old = Snapshot(source: "/bin/swe", savedAt: start.addingTimeInterval(-3600), value: Names(names: ["old"]))
        let source = NamesSource([.success(Names(names: ["new"]))])
        let loader = loader(MemorySnapshotStore(old), clock: TestClock(start))
        await loader.restore()
        #expect(names(loader) == ["old"])
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        #expect(await source.calls == 1)
        #expect(names(loader) == ["new"])
    }

    @Test func aRecentSavedValueFromTheSameSweIsUsedAsIs() async throws {
        let recent = Snapshot(source: "/bin/swe", savedAt: start.addingTimeInterval(-20), value: Names(names: ["a"]))
        let source = NamesSource([.success(Names(names: []))])
        let loader = loader(MemorySnapshotStore(recent), clock: TestClock(start))
        await loader.restore()
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        #expect(await source.calls == 0)
        #expect(names(loader) == ["a"])
        // From another swe it is old news.
        await loader.loadIfStale(from: "/other/swe") { try await source.fetch() }
        #expect(await source.calls == 1)
    }

    @Test func theSavedValueNeverReplacesOneAlreadyFetched() async throws {
        let saved = Snapshot(source: "/bin/swe", savedAt: start, value: Names(names: ["saved"]))
        let loader = loader(MemorySnapshotStore(saved), clock: TestClock(start))
        await loader.load(from: "/bin/swe") { Names(names: ["live"]) }
        await loader.restore()
        #expect(names(loader) == ["live"])
    }

    @Test func aSlowSavedReadNeverReplacesALoadThatFinishedMeanwhile() async throws {
        for live in [Result<Names, Error>.success(Names(names: ["live"])), .failure(Down())] {
            let saved = Snapshot(source: "/bin/swe", savedAt: start, value: Names(names: ["saved"]))
            let store = PausedReadStore(saved)
            let loader = SnapshotLoader(snapshots: store, now: { [start] in start })
            let restoring = Task { await loader.restore() }
            try #require(await store.parks.reach(1))
            await loader.load(from: "/bin/swe") { try live.get() }
            let shown = loader.state
            await store.release()
            await restoring.value
            #expect(loader.state == shown)
            if case .loaded(let value) = loader.state { #expect(value.names == ["live"]) }
        }
    }

    @Test func aFetchThatIgnoresCancellationStillTimesOutOnTime() async throws {
        // A swe that shrugs off SIGTERM: its runner returns only when it exits.
        let stubborn: @Sendable () async throws -> Names = {
            // Shrugs off cancellation without spinning: a checked
            // continuation is never resumed by cancellation, so the
            // fetch still waits out the full hold.
            await withCheckedContinuation { (parked: CheckedContinuation<Void, Never>) in
                DispatchQueue.global().asyncAfter(deadline: .now() + 60) { parked.resume() }
            }
            return Names(names: ["late"])
        }
        let loader = loader(clock: TestClock(start), timeout: .milliseconds(50))
        let elapsed = await ContinuousClock().measure {
            await loader.load(from: "/bin/swe", fetch: stubborn)
        }
        // The bound catches a hang, not latency: it sits under the
        // source's 60s hold, so a broken timeout still fails while a
        // starved CI runner cannot trip it.
        #expect(elapsed < .seconds(30))
        #expect(!loader.isLoading)
        guard case .failed = loader.state else { Issue.record("\(loader.state)"); return }
    }

    @Test func aReloadAfterAChangeIsDroppedOnceAnotherSweIsAskedFor() async throws {
        let loader = loader(clock: TestClock(start))
        await loader.load(from: "/a/swe") { Names(names: ["a"]) }
        await loader.loadIfStale(from: "/b/swe") { Names(names: ["b"]) }
        // A change made through /a/swe finishes after Settings moved to /b/swe.
        await loader.reload(from: "/a/swe") { Names(names: ["a, changed"]) }
        #expect(names(loader) == ["b"])
        // Through the swe in use, it reloads.
        await loader.reload(from: "/b/swe") { Names(names: ["b, changed"]) }
        #expect(names(loader) == ["b, changed"])
    }

    @Test func aSavedValueDoesNotHideAFailure() async throws {
        let saved = Snapshot(source: "/bin/swe", savedAt: start, value: Names(names: ["saved"]))
        let loader = loader(MemorySnapshotStore(saved), clock: TestClock(start))
        await loader.load(from: "/bin/swe") { throw Down() }
        await loader.restore()
        guard case .failed = loader.state else { Issue.record("\(loader.state)"); return }
    }

    @Test func aFailedFetchIsNotSavedAndIsTriedAgainNextTime() async throws {
        let store = MemorySnapshotStore<Snapshot<Names>>()
        let source = NamesSource([.failure(Down()), .success(Names(names: ["a"]))])
        let loader = loader(store, clock: TestClock(start))
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        guard case .failed = loader.state else { Issue.record("\(loader.state)"); return }
        #expect(await store.saves == 0)
        await loader.loadIfStale(from: "/bin/swe") { try await source.fetch() }
        #expect(names(loader) == ["a"])
        #expect(await store.saves == 1)
    }

    @Test func onlyTheNewestLoadIsShown() async throws {
        let slow = NamesSource([.success(Names(names: ["slow"]))], gated: true)
        let loader = loader(clock: TestClock(start))
        let first = Task { await loader.load(from: "/bin/swe") { try await slow.fetch() } }
        try #require(await slow.parks.reach(1))
        let applied = await loader.load(from: "/bin/swe") { Names(names: ["fast"]) }
        #expect(applied)
        await slow.release()
        let stale = await first.value
        #expect(!stale)
        #expect(names(loader) == ["fast"])
    }

    @Test func aLoadedValueStaysOnShowWhileItRefreshes() async throws {
        let source = NamesSource([.success(Names(names: ["b"]))], gated: true)
        let loader = loader(clock: TestClock(start))
        await loader.load(from: "/bin/swe") { Names(names: ["a"]) }
        let refresh = Task { await loader.load(from: "/bin/swe") { try await source.fetch() } }
        try #require(await source.parks.reach(1))
        #expect(names(loader) == ["a"])
        #expect(loader.isLoading)
        await source.release()
        await refresh.value
        #expect(names(loader) == ["b"])
    }
}

@Suite struct SnapshotFileTests {
    func temporaryURL() -> URL {
        FileManager.default.temporaryDirectory
            .appending(path: "quiver-snapshot-\(UUID().uuidString)/cache/names.json")
    }

    @Test func aSnapshotRoundTripsThroughAPrivateFile() async throws {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent().deletingLastPathComponent()) }
        let file = SnapshotFile<Snapshot<Names>>(url: url)
        let snapshot = Snapshot(source: "/bin/swe", savedAt: Date(timeIntervalSince1970: 1_791_700_000.25),
                                value: Names(names: ["a", "b"]))
        await file.save(snapshot)
        #expect(await file.load() == snapshot)
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
        let folder = try FileManager.default.attributesOfItem(atPath: url.deletingLastPathComponent().path)
        #expect(folder[.posixPermissions] as? Int == 0o700)
    }

    @Test func aSnapshotOfAnotherShapeIsIgnored() async throws {
        let url = temporaryURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent().deletingLastPathComponent()) }
        let file = SnapshotFile<Snapshot<Names>>(url: url)
        #expect(await file.load() == nil)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("{not json".utf8).write(to: url)
        #expect(await file.load() == nil)
        var other = Snapshot(source: "/bin/swe", savedAt: Date(timeIntervalSince1970: 1), value: Names(names: []))
        other.version = Names.snapshotVersion + 1
        try JSONEncoder().encode(other).write(to: url)
        #expect(await file.load() == nil)
    }
}
