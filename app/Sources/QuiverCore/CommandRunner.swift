import Foundation

/// What a finished process left behind.
public struct CommandResult: Sendable, Equatable {
    public var status: Int32
    public var stdout: Data
    public var stderr: String

    public init(status: Int32, stdout: Data, stderr: String) {
        self.status = status
        self.stdout = stdout
        self.stderr = stderr
    }
}

/// Runs `swe` with arguments. The seam tests replace with a fake.
public protocol CommandRunning: Sendable {
    func run(_ arguments: [String]) async throws -> CommandResult
}

/// Runs a real executable, with the PATH a login shell would have.
///
/// A GUI app launched from Finder or the Dock inherits launchd's minimal
/// PATH, so `swe` (a pip console script, often under ~/.local/bin or a
/// venv) and the harness CLIs it shells out to would not be found. With no
/// environment given, the login shell's is resolved on first use, off the
/// main thread, and cached.
public struct ProcessRunner: CommandRunning {
    public var executable: String
    public var environment: [String: String]?

    public init(executable: String = "swe", environment: [String: String]? = nil) {
        self.executable = executable
        self.environment = environment
    }

    public func run(_ arguments: [String]) async throws -> CommandResult {
        let env: [String: String]
        if let environment {
            env = environment
        } else {
            env = await LoginShell.cachedEnvironment()
        }
        try Task.checkCancellation()

        // App Nap may throttle an app that is not frontmost, as when a page
        // refreshes behind another window. The user asked for this work, so
        // hold it off for the length of the run.
        let activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated], reason: "Running swe")
        defer { ProcessInfo.processInfo.endActivity(activity) }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [executable] + arguments
        process.environment = env
        process.standardInput = FileHandle.nullDevice
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                // One reader per pipe, each reading to EOF, and the result is
                // assembled only once both readers and the exit are in. A
                // callback-per-chunk reader can still be holding bytes when
                // the exit arrives, which truncated or reordered output.
                let group = DispatchGroup()
                let box = OutputBox()
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    box.stdout = out.fileHandleForReading.readDataToEndOfFile()
                    group.leave()
                }
                group.enter()
                DispatchQueue.global(qos: .userInitiated).async {
                    box.stderr = err.fileHandleForReading.readDataToEndOfFile()
                    group.leave()
                }
                group.enter()
                process.terminationHandler = { _ in group.leave() }
                do {
                    try process.run()
                } catch {
                    // Close our write ends so the readers see EOF and finish.
                    try? out.fileHandleForWriting.close()
                    try? err.fileHandleForWriting.close()
                    continuation.resume(throwing: SweError.notInstalled(executable))
                    return
                }
                group.notify(queue: .global()) {
                    continuation.resume(returning: CommandResult(
                        status: process.terminationStatus,
                        stdout: box.stdout,
                        stderr: String(decoding: box.stderr, as: UTF8.self)))
                }
            }
        } onCancel: {
            // Leaving a page cancels its task; the child should not outlive it.
            if process.isRunning { process.terminate() }
        }
    }
}

/// Written by exactly one reader each, read only after both have finished.
private final class OutputBox: @unchecked Sendable {
    var stdout = Data()
    var stderr = Data()
}

/// The environment of the user's login shell.
public enum LoginShell {
    /// `PATH=...` out of the output of `env`.
    public static func path(fromEnvOutput output: String) -> String? {
        output.split(separator: "\n")
            .first { $0.hasPrefix("PATH=") }
            .map { String($0.dropFirst("PATH=".count)) }
    }

    /// The login shell's environment, resolved once per launch.
    public static func cachedEnvironment() async -> [String: String] {
        await cache.value
    }

    private static let cache = Task.detached(priority: .userInitiated) {
        let base = ProcessInfo.processInfo.environment
        return await resolve(shell: base["SHELL"] ?? "/bin/zsh", base: base, timeout: .seconds(5))
    }

    /// `base` with PATH replaced by what `shell -ilc env` reports.
    ///
    /// A slow or hanging shell startup file must not freeze the app: after
    /// `timeout` the shell is stopped and `base` comes back unchanged.
    public static func resolve(shell: String, base: [String: String], timeout: Duration) async -> [String: String] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: shell)
        process.arguments = ["-ilc", "/usr/bin/env"]
        process.environment = base
        process.standardInput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        let pipe = Pipe()
        process.standardOutput = pipe

        let output: String? = await withCheckedContinuation { continuation in
            let once = ResumeOnce(continuation)
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    try process.run()
                } catch {
                    once.resume(nil)
                    return
                }
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                once.resume(String(decoding: data, as: UTF8.self))
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout.timeInterval) {
                if process.isRunning { process.terminate() }
                once.resume(nil)
            }
        }
        var env = base
        if let output, let path = path(fromEnvOutput: output) {
            env["PATH"] = path
        }
        return env
    }
}

/// Resumes a continuation with the first value offered, ignoring the rest.
private final class ResumeOnce<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Never>?

    init(_ continuation: CheckedContinuation<T, Never>) { self.continuation = continuation }

    func resume(_ value: T) {
        let pending = lock.withLock { () -> CheckedContinuation<T, Never>? in
            defer { continuation = nil }
            return continuation
        }
        pending?.resume(returning: value)
    }
}

extension Duration {
    var timeInterval: TimeInterval {
        let (seconds, attoseconds) = components
        return TimeInterval(seconds) + TimeInterval(attoseconds) / 1e18
    }
}
