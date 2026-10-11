import Foundation
import Testing
@testable import QuiverCore

// Every expected string below is written out by hand, not built with the
// planner's own helpers, so a mistake in the planner's quoting cannot hide
// behind the same mistake in the test.

private let claudeDefault = Profile(
    id: "claude-default", vendor: .claude, name: "Default", isDefault: true, color: "#d97757")
private let codexDefault = Profile(
    id: "codex-default", vendor: .codex, name: "Default", isDefault: true, color: "#10a37f")
private let claudeWork = Profile(
    id: "claude-work", vendor: .claude, name: "Work", isDefault: false, color: "#3b82f6")
private let codexSide = Profile(
    id: "codex-side", vendor: .codex, name: "Side", isDefault: false, color: "#a855f7")

private func paths(_ home: String, desktop: String? = nil) -> ProfilePaths {
    ProfilePaths(
        home: URL(fileURLWithPath: home, isDirectory: true),
        desktopData: desktop.map { URL(fileURLWithPath: $0, isDirectory: true) })
}

private let claudeDefaultPaths = paths("/Users/me/.claude")
private let codexDefaultPaths = paths("/Users/me/.codex")
private let claudeWorkPaths = paths(
    "/Users/me/.quiver/accounts/claude/claude-work/home",
    desktop: "/Users/me/.quiver/accounts/claude/claude-work/desktop")
private let codexSidePaths = paths(
    "/Users/me/.quiver/accounts/codex/codex-side/home",
    desktop: "/Users/me/.quiver/accounts/codex/codex-side/desktop")
/// A home with a space and a single quote in it.
private let awkwardPaths = paths("/Users/me/it's a dir/home", desktop: "/Users/me/it's a dir/desktop")

@Suite struct DesktopLaunchTests {
    @Test func aClaudeProfileOpensASecondInstanceWithItsOwnHomeAndUserDataDir() {
        let command = LaunchPlanner.desktopApp(for: claudeWork, paths: claudeWorkPaths)
        #expect(command == LaunchCommand(executable: "/usr/bin/open", arguments: [
            "-n",
            "--env", "CLAUDE_CONFIG_DIR=/Users/me/.quiver/accounts/claude/claude-work/home",
            "-a", "/Applications/Claude.app",
            "--args", "--user-data-dir=/Users/me/.quiver/accounts/claude/claude-work/desktop",
        ]))
    }

    @Test func aCodexProfileUsesCodexHomeAndTheChatGPTBundle() {
        let command = LaunchPlanner.desktopApp(for: codexSide, paths: codexSidePaths)
        #expect(command == LaunchCommand(executable: "/usr/bin/open", arguments: [
            "-n",
            "--env", "CODEX_HOME=/Users/me/.quiver/accounts/codex/codex-side/home",
            "-a", "/Applications/ChatGPT.app",
            "--args", "--user-data-dir=/Users/me/.quiver/accounts/codex/codex-side/desktop",
        ]))
    }

    @Test func theDefaultProfilesOpenTheAppPlainlyWithNoEnvOrFlags() {
        #expect(LaunchPlanner.desktopApp(for: claudeDefault, paths: claudeDefaultPaths)
            == LaunchCommand(executable: "/usr/bin/open", arguments: ["-a", "/Applications/Claude.app"]))
        #expect(LaunchPlanner.desktopApp(for: codexDefault, paths: codexDefaultPaths)
            == LaunchCommand(executable: "/usr/bin/open", arguments: ["-a", "/Applications/ChatGPT.app"]))
    }

    @Test func pathsAreOneArgvElementEachSoSpacesAndQuotesNeedNoEscaping() {
        let command = LaunchPlanner.desktopApp(for: claudeWork, paths: awkwardPaths)
        #expect(command.arguments == [
            "-n",
            "--env", "CLAUDE_CONFIG_DIR=/Users/me/it's a dir/home",
            "-a", "/Applications/Claude.app",
            "--args", "--user-data-dir=/Users/me/it's a dir/desktop",
        ])
    }

    @Test func aNonDefaultProfileWithoutDesktopDataStillGetsItsHomeButNoFlag() {
        let command = LaunchPlanner.desktopApp(for: claudeWork, paths: paths("/Users/me/x/home"))
        #expect(command.arguments == [
            "-n", "--env", "CLAUDE_CONFIG_DIR=/Users/me/x/home", "-a", "/Applications/Claude.app",
        ])
    }

    @Test func theAppPathsAreExposedForAnInstalledCheck() {
        #expect(LaunchPlanner.desktopAppPath(for: .claude) == "/Applications/Claude.app")
        #expect(LaunchPlanner.desktopAppPath(for: .codex) == "/Applications/ChatGPT.app")
    }
}

