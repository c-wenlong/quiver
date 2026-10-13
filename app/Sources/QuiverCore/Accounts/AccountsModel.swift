import Foundation
import Observation

/// Anything that can report an account's usage. `UsageService` in the app.
public protocol UsageFetching: Sendable {
    func usage(for key: AccountKey, force: Bool) async -> AccountUsage
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
    var usageProvider: UsageProvider {
        switch vendor {
        case .claude: .claude
        case .codex: .codex
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

    private let store: ProfileStore
    private let usage: UsageFetching
    private let launcher: Launching
    private let terminal: () -> TerminalApp
    private let loginPath: @Sendable () async -> String
    private let renewer: ClaudeSignInRenewing
    private var generation = 0

    public init(store: ProfileStore, usage: UsageFetching, launcher: Launching,
                terminal: @escaping () -> TerminalApp,
                loginPath: @escaping @Sendable () async -> String = { await CLIPath.terminalShellPath() },
                renewer: ClaudeSignInRenewing = ClaudeSignInRenewer()) {
        self.store = store
        self.usage = usage
        self.launcher = launcher
        self.terminal = terminal
        self.loginPath = loginPath
        self.renewer = renewer
    }

    /// Reload profiles, then every account's usage at once. `force` skips the
    /// five-minute cache but never a provider's cooldown.
    public func load(force: Bool) async {
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

        let keys = profiles.map { profile in
            (profile.id, AccountKey(provider: profile.usageProvider,
                                    configHome: store.paths(for: profile).home.path,
                                    isDefault: profile.isDefault))
        }
        let usage = self.usage
        let results = await withTaskGroup(of: (String, AccountUsage).self) { group in
            for (id, key) in keys {
                group.addTask { (id, await usage.usage(for: key, force: force)) }
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

    /// What the Refresh buttons do, and only them: renew each expired
    /// Claude sign-in (Claude Code renews its own keychain credential;
    /// Quiver never touches the token), then load fresh. Nothing else —
    /// launch, timers, page appear, plain `load` — runs a renewal.
    public func refreshFromButton() async {
        // Cards already renewing skip this press: two `claude` runs in
        // one account race on refresh-token rotation and can sign it out.
        let expired = cards.filter {
            $0.profile.vendor == .claude && $0.usage?.status == .expired
                && !renewing.contains($0.id)
        }
        if !expired.isEmpty {
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
        }
        await load(force: true)
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

    /// The desktop app, signed in as this account.
    public func openApp(_ profile: Profile) async throws {
        try await launcher.launch(LaunchPlanner.desktopApp(for: profile, paths: store.paths(for: profile)))
    }

    /// The desktop app found at `path`, for an account whose app isn't in
    /// the usual place.
    public func openApp(_ profile: Profile, at path: String) async throws {
        try await launcher.launch(LaunchPlanner.desktopApp(for: profile, paths: store.paths(for: profile), appPath: path))
    }

    /// A harness's desktop app, as the user would open it.
    public func openApp(at path: String) async throws {
        try await launcher.launch(LaunchCommand(executable: "/usr/bin/open", arguments: ["-a", path]))
    }

    /// A harness's CLI in a terminal. The command comes from harness.json,
    /// so it is quoted as one word rather than run as shell text.
    public func openTerminal(command: String) async throws {
        try await launcher.launch(LaunchPlanner.terminal(terminal(), running: LaunchPlanner.shellQuote(command)))
    }

    /// A harness's own sign-in in a terminal, where its CLI has one.
    public func signIn(harness: String) async throws {
        guard let login = HarnessAccess.loginCommand(for: harness) else {
            throw LaunchError.failed("\(harness) signs in from inside its own app or CLI.")
        }
        try await launcher.launch(LaunchPlanner.terminal(terminal(), running: login))
    }

    /// The CLI in a terminal, as this account.
    public func openTerminal(_ profile: Profile) async throws {
        let paths = store.paths(for: profile)
        let line = LaunchPlanner.shellLine(for: profile, paths: paths,
                                           command: profile.vendor.rawValue)
        try await launcher.launch(LaunchPlanner.terminal(terminal(), running: line))
    }

    /// The CLI's sign-in flow in a terminal, for this account's folder.
    public func signIn(_ profile: Profile) async throws {
        let line = LaunchPlanner.cliLoginCommand(for: profile, paths: store.paths(for: profile))
        try await launcher.launch(LaunchPlanner.terminal(terminal(), running: line))
    }

    public func folder(of profile: Profile) -> URL {
        store.paths(for: profile).home
    }
}
