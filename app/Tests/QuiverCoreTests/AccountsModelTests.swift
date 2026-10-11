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
}
