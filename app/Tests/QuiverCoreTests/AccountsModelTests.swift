import Foundation
import Testing
@testable import QuiverCore

/// Answers usage by config home, and records every request.
final class FakeUsage: UsageFetching, @unchecked Sendable {
    private let lock = NSLock()
    var byHome: [String: AccountUsage] = [:]
    private var recorded: [(AccountKey, Bool)] = []
    /// A snapshot taken under the lock: the model's child tasks append concurrently.
    var requests: [(AccountKey, Bool)] { lock.withLock { recorded } }

    func usage(for key: AccountKey, force: Bool) async -> AccountUsage {
        lock.withLock {
            recorded.append((key, force))
            return byHome[key.configHome]
                ?? AccountUsage(status: .signedOut, plan: nil, email: nil, windows: [], fetchedAt: nil, isStale: false)
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
        lock.withLock { recorded.append(home) }
        await withCheckedContinuation { continuation in
            lock.withLock { waiters.append(continuation) }
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

@MainActor
@Suite struct AccountsModelTests {
    let root: URL
    let store: ProfileStore
    let usage = FakeUsage()
    let launcher = FakeLauncher()

    init() throws {
        root = FileManager.default.temporaryDirectory.appending(path: "accounts-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)
    }

    func model(terminal: TerminalApp = .terminal) -> AccountsModel {
        AccountsModel(store: store, usage: usage, launcher: launcher, terminal: { terminal }, loginPath: { "" })
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
        // The reload afterwards is forced.
        #expect(usage.requests.last?.1 == true)
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
}
