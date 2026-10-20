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
    /// `input` goes to the child's stdin, then stdin closes. Secret values
    /// travel this way, never through argv, which `ps` would show.
    func run(_ arguments: [String], input: Data?) async throws -> CommandResult
    /// `timeout` bounds the whole run: past it the child is stopped and
    /// the call throws, even if something still holds the pipes open.
    /// nil asks for the runner's own default bound.
    func run(_ arguments: [String], input: Data?, timeout: Duration?) async throws -> CommandResult
}

public extension CommandRunning {
    /// Conformers that can't feed stdin still compile; the run goes
    /// without input. The real runner overrides it.
    func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
        try await run(arguments)
    }
    /// Conformers that can't bound a run (the fakes) ignore the deadline;
    /// the real runner enforces it.
    func run(_ arguments: [String], input: Data?, timeout: Duration?) async throws -> CommandResult {
        try await run(arguments, input: input)
    }
}

/// Runs a real executable, with the PATH a login shell would have.
///
/// A GUI app launched from Finder or the Dock inherits launchd's minimal
/// PATH, so a `swe` on PATH (a pip console script, often under ~/.local/bin
/// or a venv) and the harness CLIs swe shells out to would not be found.
/// With no environment given, the login shell's is resolved on first use,
/// off the main thread, and cached.
public struct ProcessRunner: CommandRunning {
    public var executable: String
    public var environment: [String: String]?
    /// The longest a run may take before the child is stopped and the
    /// call throws `SweError.timedOut`. `run(_:input:timeout:)` overrides
    /// it per call. Long enough for swe's slowest replies (a `--usage`
    /// list polling every provider); a wedged child still gets found.
    public var defaultTimeout: Duration

    public init(executable: String = SweLocation.executable(override: nil),
                environment: [String: String]? = nil,
                defaultTimeout: Duration = .seconds(120)) {
        self.executable = executable
        self.environment = environment
        self.defaultTimeout = defaultTimeout
    }

    public func run(_ arguments: [String]) async throws -> CommandResult {
        try await run(arguments, input: nil, timeout: nil)
    }

    public func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
        try await run(arguments, input: input, timeout: nil)
    }

    public func run(_ arguments: [String], input: Data?, timeout: Duration?) async throws -> CommandResult {
        let timeout = timeout ?? defaultTimeout
        let env: [String: String]
        if let environment {
            env = environment
        } else {
            env = await LoginShell.cachedEnvironment()
        }

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
        let inputPipe = Pipe()
        process.standardInput = input != nil ? inputPipe : FileHandle.nullDevice
        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err
        let gate = LaunchGate(process)

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let once = ResumeOnce(continuation)
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
                // No child started: close our write ends so the readers see
                // EOF and finish, and leave the exit entry the termination
                // handler will never leave, so the group does not leak.
                func abandon(_ error: any Error) {
                    group.leave()
                    try? out.fileHandleForWriting.close()
                    try? err.fileHandleForWriting.close()
                    once.resume(throwing: error)
                }
                do {
                    guard try gate.launch() else { return abandon(CancellationError()) }
                } catch let error as NSError {
                    // env(1) itself failed to spawn — the box is out of
                    // processes or env is broken — which is a system
                    // problem, not swe missing. A missing swe answers
                    // exit 127 and is named in SweClient.
                    return abandon(SweError.commandFailed(
                        status: Int32(clamping: error.code),
                        message: "Couldn't start \(executable): \(error.localizedDescription)"))
                }
                if let input {
                    // A child that exits before reading (an older swe that
                    // lacks the command) must not SIGPIPE the whole app.
                    _ = fcntl(inputPipe.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
                    // On a queue: a child that never reads stdin would
                    // otherwise block the caller once the pipe filled.
                    DispatchQueue.global(qos: .userInitiated).async {
                        try? inputPipe.fileHandleForWriting.write(contentsOf: input)
                        try? inputPipe.fileHandleForWriting.close()
                    }
                }
                // A hung child — or one that already exited while a
                // grandchild still holds the pipes open — must not wedge
                // the caller forever: past the deadline the run throws.
                // The readers finish whenever the pipes finally close;
                // the group's late notify is then a no-op.
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout.timeInterval) {
                    if process.isRunning {
                        process.terminate()
                        // SIGTERM is a request; a child that ignores it is
                        // killed outright a moment later.
                        DispatchQueue.global().asyncAfter(deadline: .now() + .seconds(2)) {
                            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                        }
                    }
                    once.resume(throwing: SweError.timedOut)
                }
                group.notify(queue: .global()) {
                    once.resume(CommandResult(
                        status: process.terminationStatus,
                        stdout: box.stdout,
                        stderr: String(decoding: box.stderr, as: UTF8.self)))
                }
            }
        } onCancel: {
            // Leaving a page cancels its task; the child should not outlive it.
            gate.cancel()
        }
    }
}

