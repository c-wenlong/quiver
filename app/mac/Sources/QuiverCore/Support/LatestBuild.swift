import Foundation

/// Runs `work` on a global dispatch queue and returns its result: for disk
/// work that may block (`realpath(2)` or `fileExists` on a synced or stale
/// folder). A blocked GCD thread costs nothing else; a blocked thread of
/// the concurrency pool, which `Task.detached` would use, starves every
/// task in the app. Cancelling the caller does not stop `work`, so check
/// `Task.isCancelled` before using the result.
public func offMain<Value: Sendable>(_ work: @escaping @Sendable () -> Value) async -> Value {
    await withCheckedContinuation { (done: CheckedContinuation<Value, Never>) in
        DispatchQueue.global(qos: .userInitiated).async { done.resume(returning: work()) }
    }
}

/// Work too slow for the main actor, run off it with only the newest
/// start's result kept.
///
/// `start` runs the work on a global dispatch queue rather than the
/// concurrency pool: the work this is for (the Plugins page's index, the
/// Skills page's placement) calls `realpath(2)` and `fileExists` per
/// folder, and a synced or stale folder can block those for as long as it
/// likes. A blocked GCD thread costs nothing else; a blocked pool thread
/// starves every task in the app.
///
/// Each `start` or `cancel` moves to a new generation, and a result from an
/// older one is dropped, so a slow build can never overwrite a newer one.
@MainActor
public final class LatestBuild<Value: Sendable> {
    private var generation = 0
    /// Callers of `settled()`, woken when the latest build publishes or is
    /// cancelled. Never tied to one build's task: a build stuck on disk
    /// must not hold a waiter once a newer one has published.
    private var waiters: [CheckedContinuation<Void, Never>] = []
    /// Called each time a `settled()` caller starts waiting; for tests.
    var onWait: (() -> Void)?
    /// Called when a superseded build finishes and its result is thrown
    /// away; for tests, which otherwise can't see a result not arriving.
    var onDrop: (() -> Void)?

    public init() {}

    /// A build is in flight whose result will be published.
    public private(set) var isRunning = false

    /// Runs `work` off the main actor, then hands its result to `publish`
    /// on the main actor, unless `start` or `cancel` was called again
    /// meanwhile.
    public func start(_ work: @escaping @Sendable () -> Value,
                      publish: @escaping @MainActor (Value) -> Void) {
        generation += 1
        let mine = generation
        isRunning = true
        Task { [weak self] in
            let value = await offMain(work)
            guard let self else { return }
            guard self.generation == mine else {
                self.onDrop?()
                return
            }
            self.isRunning = false
            publish(value)
            self.wake()
        }
    }

    /// Drops the build in flight: its result is never published, and
    /// whoever waits on `settled()` goes on.
    public func cancel() {
        generation += 1
        isRunning = false
        wake()
    }

    /// Returns once the latest start has published, or been cancelled.
    /// A start made while waiting is waited for too.
    public func settled() async {
        guard isRunning else { return }
        await withCheckedContinuation { (waiter: CheckedContinuation<Void, Never>) in
            waiters.append(waiter)
            onWait?()
        }
    }

    private func wake() {
        let woken = waiters
        waiters = []
        for waiter in woken { waiter.resume() }
    }
}
