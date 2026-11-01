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
    /// When the deadline fires. Tests replace it to fire at a point they
    /// pick, once the command tree they mean to stop exists.
    var deadlineTimer: DeadlineTimer = .wallClock

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
        let deadlineTimer = self.deadlineTimer

        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                let once = ResumeOnce(continuation)
                let deadline = DeadlineBox()
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
                process.terminationHandler = { _ in
                    group.leave()
                    // A timed-out run reports the stop as soon as the
                    // child is actually gone, not when the pipes drain.
                    if deadline.isExpired { once.resume(throwing: SweError.timedOut) }
                }
                // No child started: close our write ends so the readers see
                // EOF and finish, and leave the exit entry the termination
                // handler will never leave, so the group does not leak.
                func abandon(_ error: any Error) {
                    group.leave()
                    try? out.fileHandleForWriting.close()
                    try? err.fileHandleForWriting.close()
                    once.resume(throwing: error)
                }
                // Set below, before anything can call it: a cancel
                // arriving after the launch stops the tree through it.
                let stopper = StopperBox()
                do {
                    guard try gate.launch(onCancel: { stopper.run() }) else {
                        return abandon(CancellationError())
                    }
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
                // A hung run must not wedge the caller forever: past the
                // deadline the whole command tree is stopped — a child
                // like `bash` leaves `swift build` running otherwise —
                // and the call throws once the run's own process is gone
                // (the termination handler may report it sooner). A
                // cancelled caller stops the same tree. Both blocks are
                // cancelled when a run nobody stopped answers, so a pid
                // reused after the child exited is never signalled.
                let tagged = PidBox()
                let stopping = StopOnce()
                let killBlock = DispatchWorkItem {
                    if process.isRunning {
                        kill(process.processIdentifier, SIGKILL)
                        Self.stopDescendants(of: process.processIdentifier, signal: SIGKILL)
                    }
                    // Orphans reparented to launchd vanish from the tree
                    // walk, so tagged pids are re-signalled by number —
                    // but only while the number still identifies the
                    // same process. A descendant that obeyed SIGTERM and
                    // whose pid was reused is left alone.
                    for descendant in Set(tagged.all)
                    where Self.tag(descendant.pid) == descendant {
                        kill(descendant.pid, SIGKILL)
                    }
                    // SIGKILL cannot be ignored, so this waits out the
                    // kernel's reaping rather than a live child. A
                    // cancelled run whose pipes an orphan still holds
                    // answers here too.
                    process.waitUntilExit()
                    once.resume(throwing: deadline.isExpired
                                ? SweError.timedOut : CancellationError())
                }
                /// Stops the whole tree, once: SIGTERM now, SIGKILL for
                /// whatever is left two seconds later.
                func stopTree() {
                    // A child that already exited left nothing to
                    // signal, and its pid may already belong to another
                    // process — never sweep then.
                    guard process.isRunning, stopping.begin() else { return }
                    // Children first: the parent must stay alive until
                    // the tree is tagged or they reparent and vanish.
                    tagged.set(Self.stopDescendants(
                        of: process.processIdentifier, signal: SIGTERM))
                    process.terminate()
                    // A dying parent may spawn during the sweep; one
                    // more pass catches the strays while attached.
                    tagged.set(tagged.all + Self.stopDescendants(
                        of: process.processIdentifier, signal: SIGTERM))
                    // SIGTERM is a request; whatever ignores it is
                    // killed outright a moment later.
                    DispatchQueue.global().asyncAfter(
                        deadline: .now() + .seconds(2), execute: killBlock)
                }
                stopper.set(stopTree)
                let deadlineBlock = DispatchWorkItem {
                    deadline.expire()
                    // A child that already exited reports the timeout
                    // without a sweep.
                    guard process.isRunning else {
                        once.resume(throwing: SweError.timedOut)
                        return
                    }
                    stopTree()
                }
                deadlineTimer.arm(timeout, deadlineBlock)
                group.notify(queue: .global()) {
                    // A run past its deadline throws however it ended:
                    // the termination handler leaves the group before it
                    // reports the timeout, so this block can get there
                    // first and must not hand back the killed child's
                    // exit status as an answer.
                    if deadline.isExpired {
                        once.resume(throwing: SweError.timedOut)
                        return
                    }
                    // A finished run disarms the deadline; a cancelled
                    // one keeps the kill pass so survivors cannot
                    // outlive the call.
                    if !stopping.started {
                        deadlineBlock.cancel()
                        killBlock.cancel()
                    }
                    once.resume(CommandResult(
                        status: process.terminationStatus,
                        stdout: box.stdout,
                        stderr: String(decoding: box.stderr, as: UTF8.self)))
                }
            }
        } onCancel: {
            // Leaving a page cancels its task; the child should not outlive
            // it, and neither should anything it started.
            gate.cancel()
        }
    }

    /// Sends `signal` to every process under `pid`, walking the tree with
    /// `proc_listchildpids`, and returns the processes it tagged so a
    /// later pass can reach orphans that reparented off the tree.
    /// Children may outlive their parent — a `bash` script's `swift
    /// build` survives bash's own terminate — so a timeout sweeps the
    /// descendants too. Never call this on a child that has already
    /// exited: its pid may belong to another process by then.
    @discardableResult
    fileprivate static func stopDescendants(of pid: pid_t, signal: Int32) -> [TaggedPid] {
        // The whole tree is listed before anything is signalled, then
        // signalled leaves first: a parent killed mid-walk would let its
        // children reparent to launchd before the walk listed them.
        var found: [TaggedPid] = []
        var seen: Set<pid_t> = [pid]
        var queue = [pid]
        while let parent = queue.popLast() {
            var buffer = [pid_t](repeating: 0, count: 512)
            // libproc answers a count of pids here, not bytes (unlike
            // proc_listpids). Read as bytes it dropped every child of a
            // parent with fewer than four, so the sweep found nothing and
            // a child that ignored SIGTERM outlived the run. The zero
            // filter keeps a bytes answer harmless too.
            let count = proc_listchildpids(parent, &buffer,
                                           Int32(MemoryLayout<pid_t>.stride * buffer.count))
            guard count > 0 else { continue }
            for child in buffer.prefix(min(Int(count), buffer.count))
            where child > 0 && seen.insert(child).inserted {
                // A child that exited since the listing has no start tag
                // and its number may already serve another process, so
                // only a tagged pid is ever signalled. Its own children
                // are still walked either way.
                if let tagged = Self.tag(child) { found.append(tagged) }
                queue.append(child)
            }
        }
        // Every child comes after its parent in `found`. The tag is
        // checked again: a process may have exited since it was listed.
        for tagged in found.reversed() where Self.tag(tagged.pid) == tagged {
            kill(tagged.pid, signal)
        }
        return found
    }

    /// The process's start instant, nil once it has exited — the
    /// identity check that keeps a signal off a pid the system reused.
    private static func tag(_ pid: pid_t) -> TaggedPid? {
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
        return TaggedPid(pid: pid,
                         seconds: info.pbi_start_tvsec,
                         micros: info.pbi_start_tvusec)
    }
}