/// The one place a run's cancel and its launch meet. Both take the lock, so
/// a cancel that lands before the launch stops it from starting, and one
/// that lands after terminates the child. Checking `isRunning` alone missed
/// a cancel that arrived before `run()`, and the child then ran to the end.
private final class LaunchGate: @unchecked Sendable {
    private let lock = NSLock()
    private let process: Process
    private var cancelled = false
    private var launched = false

    init(_ process: Process) { self.process = process }

    /// Starts the process, or returns false without starting it when the
    /// run was already cancelled.
    func launch() throws -> Bool {
        try lock.withLock {
            if cancelled { return false }
            try process.run()
            launched = true
            return true
        }
    }

    func cancel() {
        lock.withLock {
            cancelled = true
            if launched, process.isRunning { process.terminate() }
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
    /// Variables a login shell may set that must not reach the swe
    /// processes the app runs: the app polls limits on its own schedule,
    /// and an override like `SWE_RATE_LIMITS_TTL=inf` would freeze them,
    /// so swe children get swe's default.
    static let ignoredVariables: Set<String> = ["SWE_RATE_LIMITS_TTL"]

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
        var env = base
        if let output = await envOutput(shell: shell, base: base, timeout: timeout),
           let path = path(fromEnvOutput: output) {
            env["PATH"] = path
        }
        for key in ignoredVariables { env.removeValue(forKey: key) }
        return env
    }

    /// Everything a new login shell exports, or nil when it couldn't be read
    /// in time. Holds secrets: keep it in memory, never log or cache it.
    public static func environment(shell: String, base: [String: String], timeout: Duration) async -> [String: String]? {
        await envOutput(shell: shell, base: base, timeout: timeout).map(parse(envOutput:))
    }

    /// `NAME=value` lines from `env`; a value may itself contain `=`.
    public static func parse(envOutput output: String) -> [String: String] {
        var env: [String: String] = [:]
        for line in output.split(separator: "\n") {
            guard let equals = line.firstIndex(of: "="), equals != line.startIndex else { continue }
            env[String(line[..<equals])] = String(line[line.index(after: equals)...])
        }
        return env
    }

    private static func envOutput(shell: String, base: [String: String], timeout: Duration) async -> String? {
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
        return output
    }
}

/// Resumes a continuation with the first offer, ignoring the rest.
private final class ResumeOnce<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var action: (@Sendable (Result<T, any Error>) -> Void)?

    init(_ continuation: CheckedContinuation<T, Never>) {
        action = { result in
            guard case .success(let value) = result else { return }
            continuation.resume(returning: value)
        }
    }

    init(_ continuation: CheckedContinuation<T, any Error>) {
        action = { continuation.resume(with: $0) }
    }

    func resume(_ value: T) { offer(.success(value)) }

    func resume(throwing error: any Error) { offer(.failure(error)) }

    private func offer(_ result: Result<T, any Error>) {
        let pending = lock.withLock { () -> (@Sendable (Result<T, any Error>) -> Void)? in
            defer { action = nil }
            return action
        }
        pending?(result)
    }
}

extension Duration {
    var timeInterval: TimeInterval {
        let (seconds, attoseconds) = components
        return TimeInterval(seconds) + TimeInterval(attoseconds) / 1e18
    }
}
