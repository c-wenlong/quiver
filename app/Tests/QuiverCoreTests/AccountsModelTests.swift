import Foundation
import Testing
@testable import QuiverCore

/// Answers usage by config home, and records every request.
final class FakeUsage: UsageFetching, @unchecked Sendable {
    private let lock = NSLock()
    var byHome: [String: AccountUsage] = [:]
    /// Readings the no-network cache answers with, keyed the same way.
    var cached: [String: AccountUsage] = [:]
    private var recorded: [(AccountKey, Bool)] = []
    /// A snapshot taken under the lock: the model's child tasks append concurrently.
    var requests: [(AccountKey, Bool)] { lock.withLock { recorded } }
    /// Config homes asked of the no-network cache.
    var cachedRequests: [String] { lock.withLock { recordedCached } }
    private var recordedCached: [String] = []

    func usage(for key: AccountKey, force: Bool, freshFor _: TimeInterval) async -> AccountUsage {
        lock.withLock {
            recorded.append((key, force))
            return byHome[key.configHome]
                ?? AccountUsage(status: .signedOut, plan: nil, email: nil, windows: [], fetchedAt: nil, isStale: false)
        }
    }

    func cachedUsage(for key: AccountKey) async -> AccountUsage? {
        lock.withLock {
            recordedCached.append(key.configHome)
            return cached[key.configHome]
        }
    }
}

final class FakeLauncher: Launching, @unchecked Sendable {
    private(set) var launched: [LaunchCommand] = []
    func launch(_ command: LaunchCommand) async throws { launched.append(command) }
}

/// Records the accounts asked to renew, and answers per home.
final class FakeRenewer: ClaudeSignInRenewing, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [(home: String, isDefault: Bool)] = []
    var results: [String: Bool] = [:]
    var renewals: [(home: String, isDefault: Bool)] { lock.withLock { recorded } }

    func renew(home: String, isDefault: Bool) async -> Bool {
        lock.withLock { recorded.append((home, isDefault)) }
        return results[home] ?? true
    }
}

/// Answers only once `releaseAll` runs, so a test can press Refresh
/// while a renewal is mid-flight.
final class SuspendingRenewer: ClaudeSignInRenewing, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [String] = []
    private var waiters: [CheckedContinuation<Void, Never>] = []
    var calls: [String] { lock.withLock { recorded } }

    func renew(home: String, isDefault _: Bool) async -> Bool {
        // Call record and waiter land under the same lock, so releaseAll
        // can never slip between them and leave the run suspended.
        await withCheckedContinuation { continuation in
            lock.withLock {
                recorded.append(home)
                waiters.append(continuation)
            }
        }
        return true
    }

    func releaseAll() {
        let pending = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            defer { waiters = [] }
            return waiters
        }
        for waiter in pending { waiter.resume() }
    }
}

/// Pretends apps are running with a given argv, and records activations.
@MainActor
final class FakeRunningInstances: RunningInstances {
    var instances: [RunningInstance] = []
    private(set) var activated: [Int32] = []
    func instances(ofAppAt _: String) -> [RunningInstance] { instances }
    func activate(pid: Int32) { activated.append(pid) }
}

@MainActor
@Suite struct AccountsModelTests {
    let root: URL
    let store: ProfileStore
    let usage = FakeUsage()
    let launcher = FakeLauncher()
    let running = FakeRunningInstances()

