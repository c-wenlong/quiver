import Foundation

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
    private var running: Task<Void, Never>?

    public init() {}

    /// Runs `work` off the main actor, then hands its result to `publish`
    /// on the main actor, unless `start` or `cancel` was called again
    /// meanwhile.
    public func start(_ work: @escaping @Sendable () -> Value,
                      publish: @escaping @MainActor (Value) -> Void) {
        generation += 1
        let mine = generation
        running = Task { [weak self] in
            let value = await withCheckedContinuation { (done: CheckedContinuation<Value, Never>) in
                DispatchQueue.global(qos: .userInitiated).async { done.resume(returning: work()) }
            }
            guard let self, self.generation == mine else { return }
            self.running = nil
            publish(value)
        }
    }

    /// Drops the build in flight: its result is never published.
    public func cancel() {
        generation += 1
        running = nil
    }

    /// A build is in flight whose result will be published.
    public var isRunning: Bool { running != nil }

    /// Returns once the latest start has published, or been cancelled.
    public func settled() async {
        while let current = running {
            await current.value
            if running == current { return }
        }
    }
}
