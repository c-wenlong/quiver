import Foundation

/// Renews one Claude account's sign-in. Quiver still never reads or
/// writes the OAuth token itself; a manual Refresh runs Claude Code,
/// which renews its own keychain credential. The seam tests replace
/// with a fake.
public protocol ClaudeSignInRenewing: Sendable {
    /// Whether the run renewed the sign-in; false on a missing CLI, a
    /// failed run or a timeout.
    func renew(home: String, isDefault: Bool) async -> Bool
}

/// Runs `claude` once, headless, inside the account's config home: a
/// throwaway prompt on the cheapest model, no session kept, no tools.
/// Claude Code renews the OAuth credential it keeps in the keychain as
/// a side effect of an authenticated run.
public struct ClaudeSignInRenewer: ClaudeSignInRenewing {
    /// The renewal run, exactly: one prompt, no session, no tools.
    public static let arguments = ["-p", "ok", "--model", "haiku",
                                   "--no-session-persistence", "--tools", ""]

    /// Variables whose presence would authenticate the run with an API
    /// key or override token and never touch the OAuth credential. A
    /// stray CLAUDE_CONFIG_DIR would renew the wrong account.
    static let clearedVariables = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                   "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR"]

    var timeout: Duration
    /// One fixed folder, so every run writes to the same projects slug.
    var workingDirectory: URL
    let environment: @Sendable () async -> [String: String]
    var isExecutable: @Sendable (String) -> Bool

    public init(timeout: Duration = .seconds(45),
                workingDirectory: URL? = nil,
                environment: @escaping @Sendable () async -> [String: String] = {
                    await LoginShell.cachedEnvironment()
                },
                isExecutable: @escaping @Sendable (String) -> Bool = {
                    FileManager.default.isExecutableFile(atPath: $0)
                }) {
        self.timeout = timeout
        self.workingDirectory = workingDirectory
            ?? realHome().appending(path: ".quiver/cache/claude-renew")
        self.environment = environment
        self.isExecutable = isExecutable
    }

    /// What one renewal run gets, as plain data for tests: the fixed
    /// arguments, and `base` without the API-key and token variables.
    /// A non-default profile's CLAUDE_CONFIG_DIR points it at its own
    /// home; the default gets none either way. PATH is kept.
    public static func plan(home: String, isDefault: Bool, base: [String: String])
        -> (arguments: [String], environment: [String: String]) {
        var env = base
        for key in clearedVariables { env.removeValue(forKey: key) }
        if !isDefault { env[LaunchPlanner.homeVariable(for: .claude)] = home }
        return (arguments, env)
    }

    /// `claude` as the login PATH resolves it, or nil.
    static func executable(path: String, isExecutable: @Sendable (String) -> Bool) -> String? {
        path.split(separator: ":").lazy.map { "\($0)/claude" }.first(where: isExecutable)
    }

    public func renew(home: String, isDefault: Bool) async -> Bool {
        let (arguments, env) = Self.plan(home: home, isDefault: isDefault,
                                       base: await environment())
        guard let executable = Self.executable(path: env["PATH"] ?? "", isExecutable: isExecutable)
        else { return false }
        try? FileManager.default.createDirectory(at: workingDirectory, withIntermediateDirectories: true)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = env
        process.currentDirectoryURL = workingDirectory
        // stdin, stdout and stderr go nowhere: the output is model text,
        // never read and never logged.
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice

        let exited = AsyncStream<Int32> { continuation in
            process.terminationHandler = { finished in
                continuation.yield(finished.terminationStatus)
                continuation.finish()
            }
        }
        do {
            try process.run()
        } catch {
            return false
        }
        let status: Int32? = await withTaskGroup(of: Int32?.self) { group in
            group.addTask { await exited.first { _ in true } }
            group.addTask { [timeout] in
                try? await Task.sleep(for: timeout)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
        guard let status else {
            if process.isRunning { process.terminate() }
            return false
        }
        return status == 0
    }
}
