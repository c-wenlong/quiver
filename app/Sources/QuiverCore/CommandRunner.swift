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
/// venv) and the harness CLIs it shells out to would not be found.
public struct ProcessRunner: CommandRunning {
    public var executable: String
    public var environment: [String: String]

    public init(executable: String = "swe", environment: [String: String] = LoginShell.environment()) {
        self.executable = executable
        self.environment = environment
    }

    public func run(_ arguments: [String]) async throws -> CommandResult {
        // App Nap may throttle an app that is not frontmost, as when a page
        // refreshes behind another window. The user asked for this work, so
        // hold it off for the length of the run.
        let activity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated], reason: "Running swe")
        defer { ProcessInfo.processInfo.endActivity(activity) }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [executable] + arguments
        process.environment = environment
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        return try await withCheckedThrowingContinuation { continuation in
            // Drain both pipes off the main thread: a large stdout fills the
            // 64 KB pipe buffer and would block the child forever otherwise.
            let collector = OutputCollector()
            out.fileHandleForReading.readabilityHandler = { handle in
                collector.appendOut(handle.availableData)
            }
            err.fileHandleForReading.readabilityHandler = { handle in
                collector.appendErr(handle.availableData)
            }
            process.terminationHandler = { finished in
                out.fileHandleForReading.readabilityHandler = nil
                err.fileHandleForReading.readabilityHandler = nil
                collector.appendOut(out.fileHandleForReading.readDataToEndOfFile())
                collector.appendErr(err.fileHandleForReading.readDataToEndOfFile())
                continuation.resume(returning: collector.result(status: finished.terminationStatus))
            }
            do {
                try process.run()
            } catch {
                continuation.resume(throwing: SweError.notInstalled(executable))
            }
        }
    }
}

private final class OutputCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var out = Data()
    private var err = Data()

    func appendOut(_ data: Data) { lock.withLock { out.append(data) } }
    func appendErr(_ data: Data) { lock.withLock { err.append(data) } }

    func result(status: Int32) -> CommandResult {
        lock.withLock {
            CommandResult(status: status, stdout: out, stderr: String(decoding: err, as: UTF8.self))
        }
    }
}

/// The environment of the user's login shell, read once.
public enum LoginShell {
    /// `PATH=...` out of the output of `env`.
    public static func path(fromEnvOutput output: String) -> String? {
        output.split(separator: "\n")
            .first { $0.hasPrefix("PATH=") }
            .map { String($0.dropFirst("PATH=".count)) }
    }

    /// The current environment with PATH replaced by the login shell's.
    public static func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let shell = env["SHELL"] ?? "/bin/zsh"
        let process = Process()
        process.executableURL = URL(fileURLWithPath: shell)
        process.arguments = ["-ilc", "/usr/bin/env"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        if (try? process.run()) != nil {
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            if let path = path(fromEnvOutput: String(decoding: data, as: UTF8.self)) {
                env["PATH"] = path
            }
        }
        return env
    }
}