    init() throws {
        root = FileManager.default.temporaryDirectory.appending(path: "accounts-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
    }

    func model(terminal: TerminalApp = .terminal) -> AccountsModel {
        AccountsModel(store: store, usage: usage, launcher: launcher, terminal: { terminal },
                      loginPath: { "" }, running: running)
    }

    let reading = AccountUsage(
        status: .ok, plan: "max", email: "k@example.com",
        windows: [UsageWindow(label: "5h", usedPercent: 28, resetsAt: nil),
                  UsageWindow(label: "7d", usedPercent: 50, resetsAt: nil)],
        fetchedAt: Date(timeIntervalSince1970: 1_791_700_000), isStale: false)

    @Test func defaultsComeFirstThenAddedAccountsEachWithTheirUsage() async throws {
        let work = try store.add(vendor: .codex, name: "Work")
        usage.byHome[root.appending(path: ".claude").path] = reading
        let model = model()
        await model.load(force: false)

        #expect(model.cards.map(\.profile.id) == ["claude-default", "codex-default", work.id])
        #expect(model.cards[0].usage == reading)
        #expect(model.cards[2].usage?.status == .signedOut)
    }

    @Test func eachAccountIsAskedForByItsOwnConfigHome() async throws {
        let work = try store.add(vendor: .codex, name: "Work")
        await model().load(force: true)

        let homes = Set(usage.requests.map(\.0.configHome))
        #expect(homes == [root.appending(path: ".claude").path,
                          root.appending(path: ".codex").path,
                          store.paths(for: work).home.path])
        #expect(usage.requests.allSatisfy { $0.1 == true })
        let defaults = usage.requests.filter { $0.0.isDefault }.map(\.0.provider)
        #expect(Set(defaults) == [.claude, .codex])
    }

    @Test func openingTheAppLaunchesItWithThatAccountsFolders() async throws {
        let work = try store.add(vendor: .claude, name: "Work")
        let model = model()
        await model.load(force: false)
        try await model.openApp(work)

        let paths = store.paths(for: work)
        #expect(launcher.launched == [LaunchPlanner.desktopApp(for: work, paths: paths)])
        #expect(launcher.launched[0].arguments.contains("--user-data-dir=\(paths.desktopData!.path)"))
    }

    private let claudeExe = "/Applications/Claude.app/Contents/MacOS/Claude"
    private let personal = Profile(
        id: "claude-default", vendor: .claude, name: "Default", isDefault: true, color: "#d97757")

    private func cortexInstance(pid: Int32, for profile: Profile) -> RunningInstance {
        RunningInstance(pid: pid, arguments: [
            claudeExe, "--user-data-dir=\(store.paths(for: profile).desktopData!.path)"])
    }

    @Test func openingAnAlreadyRunningAccountFocusesItsInstanceInstead() async throws {
        let cortex = try store.add(vendor: .claude, name: "Cortex")
        running.instances = [cortexInstance(pid: 42, for: cortex),
                             RunningInstance(pid: 7, arguments: [claudeExe])]
        try await model().openApp(cortex)

        #expect(running.activated == [42])
        #expect(launcher.launched.isEmpty)
    }

    @Test func twoRunningCopiesOfAnAccountFocusTheFirst() async throws {
        let cortex = try store.add(vendor: .claude, name: "Cortex")
        running.instances = [cortexInstance(pid: 48138, for: cortex),
                             cortexInstance(pid: 32848, for: cortex)]
        try await model().openApp(cortex)

        #expect(running.activated == [32848])
        #expect(launcher.launched.isEmpty)
    }

    @Test func openingTheDefaultWhileAnotherAccountRunsLaunchesANewInstance() async throws {
        let cortex = try store.add(vendor: .claude, name: "Cortex")
        running.instances = [cortexInstance(pid: 42, for: cortex)]
        try await model().openApp(personal)

        #expect(running.activated.isEmpty)
        #expect(launcher.launched == [LaunchCommand(
            executable: "/usr/bin/open",
            arguments: ["-n", "-a", "/Applications/Claude.app"])])
    }

    @Test func openingTheDefaultFocusesItsRunningPlainInstance() async throws {
        running.instances = [RunningInstance(pid: 7, arguments: [claudeExe])]
        try await model().openApp(personal)

        #expect(running.activated == [7])
        #expect(launcher.launched.isEmpty)
    }

    @Test func openingTheDefaultWithNothingRunningOpensPlainly() async throws {
        running.instances = []
        try await model().openApp(personal)

        #expect(running.activated.isEmpty)
        #expect(launcher.launched == [LaunchCommand(
            executable: "/usr/bin/open", arguments: ["-a", "/Applications/Claude.app"])])
    }

    @Test func signingInRunsTheLoginInTheChosenTerminal() async throws {
        let work = try store.add(vendor: .codex, name: "Work")
        let model = model(terminal: .iterm)
        await model.load(force: false)
        try await model.signIn(work)

        let paths = store.paths(for: work)
        let line = LaunchPlanner.cliLoginCommand(for: work, paths: paths)
        #expect(launcher.launched == [LaunchPlanner.terminal(.iterm, running: line)])
    }

    @Test func addingAnAccountShowsItAfterReload() async throws {
        let model = model()
        await model.load(force: false)
        let added = try await model.add(vendor: .claude, name: "Personal", color: "#7f77dd")
        #expect(model.cards.map(\.profile.id).last == added.id)
        #expect(added.color == "#7f77dd")
    }

    @Test func aDefaultAccountCannotBeRemoved() async throws {
        let model = model()
        await model.load(force: false)
        let claude = try #require(model.cards.first?.profile)
        await #expect(throws: (any Error).self) { try await model.remove(claude) }
        #expect(model.cards.count == 2)
    }

    // MARK: Sign-in renewal

    func renewalModel(renewer: FakeRenewer) -> AccountsModel {
        AccountsModel(store: store, usage: usage, launcher: launcher,
                      terminal: { .terminal }, loginPath: { "" }, renewer: renewer)
    }

    @Test func refreshFromButtonRenewsOnlyExpiredClaudeCardsThenLoads() async throws {
        let work = try store.add(vendor: .claude, name: "Work")
        let expiredHome = store.paths(for: work).home.path
        usage.byHome[root.appending(path: ".claude").path] = AccountUsage(status: .expired)
        usage.byHome[expiredHome] = AccountUsage(status: .expired)
        // A signed-out card is not renewed; the codex card never is.
        let renewer = FakeRenewer()
        let model = renewalModel(renewer: renewer)
        await model.load(force: false)

        await model.refreshFromButton()

        let renewed = Set(renewer.renewals.map(\.home))
        #expect(renewed == [root.appending(path: ".claude").path, expiredHome])
        #expect(model.renewing.isEmpty)
        // After renewing, only the renewed accounts are read forced; the
        // healthy Codex account reuses its reading.
        let last = usage.requests.suffix(3)
        #expect(Set(last.filter { $0.1 }.map(\.0.configHome)) == [root.appending(path: ".claude").path, expiredHome])
        #expect(last.filter { !$0.1 }.count == 1)
    }

    @Test func aFailedRenewalIsMarkedUntilTheAccountIsNoLongerExpired() async throws {
        let renewer = FakeRenewer()
        let model = renewalModel(renewer: renewer)
        let home = root.appending(path: ".claude").path
        usage.byHome[home] = AccountUsage(status: .expired)
        await model.load(force: false)

        renewer.results[home] = false
        await model.refreshFromButton()
        #expect(model.renewFailed == ["claude-default"])

        // A later load finds it renewed: the failure clears.
        usage.byHome[home] = reading
        await model.load(force: true)
        #expect(model.renewFailed.isEmpty)
        #expect(renewer.renewals.count == 1)
    }

    @Test func aSignInExpiredSinceTheLastReadingIsRenewed() async throws {
        let renewer = FakeRenewer()
        let model = renewalModel(renewer: renewer)
        let home = root.appending(path: ".claude").path
        usage.byHome[home] = reading
        await model.load(force: false)

        // The token lapses between readings; the press's first load sees it.
        usage.byHome[home] = AccountUsage(status: .expired)
        let readsBefore = usage.requests.count
        await model.refreshFromButton()

        #expect(renewer.renewals.map(\.home) == [home])
        // A forced read finds the lapse, and the renewed account is forced
        // again after renewing, so a failed renewal can't hide.
        let reads = usage.requests[readsBefore...].filter { $0.0.configHome == home }
        // The press's load fetches it unforced (the card read ok before),
        // finds it expired, then the renewal forces the re-read.
        #expect(reads.map(\.1) == [false, true])
    }

    @Test func aPressWithNothingExpiredLoadsOnce() async throws {
        let renewer = FakeRenewer()
        let model = renewalModel(renewer: renewer)
        let home = root.appending(path: ".claude").path
        usage.byHome[home] = reading
        await model.load(force: false)
        let readsBefore = usage.requests.count
        await model.refreshFromButton()
        let reads = usage.requests[readsBefore...].filter { $0.0.configHome == home }
        // Nothing failed: the healthy card is read unforced, and swe's
        // freshness window decides whether a fetch even happens.
        #expect(reads.map(\.1) == [false])
        #expect(renewer.renewals.isEmpty)
    }

    @Test func refreshForcesOnlyTheCardsThatFailed() async throws {
        let model = renewalModel(renewer: FakeRenewer())
        let claudeHome = root.appending(path: ".claude").path
        let codexHome = root.appending(path: ".codex").path
        usage.byHome[claudeHome] = reading
        usage.byHome[codexHome] = AccountUsage(status: .error("down"))
        await model.load(force: false)
        let readsBefore = usage.requests.count

        await model.refreshFromButton()

        let reads = usage.requests[readsBefore...]
        let forced = Set(reads.filter { $0.1 }.map(\.0.configHome))
        // The error card is forced; the healthy one is a cache-eligible read.
        #expect(forced == [codexHome])
        #expect(Set(reads.map(\.0.configHome)) == [claudeHome, codexHome])
    }

    @Test func aStaleReadingCountsAsFailedForRefresh() async throws {
        let model = renewalModel(renewer: FakeRenewer())
        let home = root.appending(path: ".claude").path
        usage.byHome[home] = AccountUsage(status: .ok, windows: [], fetchedAt: .now, isStale: true)
        await model.load(force: false)
        let readsBefore = usage.requests.count

        await model.refreshFromButton()

        let reads = usage.requests[readsBefore...].filter { $0.0.configHome == home }
        #expect(reads.first?.1 == true)
    }

    @Test func cardsHydrateFromTheNoNetworkCacheBeforeTheFetch() async throws {
        let model = renewalModel(renewer: FakeRenewer())
        let home = root.appending(path: ".claude").path
        // What last launch stored: the reading plus the failure it ended on.
        usage.cached[home] = AccountUsage(status: .rateLimited(until: .distantFuture),
                                          windows: reading.windows,
                                          fetchedAt: .now, isStale: true)
        await model.load(force: false)
        // The cache answered without a keychain read and the card saw it.
        #expect(usage.cachedRequests.contains(home))
    }

    @Test func aSecondPressSkipsAccountsAlreadyRenewing() async throws {
        let renewer = SuspendingRenewer()
        let model = AccountsModel(store: store, usage: usage, launcher: launcher,
                                  terminal: { .terminal }, loginPath: { "" }, renewer: renewer)
        let home = root.appending(path: ".claude").path
        usage.byHome[home] = AccountUsage(status: .expired)
        await model.load(force: false)

        async let first: Void = model.refreshFromButton()
        // Wait until the renewal is actually running before pressing again.
        while renewer.calls.isEmpty { try await Task.sleep(for: .milliseconds(5)) }
        #expect(model.renewing == ["claude-default"])

        // A second press loads but never starts a second claude run.
        await model.refreshFromButton()
        #expect(renewer.calls == [home])

        renewer.releaseAll()
        await first
        #expect(model.renewing.isEmpty)
        #expect(renewer.calls.count == 1)
    }

    @Test func plainLoadNeverRenews() async throws {
        let renewer = FakeRenewer()
        let model = renewalModel(renewer: renewer)
        usage.byHome[root.appending(path: ".claude").path] = AccountUsage(status: .expired)
        await model.load(force: true)
        await model.load(force: false)
        #expect(renewer.renewals.isEmpty)
    }
}

