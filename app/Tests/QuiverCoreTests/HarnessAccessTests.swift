import Foundation
import Testing
@testable import QuiverCore

@Suite struct HarnessAccessTests {
    let home = "/Users/k"

    func locator(_ existing: Set<String>) -> AppLocator {
        AppLocator(home: home, fileExists: { existing.contains($0) })
    }

    @Test func anAppIsFoundInEitherApplicationsFolder() {
        #expect(locator(["/Applications/Devin.app"]).installedApp(for: "devin") == "/Applications/Devin.app")
        #expect(locator(["/Users/k/Applications/Cursor.app"]).installedApp(for: "cursor") == "/Users/k/Applications/Cursor.app")
        #expect(locator([]).installedApp(for: "cursor") == nil)
    }

    @Test func claudeAndCodexUseTheirVendorsApps() {
        #expect(locator(["/Applications/Claude.app"]).installedApp(for: "claude") == "/Applications/Claude.app")
        #expect(locator(["/Applications/ChatGPT.app"]).installedApp(for: "codex") == "/Applications/ChatGPT.app")
    }

    @Test func aHarnessWithNoKnownAppHasNone() {
        #expect(locator(["/Applications/Freebuff.app"]).installedApp(for: "freebuff") == nil)
        #expect(locator(["/Applications/Anything.app"]).installedApp(for: "unknown") == nil)
    }

    @Test func signInIsOfferedOnlyWhereTheCLIHasACommandForIt() {
        #expect(HarnessAccess.loginCommand(for: "cursor") == "cursor-agent login")
        #expect(HarnessAccess.loginCommand(for: "devin") == "devin auth login")
        #expect(HarnessAccess.loginCommand(for: "freebuff") == "freebuff login")
        #expect(HarnessAccess.loginCommand(for: "copilot") == "gh auth login")
        // Droid signs in from inside its own TUI.
        #expect(HarnessAccess.loginCommand(for: "droid") == nil)
        #expect(HarnessAccess.loginCommand(for: "unknown") == nil)
    }
}

@MainActor
@Suite struct HarnessLaunchTests {
    let launcher = FakeLauncher()
    /// Empty: a real scan would find the user's actual apps running and
    /// these tests must never activate them.
    let running = FakeRunningInstances()

    func model() throws -> AccountsModel {
        let root = FileManager.default.temporaryDirectory.appending(path: "access-\(UUID().uuidString)")
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
        // Real stub CLIs on the injected PATH: signIn refuses a missing one.
        let bin = root.appending(path: "bin")
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        for cli in ["claude", "codex", "devin", "cursor-agent", "gh", "freebuff"] {
            FileManager.default.createFile(
                atPath: bin.appending(path: cli).path, contents: Data("#!/bin/sh\n".utf8),
                attributes: [.posixPermissions: 0o755])
        }
        return AccountsModel(store: store, usage: FakeUsage(), launcher: launcher, terminal: { .ghostty },
                             loginPath: { bin.path }, running: running)
    }

    @Test func openingAHarnessAppOpensThatBundle() async throws {
        try await model().openApp(at: "/Applications/Devin.app")
        #expect(launcher.launched == [LaunchCommand(executable: "/usr/bin/open", arguments: ["-a", "/Applications/Devin.app"])])
    }

    @Test func aHarnessTerminalRunsItsCommandQuotedAsOneWord() async throws {
        try await model().openTerminal(command: "cursor-agent; rm -rf ~")
        let line = try #require(launcher.launched.first?.arguments.last)
        #expect(line == "'cursor-agent; rm -rf ~'")
    }

    @Test func signingInAHarnessRunsItsLoginCommand() async throws {
        try await model().signIn(harness: "devin")
        #expect(launcher.launched.first?.arguments.last == "devin auth login")
    }

    @Test func aHarnessWithNoLoginCommandRefuses() async throws {
        await #expect(throws: LaunchError.self) { try await model().signIn(harness: "droid") }
        #expect(launcher.launched.isEmpty)
    }

    @Test func aProfileOpensTheAppWhereItWasFound() async throws {
        let model = try model()
        await model.load(force: false)
        let claude = try #require(model.cards.first { $0.profile.vendor == .claude }?.profile)
        try await model.openApp(claude, at: "/Users/k/Applications/Claude.app")
        #expect(launcher.launched.first?.arguments == ["-a", "/Users/k/Applications/Claude.app"])
    }
}

