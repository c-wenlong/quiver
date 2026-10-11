import Foundation

/// A process to start: an executable and its argv. Plain data, so the planner
/// can be tested without starting anything.
public struct LaunchCommand: Equatable, Sendable {
    public var executable: String
    public var arguments: [String]

    public init(executable: String, arguments: [String]) {
        self.executable = executable
        self.arguments = arguments
    }
}

/// Terminal apps a profile's CLI can be opened in.
public enum TerminalApp: String, Codable, CaseIterable, Sendable {
    case terminal, iterm, ghostty, wezterm
}

/// Works out what to run to open a profile's desktop app or CLI. Pure
/// functions: nothing here starts a process or touches the disk.
///
/// A non-default profile needs two isolations, because they cover different
/// state. `--user-data-dir` isolates the Chromium profile (cookies, the
/// signed-in session); the home environment variable isolates the embedded
/// agent (sessions, plugins, config). Passing only the flag would leave the
/// agent writing into the default home, which is a silent leak rather than a
/// visible failure.
public enum LaunchPlanner {
    /// The desktop app for a vendor.
    ///
    /// Codex ships inside OpenAI's app, whose bundle is `ChatGPT.app` (bundle
    /// id `com.openai.codex`); there is no `Codex.app`.
    public static func desktopAppPath(for vendor: Vendor) -> String {
        switch vendor {
        case .claude: "/Applications/Claude.app"
        case .codex: "/Applications/ChatGPT.app"
        }
    }

    /// The command-line tool for a vendor.
    public static func cliName(for vendor: Vendor) -> String {
        vendor.rawValue
    }

    /// The environment variable that points a vendor's CLI and agent at their home.
    static func homeVariable(for vendor: Vendor) -> String {
        switch vendor {
        case .claude: "CLAUDE_CONFIG_DIR"
        case .codex: "CODEX_HOME"
        }
    }

    /// Opens the desktop app. A non-default profile starts a second instance
    /// (`-n`) with its own home and Chromium data directory; the default just
    /// opens or focuses the app as the user would.
    public static func desktopApp(for profile: Profile, paths: ProfilePaths) -> LaunchCommand {
        let app = desktopAppPath(for: profile.vendor)
        guard !profile.isDefault else {
            return LaunchCommand(executable: "/usr/bin/open", arguments: ["-a", app])
        }
        var arguments = ["-n", "--env", "\(homeVariable(for: profile.vendor))=\(paths.home.path)", "-a", app]
        if let desktop = paths.desktopData {
            arguments += ["--args", "--user-data-dir=\(desktop.path)"]
        }
        return LaunchCommand(executable: "/usr/bin/open", arguments: arguments)
    }

    /// A line for `sh`/`zsh` that runs `command` as this profile: a non-default
    /// profile exports its home first, so the shell stays on that account.
    /// `command` is the whole invocation (`claude`, `codex login`) and is used
    /// as given, so it must come from the app, never from user text.
    public static func shellLine(for profile: Profile, paths: ProfilePaths, command: String) -> String {
        guard !profile.isDefault else { return command }
        return "export \(homeVariable(for: profile.vendor))=\(shellQuote(paths.home.path)); \(command)"
    }

    /// The line that signs this profile's CLI in.
    public static func cliLoginCommand(for profile: Profile, paths: ProfilePaths) -> String {
        let cli = cliName(for: profile.vendor)
        let login = profile.vendor == .claude ? "\(cli) auth login" : "\(cli) login"
        return shellLine(for: profile, paths: paths, command: login)
    }

    /// Opens `shellLine` in a new window of the chosen terminal.
    public static func terminal(_ app: TerminalApp, running shellLine: String) -> LaunchCommand {
        switch app {
        case .terminal:
            return osascript([
                #"tell application "Terminal""#,
                "activate",
                "do script \(appleScriptString(shellLine))",
                "end tell",
            ])
        case .iterm:
            return osascript([
                #"tell application "iTerm""#,
                "activate",
                "set w to (create window with default profile)",
                "tell current session of w to write text \(appleScriptString(shellLine))",
                "end tell",
            ])
        case .ghostty:
            return LaunchCommand(
                executable: "/usr/bin/open",
                arguments: ["-na", "Ghostty", "--args", "-e", "/bin/zsh", "-lc", shellLine])
        case .wezterm:
            return LaunchCommand(
                executable: "/usr/bin/open",
                arguments: ["-na", "WezTerm", "--args", "start", "--", "/bin/zsh", "-lc", shellLine])
        }
    }

    // MARK: - Quoting

    /// One `-e` per script line, so no argument contains a newline.
    private static func osascript(_ lines: [String]) -> LaunchCommand {
        LaunchCommand(executable: "/usr/bin/osascript", arguments: lines.flatMap { ["-e", $0] })
    }

    /// Single-quotes `value` for sh: `it's` becomes `'it'\''s'`.
    static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: #"'\''"#) + "'"
    }

    /// A double-quoted AppleScript string literal.
    static func appleScriptString(_ value: String) -> String {
        var out = "\""
        for character in value {
            switch character {
            case "\\": out += #"\\"#
            case "\"": out += #"\""#
            case "\n", "\r\n": out += #"\n"#
            case "\r": out += #"\r"#
            case "\t": out += #"\t"#
            default: out.append(character)
            }
        }
        return out + "\""
    }
}