@MainActor
@Suite struct LaunchTargetTests {
    let root = FileManager.default.temporaryDirectory.appending(path: "launch-\(UUID().uuidString)")
    let launcher = FakeLauncher()

    func harnessTarget(appPath: String?, cliInstalled: Bool?,
                       name: String = "cursor", command: String = "cursor-agent") -> LaunchTarget {
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
        return LaunchTarget(kind: .harness(name: name, command: command),
                            model: AccountsModel(store: store, usage: FakeUsage(), launcher: launcher,
                                                 terminal: { .terminal }, loginPath: { "" }),
                            installedApp: { _ in appPath }, cliInstalled: { _ in cliInstalled })
    }

    @Test func theAppIsPreferredOverTheTerminal() async throws {
        let target = harnessTarget(appPath: "/Applications/Cursor.app", cliInstalled: true)
        #expect(target.appPath == "/Applications/Cursor.app")
        #expect(try await target.openPreferred() == .app)
        #expect(launcher.launched == [LaunchCommand(executable: "/usr/bin/open",
                                                  arguments: ["-a", "/Applications/Cursor.app"])])
    }

    @Test func withNoAppTheTerminalOpensTheCLI() async throws {
        let target = harnessTarget(appPath: nil, cliInstalled: true)
        #expect(try await target.openPreferred() == .terminal)
        // The harness's command goes out as one word, not the account's line.
        #expect(launcher.launched.count == 1)
        #expect(launcher.launched[0].executable == "/usr/bin/osascript")
        #expect(launcher.launched[0].arguments.contains { $0.contains("cursor-agent") })
    }

    @Test func aCliNotCheckedYetStillOffersTheTerminal() async throws {
        let target = harnessTarget(appPath: nil, cliInstalled: nil)
        #expect(target.canLaunch)
        #expect(try await target.openPreferred() == .terminal)
    }

    @Test func neitherAvailableLaunchesNothing() async throws {
        let target = harnessTarget(appPath: nil, cliInstalled: false)
        #expect(!target.canLaunch)
        #expect(try await target.openPreferred() == .none)
        #expect(launcher.launched.isEmpty)
    }

    @Test func anAccountLaunchesTheAppWithItsFolders() async throws {
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
        let work = try store.add(vendor: .claude, name: "Work")
        let model = AccountsModel(store: store, usage: FakeUsage(), launcher: launcher,
                                  terminal: { .terminal }, loginPath: { "" })
        let target = LaunchTarget(kind: .account(work), model: model,
                                  installedApp: { _ in "/Applications/Claude.app" },
                                  cliInstalled: { _ in true })
        #expect(try await target.openPreferred() == .app)
        // The account form exports the profile home into the app launch.
        #expect(launcher.launched[0].arguments.contains("CLAUDE_CONFIG_DIR=\(store.paths(for: work).home.path)"))
    }

    @Test func anAccountTerminalRunsAsThatAccount() async throws {
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts2"), userHome: root)
        let work = try store.add(vendor: .claude, name: "Work")
        let model = AccountsModel(store: store, usage: FakeUsage(), launcher: launcher,
                                  terminal: { .terminal }, loginPath: { "" })
        let target = LaunchTarget(kind: .account(work), model: model,
                                  installedApp: { _ in nil }, cliInstalled: { _ in true })
        #expect(try await target.openPreferred() == .terminal)
        // Not the bare harness command: the profile's export line.
        #expect(launcher.launched[0].arguments.contains {
            $0.contains(store.paths(for: work).home.path) && $0.contains("claude")
        })
    }
}