@Suite struct CLIPathTests {
    @Test func aCommandIsOnPathOnlyAsAnExecutableFile() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "path-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let tool = dir.appending(path: "claude")
        try Data("#!/bin/sh\n".utf8).write(to: tool)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tool.path)
        try Data("x".utf8).write(to: dir.appending(path: "codex"))
        let path = "/nonexistent:\(dir.path)"
        #expect(CLIPath.contains("claude", path: path))
        // Present but not executable is not runnable.
        #expect(!CLIPath.contains("codex", path: path))
        #expect(!CLIPath.contains("devin", path: path))
        #expect(!CLIPath.contains("../claude", path: path))
    }
}

@MainActor
@Suite struct AccountCLICheckTests {
    @Test func loadingChecksEachVendorsCLIDirectly() async throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "cli-\(UUID().uuidString)")
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
        let bin = root.appending(path: "bin")
        try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
        try Data("#!/bin/sh\n".utf8).write(to: bin.appending(path: "claude"))
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: bin.appending(path: "claude").path)
        let model = AccountsModel(store: store, usage: FakeUsage(), launcher: FakeLauncher(), terminal: { .terminal },
                                  loginPath: { "/nonexistent:\(bin.path)" })
        #expect(model.cliOnPath.isEmpty)
        await model.load(force: false)
        #expect(model.cliOnPath == [.claude: true, .codex: false, .devin: false, .cursor: false])
    }
}

/// The Accounts page's Sign in vs Install choice, from `AccountSignIn`.
@Suite struct AccountSignInTests {
    @Test func installedMeansSignIn() {
        #expect(AccountSignIn.action(cli: "codex", harness: "codex",
                                     installed: true) == .signIn)
        #expect(AccountSignIn.action(cli: "claude", harness: "claude",
                                     installed: true) == .signIn)
    }

    @Test func missingCLIAsksToInstallFirst() {
        #expect(AccountSignIn.action(cli: "codex", harness: "codex",
                                     installed: false) == .install(harness: "codex"))
        #expect(AccountSignIn.action(cli: "cursor-agent", harness: "cursor",
                                     installed: false) == .install(harness: "cursor"))
        // Copilot signs in with gh; the sheet installs the harness.
        #expect(AccountSignIn.action(cli: "gh", harness: "copilot",
                                     installed: false) == .install(harness: "copilot"))
    }

    @Test func unknownOrAppSignInKeepsSignIn() {
        // Data not loaded yet.
        #expect(AccountSignIn.action(cli: "codex", harness: "codex",
                                     installed: nil) == .signIn)
        // Cursor profiles sign in inside their isolated app.
        #expect(AccountSignIn.action(cli: nil, harness: "cursor",
                                     installed: false) == .signIn)
    }

    @Test func cliTakesTheLoginCommandsFirstWord() {
        #expect(AccountSignIn.cli(loginCommand: "gh auth login") == "gh")
        #expect(AccountSignIn.cli(loginCommand: "devin auth login") == "devin")
        #expect(AccountSignIn.cli(loginCommand: "cursor-agent login") == "cursor-agent")
    }
}

@MainActor
@Suite struct SignInInstallGuardTests {
    let launcher = FakeLauncher()
    let running = FakeRunningInstances()

    private func model(path: String = "") throws -> AccountsModel {
        let root = FileManager.default.temporaryDirectory.appending(path: "guard-\(UUID().uuidString)")
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
        return AccountsModel(store: store, usage: FakeUsage(), launcher: launcher,
                             terminal: { .terminal }, loginPath: { path }, running: running)
    }

    @Test func aMissingHarnessCLIThrowsWithoutLaunching() async throws {
        await #expect(throws: LaunchError.self) {
            try await model().signIn(harness: "devin")
        }
        #expect(launcher.launched.isEmpty)
    }

    @Test func aMissingVendorCLIThrowsWithoutLaunching() async throws {
        let m = try model()
        let root = FileManager.default.temporaryDirectory.appending(path: "guard-\(UUID().uuidString)")
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
        let work = try store.add(vendor: .codex, name: "Work")
        let m2 = AccountsModel(store: store, usage: FakeUsage(), launcher: launcher,
                               terminal: { .terminal }, loginPath: { "" }, running: running)
        await m2.load(force: true)
        _ = m
        await #expect(throws: LaunchError.self) { try await m2.signIn(work) }
        #expect(launcher.launched.isEmpty)
    }
}
