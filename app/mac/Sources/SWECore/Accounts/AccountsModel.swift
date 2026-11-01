import Foundation
import Observation

/// Anything that can report an account's usage. `UsageService` in the app.
public protocol UsageFetching: Sendable {
    /// `freshFor`: how old a good reading may be before this call refetches.
    func usage(for key: AccountKey, force: Bool, freshFor: TimeInterval) async -> AccountUsage
    /// The stored reading and last failure without touching the network or
    /// the keychain, for filling a card before the first fetch lands.
    func cachedUsage(for key: AccountKey) async -> AccountUsage?
}

extension UsageService: UsageFetching {}

/// Starts a planned launch. Real launches spawn a process; tests record.
public protocol Launching: Sendable {
    func launch(_ command: LaunchCommand) async throws
}

public enum LaunchError: Error, LocalizedError, Equatable {
    case failed(String)

    public var errorDescription: String? {
        switch self {
        case .failed(let message): message
        }
    }
}

/// Runs the command and waits for it to report back. Every planned launch is
/// a short-lived `open` or `osascript` that hands off to the real app and
/// exits, so its status says whether the app or terminal was found. One
/// still running after `patience` is left to finish on its own.
public struct ProcessLauncher: Launching {
    var patience: Duration

    public init(patience: Duration = .seconds(15)) {
        self.patience = patience
    }

    public func launch(_ command: LaunchCommand) async throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: command.executable)
        process.arguments = command.arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        let err = Pipe()
        process.standardError = err
        let exited = AsyncStream<Int32> { continuation in
            process.terminationHandler = { finished in
                continuation.yield(finished.terminationStatus)
                continuation.finish()
            }
        }
        do {
            try process.run()
        } catch {
            throw LaunchError.failed("Couldn't run \(command.executable): \(error.localizedDescription)")
        }
        let status: Int32? = await withTaskGroup(of: Int32?.self) { group in
            group.addTask { await exited.first { _ in true } }
            group.addTask { [patience] in
                try? await Task.sleep(for: patience)
                return nil
            }
            let first = await group.next() ?? nil
            group.cancelAll()
            return first
        }
        guard let status, status != 0 else { return }
        let message = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        throw LaunchError.failed(message.isEmpty ? "\(command.executable) exited with status \(status)" : message)
    }
}

extension Profile {
    /// The usage service only reads Claude and Codex; a Devin or Cursor
    /// extra account's limit comes from `swe harness usage`, not here.
    var usageProvider: UsageProvider? {
        switch vendor {
        case .claude: .claude
        case .codex: .codex
        case .devin, .cursor: nil
        }
    }
}

/// The Accounts page: every profile with its latest usage, plus the actions
/// that open an account in its app or a terminal.
@MainActor
@Observable
public final class AccountsModel {
    public struct Card: Identifiable, Equatable, Sendable {
        public var id: String { profile.id }
        public var profile: Profile
        public var usage: AccountUsage?
    }

    public private(set) var cards: [Card] = []
    /// Whether each vendor's CLI is on the PATH Terminal's shell sees, checked on
    /// every load. swe's registry says the same for harnesses it knows, but
    /// a fresh registry may not list Claude or Codex at all.
    public private(set) var cliOnPath: [Vendor: Bool] = [:]
    /// Claude accounts whose sign-in renewal is running right now.
    public private(set) var renewing: Set<String> = []
    /// Accounts whose last renewal failed; cleared when a load finds the
    /// account no longer expired.
    public private(set) var renewFailed: Set<String> = []
    public private(set) var isLoading = false
    public private(set) var error: String?

    /// How long a good reading counts as fresh for a refresh pass: the
    /// Settings "Refresh only what failed for" window, in seconds.
    public var freshFor: TimeInterval = 300

    private let store: ProfileStore
    private let usage: UsageFetching
    private let launcher: Launching
    private let running: RunningInstances
    private let terminal: () -> TerminalApp
    private let terminalInstalled: (TerminalApp) -> Bool
    private let loginPath: @Sendable () async -> String
    private let renewer: ClaudeSignInRenewing
    /// Where a vendor's desktop app is installed (the same AppLocator the
    /// Open-app buttons use); nil means the default /Applications path.
    private let installedApp: (String) -> String?
    private var generation = 0