@Suite struct ClaudeSignInRenewerTests {
    let base: [String: String] = [
        "PATH": "/usr/bin:/opt/homebrew/bin",
        "ANTHROPIC_API_KEY": "sk-test",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "CLAUDE_CONFIG_DIR": "/somewhere/else",
        "HOME": "/Users/me",
    ]

    @Test func theRunIsOneThrowawayPrompt() {
        let (arguments, _) = ClaudeSignInRenewer.plan(home: "/h", isDefault: false, base: [:])
        #expect(arguments == ["-p", "ok", "--model", "haiku",
                              "--no-session-persistence", "--tools", ""])
    }

    @Test func keysAndTokensLeaveTheEnvironment() {
        let (_, env) = ClaudeSignInRenewer.plan(home: "/h", isDefault: false, base: base)
        #expect(env["ANTHROPIC_API_KEY"] == nil)
        #expect(env["ANTHROPIC_AUTH_TOKEN"] == nil)
        #expect(env["CLAUDE_CODE_OAUTH_TOKEN"] == nil)
        #expect(env["PATH"] == base["PATH"])
        #expect(env["HOME"] == base["HOME"])
    }

    @Test func aNonDefaultProfileGetsItsHomeAndTheDefaultNone() {
        let (_, named) = ClaudeSignInRenewer.plan(home: "/u/.quiver/accounts/work/.claude",
                                                  isDefault: false, base: base)
        #expect(named["CLAUDE_CONFIG_DIR"] == "/u/.quiver/accounts/work/.claude")
        let (_, standard) = ClaudeSignInRenewer.plan(home: "/u/.claude",
                                                     isDefault: true, base: base)
        #expect(standard["CLAUDE_CONFIG_DIR"] == nil)
    }

    private func cli(_ script: String, timeout: Duration = .seconds(3),
                     sigtermGrace: Duration = .milliseconds(200))
        throws -> (dir: URL, renewer: ClaudeSignInRenewer) {
        let dir = FileManager.default.temporaryDirectory
            .appending(path: "renew-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let executable = dir.appending(path: "claude")
        try script.write(to: executable, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755],
                                              ofItemAtPath: executable.path)
        let path = dir.path
        return (dir, ClaudeSignInRenewer(
            timeout: timeout, sigtermGrace: sigtermGrace,
            command: "claude", workingDirectory: dir,
            environment: { ["PATH": path] }))
    }

    @Test func aCleanExitRenews() async throws {
        let (dir, renewer) = try cli("#!/bin/sh\nexit 0\n")
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(await renewer.renew(home: dir.path, isDefault: true))
    }

    @Test func aStuckRunIsTerminatedThenKilled() async throws {
        // A child that ignores SIGTERM is SIGKILLed; renew never reports
        // done while it still runs.
        // exec keeps the ignored TERM, so only SIGKILL can end it. Its
        // marker file proves the child really started before the timeout.
        let (dir, renewer) = try cli("#!/bin/sh\ntrap '' TERM\n/usr/bin/touch started\nexec /bin/sleep 60\n",
                                     timeout: .milliseconds(1500), sigtermGrace: .milliseconds(300))
        defer { try? FileManager.default.removeItem(at: dir) }
        let start = ContinuousClock.now
        #expect(await renewer.renew(home: dir.path, isDefault: true) == false)
        let elapsed = ContinuousClock.now - start
        #expect(FileManager.default.fileExists(atPath: dir.appending(path: "started").path))
        #expect(elapsed >= .milliseconds(1750))
        #expect(elapsed < .seconds(5))
    }

    @Test func aFailingRunDoesNotRenew() async throws {
        let (dir, renewer) = try cli("#!/bin/sh\nexit 1\n")
        defer { try? FileManager.default.removeItem(at: dir) }
        #expect(await renewer.renew(home: dir.path, isDefault: true) == false)
    }
}
