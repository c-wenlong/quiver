import Foundation
import Testing
@testable import QuiverCore

/// Holds a build on its background thread until the test lets it go.
/// The build blocks a GCD thread, as the slow filesystem calls it stands
/// in for do; the test side waits through a continuation, never polling.
private final class BuildGate: @unchecked Sendable {
    private let release = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var started: CheckedContinuation<Void, Never>?
    private var didStart = false

    /// Called by the build: says it has started, then waits for `open`.
    func hold() {
        lock.lock()
        didStart = true
        let waiter = started
        started = nil
        lock.unlock()
        waiter?.resume()
        release.wait()
    }

    /// Returns once the build is inside `hold`.
    func waitForStart() async {
        await withCheckedContinuation { (done: CheckedContinuation<Void, Never>) in
            lock.lock()
            if didStart {
                lock.unlock()
                done.resume()
            } else {
                started = done
                lock.unlock()
            }
        }
    }

    func open() { release.signal() }
}

@MainActor
@Suite struct LatestBuildTests {
    /// Opens `gate` and waits for the build it held to finish and be
    /// thrown away, so a check after it sees the drop, not a result still
    /// on its way. False if no drop comes (the build was published
    /// instead) within the gate timeout.
    private func openAndAwaitDrop(_ gate: BuildGate, on build: LatestBuild<some Sendable>) async -> Bool {
        let drops = CountGate()
        build.onDrop = { Task { await drops.bump() } }
        defer { build.onDrop = nil }
        gate.open()
        return await drops.reach(1)
    }

    @Test func theWorkRunsOffTheMainThreadAndPublishesOnIt() async {
        let build = LatestBuild<Bool>()
        var published: [Bool] = []
        build.start({ Thread.isMainThread }) { value in
            MainActor.assertIsolated()
            published.append(value)
        }
        #expect(build.isRunning)
        await build.settled()
        #expect(published == [false])
        #expect(!build.isRunning)
    }

    @Test(.timeLimit(.minutes(1))) func aSlowOlderBuildNeverOverwritesANewerOne() async {
        let build = LatestBuild<String>()
        let gate = BuildGate()
        var published: [String] = []
        build.start({ gate.hold(); return "old" }) { published.append($0) }
        await gate.waitForStart()
        build.start({ "new" }) { published.append($0) }
        await build.settled()
        #expect(published == ["new"])
        // The old one finishes late, and is dropped.
        #expect(await openAndAwaitDrop(gate, on: build))
        #expect(published == ["new"])
    }

    @Test func settledWaitsThroughASupersededBuild() async {
        let build = LatestBuild<String>()
        let gate = BuildGate()
        let second = BuildGate()
        var published: [String] = []
        build.start({ gate.hold(); return "first" }) { published.append($0) }
        await gate.waitForStart()
        build.start({ second.hold(); return "second" }) { published.append($0) }
        await second.waitForStart()
        // The first one finishing does not end the wait: the second is
        // the one on show next.
        gate.open()
        second.open()
        await build.settled()
        #expect(published == ["second"])
    }

    /// Starts a `settled()` caller and returns once it is waiting.
    private func parkedWaiter(on build: LatestBuild<String>) async -> Task<Void, Never> {
        await withCheckedContinuation { (parked: CheckedContinuation<Task<Void, Never>, Never>) in
            var waiter: Task<Void, Never>?
            build.onWait = {
                build.onWait = nil
                parked.resume(returning: waiter!)
            }
            waiter = Task { await build.settled() }
        }
    }

    @Test func aWaiterGoesOnWhenANewerBuildPublishesWhileTheOldOneIsStuck() async {
        let build = LatestBuild<String>()
        let stuck = BuildGate()
        var published: [String] = []
        build.start({ stuck.hold(); return "stuck" }) { published.append($0) }
        await stuck.waitForStart()
        let waiter = await parkedWaiter(on: build)
        // Waiting on the stuck build's own task would never end.
        build.start({ "new" }) { published.append($0) }
        await waiter.value
        #expect(published == ["new"])
        stuck.open()
    }

    @Test func aWaiterGoesOnWhenTheStuckBuildIsCancelled() async {
        let build = LatestBuild<String>()
        let stuck = BuildGate()
        build.start({ stuck.hold(); return "stuck" }) { _ in }
        await stuck.waitForStart()
        let waiter = await parkedWaiter(on: build)
        build.cancel()
        await waiter.value
        #expect(!build.isRunning)
        stuck.open()
    }

    @Test(.timeLimit(.minutes(1))) func aCancelledBuildIsNeverPublished() async {
        let build = LatestBuild<Int>()
        let gate = BuildGate()
        var published: [Int] = []
        build.start({ gate.hold(); return 1 }) { published.append($0) }
        await gate.waitForStart()
        build.cancel()
        #expect(!build.isRunning)
        // Nothing left to wait for.
        await build.settled()
        // The cancelled build finishes and is dropped, unpublished.
        #expect(await openAndAwaitDrop(gate, on: build))
        #expect(published.isEmpty)
        // A new one after it is published as usual.
        build.start({ 2 }) { published.append($0) }
        await build.settled()
        #expect(published == [2])
    }
}
