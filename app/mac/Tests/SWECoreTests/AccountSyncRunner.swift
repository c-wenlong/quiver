import Foundation
@testable import SWECore

/// A one-shot latch: `wait` returns once `signal` has been called.
final class AccountSyncLatch: @unchecked Sendable {
    private let lock = NSLock()
    private var fired = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func signal() {
        lock.lock()
        fired = true
        let pending = waiters
        waiters = []
        lock.unlock()
        pending.forEach { $0.resume() }
    }

    func wait() async {
        await withCheckedContinuation { continuation in
            lock.lock()
            if fired {
                lock.unlock()
                continuation.resume()
            } else {
                waiters.append(continuation)
                lock.unlock()
            }
        }
    }
}

/// For `AccountSyncTests`: `config set` can fail or be held until the test
/// releases it; every `init` call answers with the captured fixture.
final class AccountSyncRunner: CommandRunning, @unchecked Sendable {
    let configEntered = AccountSyncLatch()
    let releaseConfig = AccountSyncLatch()
    private let lock = NSLock()
    private var recorded: [[String]] = []
    private let fixture: Data
    private let holdConfig: Bool
    private let configFails: Bool

    init(fixture: Data, holdConfig: Bool, configFails: Bool) {
        self.fixture = fixture
        self.holdConfig = holdConfig
        self.configFails = configFails
    }

    var calls: [[String]] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { recorded.append(arguments) }
        if arguments.first == "config" {
            if holdConfig {
                configEntered.signal()
                await releaseConfig.wait()
            }
            if configFails {
                return .init(status: 1, stdout: Data(), stderr: "config.json is read-only")
            }
            return .init(status: 0, stdout: Data(), stderr: "")
        }
        return .init(status: 0, stdout: fixture, stderr: "")
    }
}
