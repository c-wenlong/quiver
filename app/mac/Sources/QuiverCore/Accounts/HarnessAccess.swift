import Foundation
import Observation

/// What Quiver knows about reaching a harness outside a terminal prompt: its
/// desktop app and the CLI command that signs it in. Static tables, checked
/// against each CLI's own `--help`, so nothing here runs a harness.
public enum HarnessAccess {
    /// Desktop app bundle names to look for, best first. Codex ships inside
    /// OpenAI's ChatGPT.app; Droid's app is Factory's.
    static func appNames(for harness: String) -> [String] {
        switch harness {
        case "claude": ["Claude"]
        case "codex": ["ChatGPT"]
        case "cursor": ["Cursor"]
        case "devin": ["Devin"]
        case "droid": ["Factory"]
        default: []
        }
    }

    /// Harnesses swe can read usage limits for (src/swe/harness/rate_limits.py).
    /// It polls only the starred ones.
    public static func hasLimits(_ harness: String) -> Bool {
        ["claude", "codex", "copilot", "cursor", "devin", "droid", "freebuff"].contains(harness)
    }

    /// The whole sign-in invocation, or nil when the CLI has none of its own.
    /// Claude and Codex sign in per account, through `LaunchPlanner`.
    public static func loginCommand(for harness: String) -> String? {
        switch harness {
        case "cursor": "cursor-agent login"
        case "devin": "devin auth login"
        case "freebuff": "freebuff login"
        // swe reads Copilot's limits with the GitHub CLI's token.
        case "copilot": "gh auth login"
        // Droid signs in from inside its own TUI.
        default: nil
        }
    }
}

/// Finds a harness's desktop app in /Applications or ~/Applications.
public struct AppLocator: Sendable {
    var home: String
    var fileExists: @Sendable (String) -> Bool

    public init(home: String = FileManager.default.homeDirectoryForCurrentUser.path,
                fileExists: @escaping @Sendable (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }) {
        self.home = home
        self.fileExists = fileExists
    }

    /// The app's path, or nil when it isn't installed.
    public func installedApp(for harness: String) -> String? {
        for name in HarnessAccess.appNames(for: harness) {
            for folder in ["/Applications", "\(home)/Applications"] {
                let path = "\(folder)/\(name).app"
                if fileExists(path) { return path }
            }
        }
        return nil
    }
}

/// Whether a command is an executable file in a PATH, as a shell would
/// find it. A static check: nothing is run.
public enum CLIPath {
    public static func contains(_ command: String, path: String,
                                isExecutable: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }) -> Bool {
        guard !command.isEmpty, !command.contains("/") else { return false }
        return path.split(separator: ":").contains { isExecutable("\($0)/\(command)") }
    }

    /// The PATH of the shell Terminal runs (`/bin/zsh -ilc`, see
    /// `LaunchPlanner.terminal`), read fresh on each call so a CLI installed
    /// after Quiver started shows up on Refresh. Falls back to the cached
    /// login environment when zsh doesn't answer in time.
    public static func terminalShellPath() async -> String {
        let base = ProcessInfo.processInfo.environment
        if let env = await LoginShell.environment(shell: "/bin/zsh", base: base, timeout: .seconds(5)),
           let path = env["PATH"] {
            return path
        }
        return await LoginShell.cachedEnvironment()["PATH"] ?? ""
    }
}