    public init(store: ProfileStore, usage: UsageFetching, launcher: Launching,
                terminal: @escaping () -> TerminalApp,
                terminalInstalled: @escaping (TerminalApp) -> Bool = { _ in true },
                loginPath: @escaping @Sendable () async -> String = { await CLIPath.terminalShellPath() },
                renewer: ClaudeSignInRenewing = ClaudeSignInRenewer(),
                running: RunningInstances = AppRunningInstances(),
                installedApp: @escaping (String) -> String? = { _ in nil }) {
        self.store = store
        self.usage = usage
        self.launcher = launcher
        self.running = running
        self.terminal = terminal
        self.terminalInstalled = terminalInstalled
        self.loginPath = loginPath
        self.renewer = renewer
        self.installedApp = installedApp
    }

    /// Reload profiles, then every account's usage at once. `force` skips the
    /// five-minute cache but never a provider's cooldown; `forcing` skips
    /// it for just those account ids.
    public func load(force: Bool, forcing: Set<String> = []) async {
        generation += 1
        let mine = generation
        let profiles: [Profile]
        do {
            profiles = try store.load()
            error = nil
        } catch {
            self.error = "Couldn't read your accounts: \(error.localizedDescription)"
            // This is the newest load; an older one can no longer clear it.
            isLoading = false
            return
        }
        // One shell start for every vendor's check.
        let path = await loginPath()
        var found: [Vendor: Bool] = [:]
        for vendor in Vendor.allCases { found[vendor] = CLIPath.contains(LaunchPlanner.cliName(for: vendor), path: path) }
        guard mine == generation else { return }
        cliOnPath = found
        let previous = Dictionary(uniqueKeysWithValues: cards.map { ($0.id, $0.usage) })
        cards = profiles.map { Card(profile: $0, usage: previous[$0.id] ?? nil) }
        isLoading = true
        defer { if mine == generation { isLoading = false } }

        let keys = profiles.compactMap { profile -> (String, AccountKey)? in
            guard let provider = profile.usageProvider else { return nil }
            return (profile.id, AccountKey(provider: provider,
                                           configHome: store.paths(for: profile).home.path,
                                           isDefault: profile.isDefault))
        }
        let usage = self.usage
        // While the network runs, cards show what the last launches stored:
        // the reading and the last failure, no keychain read, no prompt.
        for (id, key) in keys where cards.first(where: { $0.id == id })?.usage == nil {
            if let cached = await usage.cachedUsage(for: key) {
                guard mine == generation else { return }
                cards = cards.map { $0.id == id ? Card(profile: $0.profile, usage: cached) : $0 }
            }
        }
        let freshFor = self.freshFor
        let results = await withTaskGroup(of: (String, AccountUsage).self) { group in
            for (id, key) in keys {
                group.addTask {
                    (id, await usage.usage(for: key, force: force || forcing.contains(id),
                                           freshFor: freshFor))
                }
            }
            var out: [String: AccountUsage] = [:]
            for await (id, reading) in group { out[id] = reading }
            return out
        }
        guard mine == generation else { return }
        cards = cards.map { Card(profile: $0.profile, usage: results[$0.id] ?? $0.usage) }
        // A sign-in that came back (or was never expired) clears its
        // failed renewal.
        for card in cards where card.usage?.status != .expired {
            renewFailed.remove(card.id)
        }
    }

