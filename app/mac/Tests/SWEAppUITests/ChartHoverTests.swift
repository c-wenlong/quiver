import Testing
@testable import SWEApp

/// A sleep the test fires by hand. Each call parks until `fire` or until
/// its task is cancelled, which throws as `Task.sleep` does.
@MainActor
private final class ManualSleep {
    private(set) var durations: [Duration] = []
    private var parked: [Int: CheckedContinuation<Void, Error>] = [:]
    private var cancelled: Set<Int> = []
    /// Someone waiting for the sleep count to reach a number.
    private var countWaiter: (count: Int, continuation: CheckedContinuation<Void, Never>)?
    /// How many sleeps were cancelled, and someone waiting for a number.
    private var cancellations = 0
    private var cancelWaiter: (count: Int, continuation: CheckedContinuation<Void, Never>)?

    /// Returns once `count` sleeps have been cancelled. A cancellation
    /// lands through its own task, so a test waits for it rather than
    /// guessing with yields.
    func cancelled(_ count: Int) async {
        if cancellations >= count { return }
        await withCheckedContinuation { cancelWaiter = (count, $0) }
    }

    /// Returns once `count` sleeps have started: `enter` and `leave` only
    /// schedule the wait, so a test fires a sleep after it exists.
    func started(_ count: Int) async {
        if durations.count >= count { return }
        await withCheckedContinuation { countWaiter = (count, $0) }
    }

    func sleep(_ duration: Duration) async throws {
        let id = durations.count
        durations.append(duration)
        if let waiter = countWaiter, durations.count >= waiter.count {
            countWaiter = nil
            waiter.continuation.resume()
        }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                if cancelled.contains(id) { continuation.resume(throwing: CancellationError()) }
                else { parked[id] = continuation }
            }
        } onCancel: {
            Task { @MainActor in self.cancel(id) }
        }
    }

    var waiting: Int { parked.count }

    func fire(_ id: Int) {
        parked.removeValue(forKey: id)?.resume()
    }

    private func cancel(_ id: Int) {
        cancelled.insert(id)
        parked.removeValue(forKey: id)?.resume(throwing: CancellationError())
        cancellations += 1
        if let waiter = cancelWaiter, cancellations >= waiter.count {
            cancelWaiter = nil
            waiter.continuation.resume()
        }
    }
}

@MainActor
@Suite struct ChartHoverTests {
    private func make() -> (ChartHover, ManualSleep) {
        let sleep = ManualSleep()
        return (ChartHover(sleep: { try await sleep.sleep($0) }), sleep)
    }

    @Test func theFirstCardWaitsTheOpenDelay() async {
        let (hover, sleep) = make()
        hover.enter("2")
        await sleep.started(1)
        #expect(hover.shown == nil)
        #expect(sleep.durations == [ChartHover.openDelay])
        sleep.fire(0)
        await hover.settle()
        #expect(hover.shown == "2")
    }

    @Test func theCardFollowsItsModelWhenRowsChange() async {
        let (hover, sleep) = make()
        hover.enter("b")
        await sleep.started(1)
        sleep.fire(0)
        await hover.settle()
        #expect(hover.index(in: ["a", "b", "c"]) == 1)
        // A rebuild drops "a": the card moves with "b", not index 1.
        #expect(hover.index(in: ["b", "c"]) == 0)
        #expect(hover.index(in: ["new", "c", "b"]) == 2)
        // "b" is gone: no bar, so no card.
        #expect(hover.index(in: ["a", "c"]) == nil)
    }

    @Test func withACardUpTheNextBarSwitchesAtOnce() async {
        let (hover, sleep) = make()
        hover.enter("2")
        await sleep.started(1)
        sleep.fire(0)
        await hover.settle()
        hover.enter("3")
        #expect(hover.shown == "3")
        #expect(sleep.durations.count == 1, "no second wait")
    }

    @Test func enteringTheNextBarInsideTheCloseWindowSwitchesAndCancelsTheClose() async {
        let (hover, sleep) = make()
        hover.enter("2")
        await sleep.started(1)
        sleep.fire(0)
        await hover.settle()
        hover.leave("2")
        await sleep.started(2)
        #expect(sleep.durations.last == ChartHover.closeDelay)
        #expect(hover.shown == "2", "the card outlives its bar")
        hover.enter("3")
        #expect(hover.shown == "3")
        await sleep.cancelled(1)
        #expect(sleep.waiting == 0, "the close was cancelled")
        await hover.settle()
        #expect(hover.shown == "3")
    }

    @Test func leavingClosesAfterTheCloseDelay() async {
        let (hover, sleep) = make()
        hover.enter("2")
        await sleep.started(1)
        sleep.fire(0)
        await hover.settle()
        hover.leave("2")
        await sleep.started(2)
        sleep.fire(1)
        await hover.settle()
        #expect(hover.shown == nil)
    }

    @Test func aLateLeaveFromTheLastBarIsIgnored() async {
        let (hover, sleep) = make()
        hover.enter("2")
        await sleep.started(1)
        sleep.fire(0)
        await hover.settle()
        hover.enter("3")
        hover.leave("2")
        #expect(hover.shown == "3")
        #expect(sleep.durations.count == 1)
    }

    @Test func leavingBeforeTheCardOpensCancelsIt() async {
        let (hover, sleep) = make()
        hover.enter("2")
        await sleep.started(1)
        hover.leave("2")
        await sleep.started(2)
        await sleep.cancelled(1)
        // The open wait is gone; only the close wait is left.
        #expect(sleep.waiting == 1)
        #expect(sleep.durations == [ChartHover.openDelay, ChartHover.closeDelay])
        sleep.fire(1)
        await hover.settle()
        #expect(hover.shown == nil)
    }
}
