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

    @Test func aSlowOlderBuildNeverOverwritesANewerOne() async {
        let build = LatestBuild<String>()
        let gate = BuildGate()
        var published: [String] = []
        build.start({ gate.hold(); return "old" }) { published.append($0) }
        await gate.waitForStart()
        build.start({ "new" }) { published.append($0) }
        await build.settled()
        #expect(published == ["new"])
        // The old one finishes late, and is dropped.
        gate.open()
        await build.settled()
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

    @Test func aCancelledBuildIsNeverPublished() async {
        let build = LatestBuild<Int>()
        let gate = BuildGate()
        var published: [Int] = []
        build.start({ gate.hold(); return 1 }) { published.append($0) }
        await gate.waitForStart()
        build.cancel()
        #expect(!build.isRunning)
        // Nothing left to wait for.
        await build.settled()
        gate.open()
        // Let the dropped build finish, then start and settle a new one:
        // only the new one is published.
        build.start({ 2 }) { published.append($0) }
        await build.settled()
        #expect(published == [2])
    }
}