@Suite struct ShellLineTests {
    @Test func aNonDefaultProfileExportsItsHomeThenRunsTheCommand() {
        #expect(LaunchPlanner.shellLine(for: claudeWork, paths: claudeWorkPaths, command: "claude")
            == "export CLAUDE_CONFIG_DIR='/Users/me/.quiver/accounts/claude/claude-work/home'; claude")
        #expect(LaunchPlanner.shellLine(for: codexSide, paths: codexSidePaths, command: "codex")
            == "export CODEX_HOME='/Users/me/.quiver/accounts/codex/codex-side/home'; codex")
    }

    @Test func aDefaultProfileIsJustTheCommand() {
        #expect(LaunchPlanner.shellLine(for: claudeDefault, paths: claudeDefaultPaths, command: "claude") == "claude")
        #expect(LaunchPlanner.shellLine(for: codexDefault, paths: codexDefaultPaths, command: "codex") == "codex")
    }

    @Test func theCommandIsAppendedAsGiven() {
        #expect(LaunchPlanner.shellLine(for: claudeWork, paths: claudeWorkPaths, command: "claude --resume abc")
            == "export CLAUDE_CONFIG_DIR='/Users/me/.quiver/accounts/claude/claude-work/home'; claude --resume abc")
    }

    @Test func aSingleQuoteInThePathIsClosedEscapedAndReopened() {
        #expect(LaunchPlanner.shellLine(for: claudeWork, paths: awkwardPaths, command: "claude")
            == #"export CLAUDE_CONFIG_DIR='/Users/me/it'\''s a dir/home'; claude"#)
    }

    @Test func otherShellCharactersStayInertInsideTheQuotes() {
        let nasty = paths("/Users/me/$HOME `x` \"y\" ; rm -rf ~/home")
        #expect(LaunchPlanner.shellLine(for: codexSide, paths: nasty, command: "codex")
            == #"export CODEX_HOME='/Users/me/$HOME `x` "y" ; rm -rf ~/home'; codex"#)
    }

    @Test func loginIsTheVendorsLoginSubcommandInsideTheSameLine() {
        #expect(LaunchPlanner.cliLoginCommand(for: claudeWork, paths: claudeWorkPaths)
            == "export CLAUDE_CONFIG_DIR='/Users/me/.quiver/accounts/claude/claude-work/home'; claude auth login")
        #expect(LaunchPlanner.cliLoginCommand(for: codexSide, paths: codexSidePaths)
            == "export CODEX_HOME='/Users/me/.quiver/accounts/codex/codex-side/home'; codex login")
        #expect(LaunchPlanner.cliLoginCommand(for: claudeDefault, paths: claudeDefaultPaths) == "claude auth login")
        #expect(LaunchPlanner.cliLoginCommand(for: codexDefault, paths: codexDefaultPaths) == "codex login")
    }

    @Test func eachVendorNamesItsCLI() {
        #expect(LaunchPlanner.cliName(for: .claude) == "claude")
        #expect(LaunchPlanner.cliName(for: .codex) == "codex")
    }
}

@Suite struct TerminalLaunchTests {
    /// A line with a path that has a space and a single quote, so it carries a backslash.
    private let line = #"export CLAUDE_CONFIG_DIR='/Users/me/it'\''s a dir/home'; claude"#

    @Test func terminalAppRunsTheLineWithDoScript() {
        #expect(LaunchPlanner.terminal(.terminal, running: line) == LaunchCommand(
            executable: "/usr/bin/osascript",
            arguments: [
                "-e", #"tell application "Terminal""#,
                "-e", "activate",
                // The backslash in the line is doubled inside the AppleScript string.
                "-e", #"do script "export CLAUDE_CONFIG_DIR='/Users/me/it'\\''s a dir/home'; claude""#,
                "-e", "end tell",
            ]))
    }

    @Test func itermWritesTheLineIntoANewWindow() {
        #expect(LaunchPlanner.terminal(.iterm, running: line) == LaunchCommand(
            executable: "/usr/bin/osascript",
            arguments: [
                "-e", #"tell application "iTerm""#,
                "-e", "activate",
                "-e", "set w to (create window with default profile)",
                "-e", #"tell current session of w to write text "export CLAUDE_CONFIG_DIR='/Users/me/it'\\''s a dir/home'; claude""#,
                "-e", "end tell",
            ]))
    }

    @Test func doubleQuotesAndNewlinesAreEscapedForAppleScript() {
        let tricky = "echo \"hi\"\necho 'a\\b'"
        let command = LaunchPlanner.terminal(.terminal, running: tricky)
        #expect(command.arguments[5] == #"do script "echo \"hi\"\necho 'a\\b'""#)
    }

    @Test func ghosttyPassesTheLineToZshAsOneArgument() {
        #expect(LaunchPlanner.terminal(.ghostty, running: line) == LaunchCommand(
            executable: "/usr/bin/open",
            arguments: ["-na", "Ghostty", "--args", "-e", "/bin/zsh", "-lc", line]))
    }

    @Test func weztermPassesTheLineToZshAsOneArgument() {
        #expect(LaunchPlanner.terminal(.wezterm, running: line) == LaunchCommand(
            executable: "/usr/bin/open",
            arguments: ["-na", "WezTerm", "--args", "start", "--", "/bin/zsh", "-lc", line]))
    }

    @Test func terminalAppsRoundTripThroughCodable() throws {
        #expect(TerminalApp.allCases == [.terminal, .iterm, .ghostty, .wezterm])
        #expect(TerminalApp.allCases.map(\.rawValue) == ["terminal", "iterm", "ghostty", "wezterm"])
        let data = try JSONEncoder().encode(TerminalApp.ghostty)
        #expect(String(decoding: data, as: UTF8.self) == "\"ghostty\"")
        #expect(try JSONDecoder().decode(TerminalApp.self, from: data) == .ghostty)
    }
}
