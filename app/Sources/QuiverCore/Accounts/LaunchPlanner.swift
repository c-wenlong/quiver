import AppKit
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

    public var title: String {
        switch self {
        case .terminal: "Terminal"
        case .iterm: "iTerm"
        case .ghostty: "Ghostty"
        case .wezterm: "WezTerm"
        }
    }

    /// What LaunchServices knows the app by, wherever it's installed.
    public var bundleID: String {
        switch self {
        case .terminal: "com.apple.Terminal"
        case .iterm: "com.googlecode.iterm2"
        case .ghostty: "com.mitchellh.ghostty"
        case .wezterm: "com.github.wez.wezterm"
        }
    }

    /// Whether LaunchServices can find the app. Terminal ships with macOS.
    public var isInstalled: Bool {
        self == .terminal || NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) != nil
    }

    /// The app's own icon, nil when LaunchServices can't find it.
    public var icon: NSImage? {
        NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID)
            .map { NSWorkspace.shared.icon(forFile: $0.path) }
    }

    /// The bundled brand mark shown when the app isn't installed and
    /// LaunchServices has no icon to give, so the tab still says which
    /// terminal it is. Terminal.app always resolves on its own.
    public var toolIconID: String? {
        switch self {
        case .terminal: nil
        case .iterm: "iterm"
        case .ghostty: "ghostty"
        case .wezterm: "wezterm"
        }
    }

    /// What a launch says when the chosen terminal is missing, instead of
    /// LaunchServices' or osascript's own error text.
    public var notInstalledMessage: String {
        "\(title) isn't installed. Pick another terminal in Settings › General."
    }
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
        case .devin: "/Applications/Devin.app"
        case .cursor: "/Applications/Cursor.app"
        }
    }

    /// The command-line tool for a vendor.
    public static func cliName(for vendor: Vendor) -> String {
        vendor.cliCommand
    }

    /// The environment variable that points a vendor's CLI and agent at
    /// their home. nil for Cursor: `cursor-agent` keeps one sign-in per
    /// Mac (its token lives in the keychain and answers to no variable),
    /// so an extra Cursor account's terminal runs the default account.
    static func homeVariable(for vendor: Vendor) -> String? {
        switch vendor {
        case .claude: "CLAUDE_CONFIG_DIR"
        case .codex: "CODEX_HOME"
        case .devin: "XDG_DATA_HOME"
        case .cursor: nil
        }
    }

    /// Opens the desktop app. A non-default profile starts a second instance
    /// (`-n`) with its own home and Chromium data directory; the default just
    /// opens or focuses the app as the user would — except when another
    /// instance of that app is already running (`otherInstanceRunning`), where
    /// plain `open -a` would activate that account's instance instead of
    /// starting the default one.
    public static func desktopApp(for profile: Profile, paths: ProfilePaths,
                                  appPath: String? = nil,
                                  otherInstanceRunning: Bool = false) -> LaunchCommand {
        let app = appPath ?? desktopAppPath(for: profile.vendor)
        guard !profile.isDefault else {
            let arguments = otherInstanceRunning ? ["-n", "-a", app] : ["-a", app]
            return LaunchCommand(executable: "/usr/bin/open", arguments: arguments)
        }
        var arguments = ["-n"]
        if let variable = homeVariable(for: profile.vendor) {
            arguments += ["--env", "\(variable)=\(paths.home.path)"]
        }
        arguments += ["-a", app]
        if let desktop = paths.desktopData {
            arguments += ["--args", "--user-data-dir=\(desktop.path)"]
        }
        return LaunchCommand(executable: "/usr/bin/open", arguments: arguments)
    }

    /// Whether a running instance's argv belongs to this profile: a
    /// non-default profile's instance carries its `--user-data-dir`, the
    /// default profile's instance carries none at all.
    public static func instance(_ arguments: [String], belongsTo profile: Profile,
                                paths: ProfilePaths) -> Bool {
        guard !arguments.isEmpty else { return false }
        let dataDir = userDataDir(in: arguments)
        guard !profile.isDefault else { return dataDir == nil }
        guard let dataDir, let mine = paths.desktopData else { return false }
        return standardized(dataDir) == standardized(mine.path)
    }

    /// The `--user-data-dir` value in an argv, accepting both the `--flag=X`
    /// and `--flag X` forms.
    static func userDataDir(in arguments: [String]) -> String? {
        var i = arguments.startIndex
        while i < arguments.endIndex {
            let argument = arguments[i]
            if argument.hasPrefix("--user-data-dir=") {
                return String(argument.dropFirst("--user-data-dir=".count))
            }
            if argument == "--user-data-dir", i + 1 < arguments.endIndex {
                return arguments[i + 1]
            }
            i += 1
        }
        return nil
    }

    private static func standardized(_ path: String) -> String {
        URL(fileURLWithPath: path).standardizedFileURL.path
    }

    /// A line for `sh`/`zsh` that runs `command` as this profile: a non-default
    /// profile exports its home first, so the shell stays on that account.
    /// `command` is the whole invocation (`claude`, `codex login`) and is used
    /// as given, so it must come from the app, never from user text.
    public static func shellLine(for profile: Profile, paths: ProfilePaths, command: String) -> String {
        // A vendor with no home variable (cursor-agent's one sign-in per
        // Mac) runs the command as given: there is nothing to export, and
        // XDG_DATA_HOME must never be unset — it isn't Cursor's.
        guard let variable = homeVariable(for: profile.vendor) else { return command }
        // The default account clears an override the user's shell may export,
        // or the command would run as whichever account that points at.
        guard !profile.isDefault else { return "unset \(variable); \(command)" }
        return "export \(variable)=\(shellQuote(paths.home.path)); \(command)"
    }

    /// The line that signs this profile's CLI in, or nil when the vendor
    /// signs in inside its desktop app (Cursor: the sign-in is the app's
    /// state.vscdb, which the CLI shares).
    public static func cliLoginCommand(for profile: Profile, paths: ProfilePaths) -> String? {
        let cli = cliName(for: profile.vendor)
        let login: String? = switch profile.vendor {
        case .claude, .devin: "\(cli) auth login"
        case .codex: "\(cli) login"
        case .cursor: nil
        }
        return login.map { shellLine(for: profile, paths: paths, command: $0) }
    }

    /// Opens `shellLine` in a new window of the chosen terminal.
    public static func terminal(_ app: TerminalApp, running shellLine: String) -> LaunchCommand {
        // Terminal and iTerm type the line into whatever shell the profile
        // runs, which may be csh, where `export` fails and the CLI would then
        // start as the default account. zsh -ilc runs it the same everywhere.
        let wrapped = "/bin/zsh -ilc \(shellQuote(shellLine))"
        switch app {
        case .terminal:
            return osascript([
                #"tell application "Terminal""#,
                "activate",
                "do script \(appleScriptString(wrapped))",
                "end tell",
            ])
        case .iterm:
            return osascript([
                #"tell application "iTerm""#,
                "activate",
                "set w to (create window with default profile)",
                "tell current session of w to write text \(appleScriptString(wrapped))",
                "end tell",
            ])
        case .ghostty:
            return LaunchCommand(
                executable: "/usr/bin/open",
                arguments: ["-na", "Ghostty", "--args", "-e", "/bin/zsh", "-ilc", shellLine])
        case .wezterm:
            return LaunchCommand(
                executable: "/usr/bin/open",
                arguments: ["-na", "WezTerm", "--args", "start", "--", "/bin/zsh", "-ilc", shellLine])
        }
    }

    // MARK: - Quoting

    /// One `-e` per script line, so no argument contains a newline.
    private static func osascript(_ lines: [String]) -> LaunchCommand {
        LaunchCommand(executable: "/usr/bin/osascript", arguments: lines.flatMap { ["-e", $0] })
    }

    /// Single-quotes `value` for sh: `it's` becomes `'it'\''s'`.
    public static func shellQuote(_ value: String) -> String {
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
