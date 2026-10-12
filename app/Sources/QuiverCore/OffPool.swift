import Foundation

/// Runs blocking work (libproc calls, subprocess reads, semaphore waits)
/// on a GCD queue instead of Swift's cooperative thread pool, so a slow
/// syscall can never starve other tasks or tests sharing the pool.
public func runOffPool<T: Sendable>(_ work: @escaping @Sendable () -> T) async -> T {
    await withCheckedContinuation { done in
        DispatchQueue.global(qos: .userInitiated).async {
            done.resume(returning: work())
        }
    }
}