/// When a run's deadline fires. Production counts the timeout down on
/// the wall clock. A test fires it at the moment it means to test, once
/// the command tree it wants stopped exists, so a starved machine cannot
/// move the deadline in front of the child's own setup.
struct DeadlineTimer: Sendable {
    let arm: @Sendable (_ timeout: Duration, _ fire: DispatchWorkItem) -> Void

    static let wallClock = DeadlineTimer { timeout, fire in
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout.timeInterval, execute: fire)
    }
}

/// Set when a run passes its deadline; read by the termination handler
/// to report the stop as soon as the child is gone.
private final class DeadlineBox: @unchecked Sendable {
    private let lock = NSLock()
    private var expired = false

    var isExpired: Bool { lock.withLock { expired } }

    func expire() { lock.withLock { expired = true } }
}

/// Lets the tree be stopped only once, by whichever of the deadline and
/// a cancel comes first.
private final class StopOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var begun = false

    var started: Bool { lock.withLock { begun } }

    /// True for the first caller only.
    func begin() -> Bool {
        lock.withLock {
            defer { begun = true }
            return !begun
        }
    }
}

/// The run's stop-the-tree action, handed to the launch gate before the
/// action itself exists (it needs the kill pass built after the launch).
private final class StopperBox: @unchecked Sendable {
    private let lock = NSLock()
    // Not @Sendable: it captures the kill work item. Every call goes
    // through the lock-guarded hand-off below.
    private var action: (() -> Void)?
    private var pending = false

    func set(_ newValue: @escaping () -> Void) {
        let runNow = lock.withLock { () -> Bool in
            action = newValue
            return pending
        }
        if runNow { newValue() }
    }

    /// Runs the action, or runs it as soon as it is set.
    func run() {
        let now = lock.withLock { () -> (() -> Void)? in
            if action == nil { pending = true }
            return action
        }
        now?()
    }
}

/// A descendant process's pid plus its start instant. The pair survives
/// the process reparenting to launchd, so a later signal can still tell
/// the tagged process from an unrelated one that reused the number.
private struct TaggedPid: Hashable, Sendable {
    var pid: pid_t
    var seconds: UInt64
    var micros: UInt64
}

/// The descendants a deadline sweep tagged, shared with the SIGKILL
/// pass so orphans that reparented off the tree are still stopped.
private final class PidBox: @unchecked Sendable {
    private let lock = NSLock()
    private var pids: [TaggedPid] = []

    var all: [TaggedPid] { lock.withLock { pids } }

    func set(_ newValue: [TaggedPid]) { lock.withLock { pids = newValue } }
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
    private var onCancel: (@Sendable () -> Void)?

