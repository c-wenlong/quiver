import Foundation
import Synchronization

/// `operation`'s result, or `onTimeout()`'s error once `timeout` passes.
///
/// Losing the race cancels the operation, which stops a swe process, but
/// the caller doesn't wait for it to wind down: a swe that ignores SIGTERM
/// would otherwise hold the caller until it exits on its own.
func withDeadline<T: Sendable>(_ timeout: Duration, onTimeout: @escaping @Sendable () -> Error,
                               _ operation: @escaping @Sendable () async throws -> T) async throws -> T {
    let work = Task { try await operation() }
    let outcome = Outcome<T>()
    return try await withTaskCancellationHandler {
        try await withCheckedThrowingContinuation { continuation in
            outcome.start(continuation)
            let timer = Task {
                try await Task.sleep(for: timeout)
                outcome.finish(.failure(onTimeout()))
                work.cancel()
            }
            Task {
                let result = await work.result
                timer.cancel()
                outcome.finish(result)
            }
        }
    } onCancel: {
        work.cancel()
        outcome.finish(.failure(CancellationError()))
    }
}

/// Resumes the continuation with the first result offered. A result that
/// comes before the continuation is set (a cancel at once) is kept for it.
private final class Outcome<T: Sendable>: Sendable {
    private struct State {
        var continuation: CheckedContinuation<T, Error>?
        var early: Result<T, Error>?
        var done = false
    }

    private let state = Mutex(State())

    func start(_ continuation: CheckedContinuation<T, Error>) {
        let early: Result<T, Error>? = state.withLock {
            if let early = $0.early { return early }
            $0.continuation = continuation
            return nil
        }
        if let early { continuation.resume(with: early) }
    }

    func finish(_ result: Result<T, Error>) {
        let continuation: CheckedContinuation<T, Error>? = state.withLock {
            guard !$0.done else { return nil }
            $0.done = true
            guard let continuation = $0.continuation else {
                $0.early = result
                return nil
            }
            $0.continuation = nil
            return continuation
        }
        continuation?.resume(with: result)
    }
}