    /// What the Refresh buttons do, and only them: re-check what failed
    /// (a healthy reading inside the `freshFor` window comes back from the
    /// cache untouched), renew each expired Claude sign-in (Claude Code
    /// renews its own keychain credential; SWE never touches the
    /// token), then load again so the renewed accounts read ok. Nothing
    /// else — launch, timers, page appear, plain `load` — runs a renewal.
    public func refreshFromButton() async {
        // The first load is what may show a sign-in expired since the
        // last reading; renewals are picked from that, not stale cards.
        // Only the failures are forced: a card that read fine a minute ago
        // keeps its reading rather than spend a network call on it.
        let failed = Set(cards.filter {
            $0.usage == nil || $0.usage?.status != .ok || $0.usage?.isStale == true
        }.map(\.id))
        await load(force: false, forcing: failed)
        // Cards already renewing skip this press: two `claude` runs in
        // one account race on refresh-token rotation and can sign it out.
        let expired = cards.filter {
            $0.profile.vendor == .claude && $0.usage?.status == .expired
                && !renewing.contains($0.id)
        }
        guard !expired.isEmpty else { return }
        let targets = expired.map { card in
            (id: card.id, home: store.paths(for: card.profile).home.path,
             isDefault: card.profile.isDefault)
        }
        renewing.formUnion(targets.map(\.id))
        let renewer = self.renewer
        let results = await withTaskGroup(of: (String, Bool).self) { group in
            for target in targets {
                group.addTask {
                    (target.id, await renewer.renew(home: target.home, isDefault: target.isDefault))
                }
            }
            var out: [String: Bool] = [:]
            for await (id, ok) in group { out[id] = ok }
            return out
        }
        renewing.subtract(targets.map(\.id))
        renewFailed.formUnion(targets.map(\.id).filter { results[$0] == false })
        // Only the renewed accounts are forced: a failed renewal must not
        // hide behind a reading from before the token lapsed, and healthy
        // accounts reuse the readings the first load just took.
        await load(force: false, forcing: Set(targets.map(\.id)))
    }

    /// Reload on a timer until the calling task is cancelled, so a page left
    /// open never shows a reading the usage service would no longer stand
    /// behind (it ages out readings past 24 h). Not forced: the five-minute
    /// cache and any cooldown still apply.
    public func keepFresh(every interval: Duration = .seconds(300)) async {
        while !Task.isCancelled {
            await load(force: false)
            do {
                try await Task.sleep(for: interval)
            } catch {
                return
            }
        }
    }

    @discardableResult
    public func add(vendor: Vendor, name: String, color: String?) async throws -> Profile {
        let profile = try store.add(vendor: vendor, name: name, color: color)
        await load(force: false)
        return profile
    }

    public func remove(_ profile: Profile) async throws {
        try store.remove(id: profile.id)
        await load(force: false)
    }

    public func rename(_ profile: Profile, to name: String) async throws {
        try store.rename(id: profile.id, to: name)
        await load(force: false)
    }

    /// The desktop app, signed in as this account. Resolves where the app
    /// is installed the same way the Open-app buttons do — a Cursor.app in
    /// ~/Applications counts — and falls back to the /Applications path.
    public func openApp(_ profile: Profile) async throws {
        try await openApp(profile, at: installedApp(profile.vendor.rawValue)
                            ?? LaunchPlanner.desktopAppPath(for: profile.vendor))
    }

    /// The desktop app found at `path`, for an account whose app isn't in
    /// the usual place. If an instance already running under this account's
    /// `--user-data-dir` (or none, for the default) answers, it is focused
    /// instead of launching a second process — the two Cortex processes from
    /// double-clicking came from skipping that check. When no instance of
    /// this account runs but another account's does, the default profile is
    /// launched with `-n` or `open` would just activate that account.
    public func openApp(_ profile: Profile, at path: String) async throws {
        let paths = store.paths(for: profile)
        let instances = running.instances(ofAppAt: path)
        if let mine = instances
            .filter({ LaunchPlanner.instance($0.arguments, belongsTo: profile, paths: paths) })
            .min(by: { $0.pid < $1.pid }) {
            running.activate(pid: mine.pid)
            return
        }
        try await launcher.launch(LaunchPlanner.desktopApp(
            for: profile, paths: paths, appPath: path,
            otherInstanceRunning: !instances.isEmpty))
    }

    /// A harness's desktop app, as the user would open it. Its "instance"
    /// is the one with no `--user-data-dir`: extra profiles of the same
    /// app carry one, so a running extra must not swallow this open —
    /// when only extras run, `open -n` starts the default beside them.
    public func openApp(at path: String) async throws {
        let instances = running.instances(ofAppAt: path)
        if let plain = instances
            .filter({ LaunchPlanner.userDataDir(in: $0.arguments) == nil })
            .min(by: { $0.pid < $1.pid }) {
            running.activate(pid: plain.pid)
            return
        }
        let arguments = instances.isEmpty ? ["-a", path] : ["-n", "-a", path]
        try await launcher.launch(LaunchCommand(executable: "/usr/bin/open", arguments: arguments))
    }