    init(_ process: Process) { self.process = process }

    /// Starts the process, or returns false without starting it when the
    /// run was already cancelled. A later cancel calls `onCancel`, which
    /// stops the child and everything under it.
    func launch(onCancel: @escaping @Sendable () -> Void) throws -> Bool {
        try lock.withLock {
            if cancelled { return false }
            try process.run()
            launched = true
            self.onCancel = onCancel
            return true
        }
    }

    func cancel() {
        let stop = lock.withLock { () -> (@Sendable () -> Void)? in
            cancelled = true
            return launched ? onCancel : nil
        }
        stop?()
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

    /// The login shell's environment, resolved once per launch. A shell
    /// that didn't answer in time is asked again later, in the background,
    /// rather than leaving the whole launch on launchd's bare PATH.
    public static func cachedEnvironment() async -> [String: String] {
        await cache.environment()
    }

    private static let cache = ShellEnvironmentCache(retryAfter: .seconds(60)) {
        let base = ProcessInfo.processInfo.environment
        return await resolveIfAnswered(shell: base["SHELL"] ?? "/bin/zsh", base: base, timeout: .seconds(5))
    } fallback: {
        scrubbed(ProcessInfo.processInfo.environment)
    }

    /// `base` with PATH replaced by what `shell -ilc env` reports.
    ///
    /// A slow or hanging shell startup file must not freeze the app: after
    /// `timeout` the shell is stopped and `base` comes back unchanged.
    public static func resolve(shell: String, base: [String: String], timeout: Duration) async -> [String: String] {
        await resolve(shell: shell, base: base, timeout: timeout, deadline: .wallClock)
    }

    /// `resolve` with the deadline fired by `deadline`, for tests.
    static func resolve(shell: String, base: [String: String], timeout: Duration,
                        deadline: DeadlineTimer) async -> [String: String] {
        await resolveIfAnswered(shell: shell, base: base, timeout: timeout, deadline: deadline)
            ?? scrubbed(base)
    }

    /// `resolve`, or nil when the shell gave no PATH in time.
    static func resolveIfAnswered(shell: String, base: [String: String], timeout: Duration,
                                  deadline: DeadlineTimer = .wallClock) async -> [String: String]? {
        guard let output = await envOutput(shell: shell, base: base, timeout: timeout,
                                           deadline: deadline),
              let path = path(fromEnvOutput: output) else { return nil }
        var env = base
        env["PATH"] = path
        return scrubbed(env)
    }

    private static func scrubbed(_ base: [String: String]) -> [String: String] {
        var env = base
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

    private static func envOutput(shell: String, base: [String: String], timeout: Duration,
                                  deadline: DeadlineTimer = .wallClock) async -> String? {
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
            deadline.arm(timeout, DispatchWorkItem {
                // SIGKILL, children first: an interactive zsh ignores
                // SIGTERM, so a terminate left the shell (and whatever its
                // startup file was waiting on) running for good.
                if process.isRunning {
                    let pid = process.processIdentifier
                    ProcessRunner.stopDescendants(of: pid, signal: SIGKILL)
                    if process.isRunning { kill(pid, SIGKILL) }
                }
                once.resume(nil)
            })
        }
        return output
    }
}

/// The login shell's environment once a read succeeded. Until then each
/// caller gets `fallback`: the first call waits for the first read, and
/// after a failed read another starts in the background once `retryAfter`
/// has passed, so a slow shell neither sticks for the whole launch nor
/// stalls every call.
actor ShellEnvironmentCache {
    private let read: @Sendable () async -> [String: String]?
    private let fallback: @Sendable () -> [String: String]
    private let retryAfter: Duration
    private var value: [String: String]?
    private var inFlight: Task<Void, Never>?
    private var failedAt: ContinuousClock.Instant?

    init(retryAfter: Duration,
         read: @escaping @Sendable () async -> [String: String]?,
         fallback: @escaping @Sendable () -> [String: String]) {
        self.retryAfter = retryAfter
        self.read = read
        self.fallback = fallback
    }

    func environment() async -> [String: String] {
        if let value { return value }
        if failedAt == nil {
            // The first read: every early caller waits on the same one.
            let task = inFlight ?? start()
            await task.value
            return value ?? fallback()
        }
        if inFlight == nil, let failedAt, ContinuousClock.now - failedAt >= retryAfter {
            _ = start()
        }
        return fallback()
    }

    /// Waits for the read in flight, if any, so a test sees a background
    /// retry land without polling for it.
    func settle() async {
        await inFlight?.value
    }

    private func start() -> Task<Void, Never> {
        let read = self.read
        let task = Task.detached(priority: .userInitiated) { [weak self] in
            let result = await read()
            await self?.record(result)
        }
        inFlight = task
        return task
    }

    private func record(_ result: [String: String]?) {
        inFlight = nil
        if let result { value = result } else { failedAt = .now }
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