    /// A harness's CLI in a terminal. The command comes from harness.json,
    /// so it is quoted as one word rather than run as shell text.
    public func openTerminal(command: String) async throws {
        try await launchInTerminal(LaunchPlanner.shellQuote(command))
    }

    /// An already-built shell line in a terminal. For callers like the Add
    /// harness sheet that compose a command of several words; unlike
    /// `openTerminal(command:)` the line is trusted as shell text, so the
    /// caller must quote its tokens.
    public func runInTerminal(line: String) async throws {
        try await launchInTerminal(line)
    }

    /// Every terminal launch goes through here, so a chosen terminal that
    /// isn't installed fails with a clear message rather than the raw
    /// LaunchServices or osascript error.
    private func launchInTerminal(_ line: String) async throws {
        let app = terminal()
        guard terminalInstalled(app) else { throw LaunchError.failed(app.notInstalledMessage) }
        try await launcher.launch(LaunchPlanner.terminal(app, running: line))
    }

    /// A harness's own sign-in in a terminal, where its CLI has one.
    public func signIn(harness: String) async throws {
        guard let login = HarnessAccess.loginCommand(for: harness) else {
            throw LaunchError.failed("\(harness) signs in from inside its own app or CLI.")
        }
        let cli = AccountSignIn.cli(loginCommand: login)
        if !CLIPath.contains(cli, path: await loginPath()) {
            throw LaunchError.failed("\(cli) isn't installed. Install it first.")
        }
        try await launchInTerminal(login)
    }

    /// The CLI in a terminal, as this account.
    public func openTerminal(_ profile: Profile) async throws {
        let paths = store.paths(for: profile)
        let line = LaunchPlanner.shellLine(for: profile, paths: paths,
                                           command: LaunchPlanner.cliName(for: profile.vendor))
        try await launchInTerminal(line)
    }

    /// The CLI's sign-in flow in a terminal, for this account's folder.
    /// Cursor has no CLI sign-in of its own (`cursor-agent` keeps the one
    /// account): its profiles sign in inside their isolated Cursor app.
    public func signIn(_ profile: Profile) async throws {
        let paths = store.paths(for: profile)
        guard let line = LaunchPlanner.cliLoginCommand(for: profile, paths: paths) else {
            try await openApp(profile)
            return
        }
        // Defence in depth under the menu's Install: refuse to open a
        // terminal that would answer "command not found".
        if cliOnPath[profile.vendor] == false {
            throw LaunchError.failed(
                "\(LaunchPlanner.cliName(for: profile.vendor)) isn't installed. Install it first.")
        }
        try await launchInTerminal(line)
    }

    /// The CLI this account's Sign in runs, or nil when it signs in inside
    /// its own app (a Cursor profile), which needs no CLI.
    public func signInCLI(for profile: Profile) -> String? {
        LaunchPlanner.cliLoginCommand(for: profile, paths: store.paths(for: profile)) == nil
            ? nil : LaunchPlanner.cliName(for: profile.vendor)
    }

    public func folder(of profile: Profile) -> URL {
        store.paths(for: profile).home
    }

    /// The ledger rows an Accounts card's history sums. A profile card
    /// sums its own home (a Cursor profile, its app data folder, which
    /// Cursor activity is keyed on). A harness card with no profile sums
    /// every home of that harness except those `profiles` own, since
    /// each of those has a card of its own.
    public func historyKey(harness: String, profile: Profile?,
                           profiles: [Profile]) -> AccountHistory.Key {
        if let profile {
            return AccountHistory.Key(harness: harness, homes: [ledgerHome(of: profile)])
        }
        let owned = profiles.filter { $0.vendor.rawValue == harness }.map(ledgerHome(of:))
        return AccountHistory.Key(harness: harness, homes: nil, excluding: Set(owned))
    }

    /// The account home the ledger keys a profile's activity on.
    private func ledgerHome(of profile: Profile) -> String {
        let paths = store.paths(for: profile)
        let home = profile.vendor == .cursor ? (paths.desktopData ?? paths.home) : paths.home
        return home.standardizedFileURL.path
    }
}
