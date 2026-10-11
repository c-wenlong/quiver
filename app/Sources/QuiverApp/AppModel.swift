import AppKit
import Foundation
import Observation
import QuiverCore

/// App-wide state shared by the window and the menu bar panel.
@MainActor
@Observable
final class AppModel {
    static let shared = AppModel()

    private(set) var swe: SweClient
    /// The open page, kept across launches.
    var selection: AppSection? = UserDefaults.standard.string(forKey: "lastSection")
        .flatMap(AppSection.init(rawValue:)) ?? .overview {
        didSet { UserDefaults.standard.set(selection?.rawValue, forKey: "lastSection") }
    }

    /// Opens (or reopens) the main window. Captured from the window's scene,
    /// because the menu bar panel lives outside SwiftUI's scene graph and
    /// its own `openWindow` cannot reach the `Window` scene.
    @ObservationIgnored var openMainWindow: (() -> Void)?

    /// The swe chosen in Settings; empty means whatever PATH finds.
    var swePath: String {
        didSet {
            UserDefaults.standard.set(swePath, forKey: SweLocation.defaultsKey)
            swe = Self.client(for: swePath)
            others = OtherLimitsModel(swe: swe)
            Task { await others.load() }
        }
    }

    /// Which terminal Open in Terminal and Sign in use.
    var terminal: TerminalApp {
        didSet { UserDefaults.standard.set(terminal.rawValue, forKey: "terminalApp") }
    }

    let accounts: AccountsModel
    /// Limits of the harnesses with no Quiver account, from swe.
    private(set) var others: OtherLimitsModel
    /// Every limit together: the status item, notifications and widgets.
    let limits: LimitsMonitor
    /// App-wide so unsaved AGENTS.md edits outlive a trip to another page.
    let agentsMd = AgentsModel()
    private(set) var apiKeys: APIKeysModel

    /// The shell file of API keys; empty means ~/.api_keys.
    var apiKeysPath: String {
        didSet {
            UserDefaults.standard.set(apiKeysPath, forKey: "apiKeysPath")
            apiKeys = Self.keysModel(path: apiKeysPath)
        }
    }
    /// Nil when the ledger database could not be opened; `overviewError` says why.
    let overview: OverviewModel?
    let overviewError: String?
    let serviceStatus: ServiceStatusModel
    /// Which providers show, in what order (Settings).
    let providers: ProviderVisibilityModel
    /// Nil when the ledger is unavailable, like `overview`.
    let sessions: SessionsModel?
    private let launcher = ProcessLauncher()
    private let userHome = FileManager.default.homeDirectoryForCurrentUser

    init() {
        let saved = UserDefaults.standard.string(forKey: SweLocation.defaultsKey) ?? ""
        swePath = saved
        let client = Self.client(for: saved)
        swe = client
        others = OtherLimitsModel(swe: client)
        terminal = UserDefaults.standard.string(forKey: "terminalApp")
            .flatMap(TerminalApp.init(rawValue:)) ?? .terminal

        let keysPath = UserDefaults.standard.string(forKey: "apiKeysPath") ?? ""
        apiKeysPath = keysPath
        apiKeys = Self.keysModel(path: keysPath)

        // Everything Quiver keeps lives under ~/.quiver (see AGENTS.md).
        let home = FileManager.default.homeDirectoryForCurrentUser
        let quiver = home.appending(path: ".quiver")
        limits = LimitsMonitor(quiver: quiver)
        let usage = UsageService(
            transport: URLSessionTransport(),
            secrets: KeychainCLI(),
            files: LocalFiles(),
            cacheURL: quiver.appending(path: "cache/app-usage.json"))
        let store = ProfileStore(root: quiver.appending(path: "accounts"), userHome: home)
        var terminalChoice: () -> TerminalApp = { .terminal }
        accounts = AccountsModel(store: store, usage: usage, launcher: ProcessLauncher(),
                                 terminal: { terminalChoice() })

        let directory: @Sendable () -> AccountDirectory = {
            AccountDirectory(profiles: (try? store.load()) ?? [], paths: store.paths(for:), home: home)
        }
        do {
            let pricing = PricingStore(cacheFile: quiver.appending(path: "cache/pricing/litellm.json"))
            let ledger = try Ledger(cacheDirectory: quiver.appending(path: "cache/ledger"),
                                    pricing: (try? pricing.load()) ?? .empty)
            overview = OverviewModel(ledger: ledger, roots: { directory().roots },
                                     accountLabel: { directory().label(for: $0) },
                                     refreshPricing: { await pricing.refreshIfStale() })
            overviewError = nil
            sessions = SessionsModel(source: CurrentSwe(), ledger: ledger,
                                     defaultHomes: [home.appending(path: ".claude").path, home.appending(path: ".codex").path])
        } catch {
            overview = nil
            sessions = nil
            overviewError = "Couldn't open the usage ledger: \(error.localizedDescription)"
        }
        serviceStatus = ServiceStatusModel(service: ServiceStatusService(
            transport: URLSessionTransport(), cacheURL: quiver.appending(path: "cache/service-status.json")))
        providers = ProviderVisibilityModel(store: UserDefaultsStore(), detect: {
            // swe's installed harnesses, plus the extra providers' own signs.
            let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
            let swe = SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
            // A failure is passed on, not read as "nothing installed".
            let harnesses = try await swe.harnesses(withUsage: false)
            let shellPath = await LoginShell.cachedEnvironment()["PATH"] ?? ""
            return Set(ProviderDetection.installed(
                sweHarnesses: ProviderDetection.installedHarnesses(harnesses),
                commandExists: { ProviderDetection.commandExists($0, path: shellPath) },
                pathExists: { FileManager.default.fileExists(atPath: $0) }))
        })
        terminalChoice = { [unowned self] in self.terminal }
    }

    private var refreshing: Task<Void, Never>?

    private var watchers: [Task<Void, Never>] = []

    /// Keep the ledger scanned and every limit fetched while the app runs.
    /// One set of loops serves the window, the menu bar and the widgets.
    func start() {
        if statusPolling == nil {
            statusPolling = Task { [serviceStatus] in await serviceStatus.keepFresh() }
        }
        guard watchers.isEmpty else { return }
        if let overview { refreshing = Task { await overview.keepFresh() } }
        watchers = [
            Task { await accounts.keepFresh() },
            Task { [weak self] in
                while !Task.isCancelled {
                    await self?.others.load()
                    try? await Task.sleep(for: .seconds(300))
                }
            },
            // Limits change on a fetch; whether a window has reset changes
            // with the clock, so look again every minute as well.
            Task { [weak self] in
                let changes = Observations { [weak self] in
                    self.map { ($0.accounts.cards, $0.others.rows) }
                }
                for await _ in changes { self?.refreshLimits() }
            },
            Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(60))
                    self?.refreshLimits()
                }
            },
            Task { [weak self] in
                let changes = Observations { [weak self] in
                    self.map { ($0.overview?.activity, $0.overview?.recentModels, $0.limits.readings) }
                }
                for await _ in changes { self?.limits.writeWidgets(overview: self?.overview) }
            },
            // Notifications may be switched on or off in System Settings
            // while Quiver is in the background, and a harness installed
            // from a terminal; the provider check runs again, quietly.
            Task { [weak self] in
                await self?.limits.checkNotificationPermission()
                for await _ in NotificationCenter.default.notifications(named: NSApplication.didBecomeActiveNotification) {
                    await self?.limits.checkNotificationPermission()
                    await self?.providers.recheck()
                }
            },
        ]
    }

    private func refreshLimits() {
        limits.update(LimitReading.all(cards: accounts.cards, others: others.rows, now: .now))
    }

    /// Keeps the Service status page and the menu bar line current.
    private var statusPolling: Task<Void, Never>?
    /// Resume a session in the chosen terminal, in the account it ran under.
    func resume(_ row: SessionsModel.Row) async throws {
        guard let resume = row.session.resume else { return }
        let home = row.usage?.accountHome
        let defaults = [".claude", ".codex"].map { userHome.appending(path: $0).path }
        let line = SessionResume.shellLine(resume, harness: row.session.harness, accountHome: home,
                                           isDefaultHome: home.map(defaults.contains) ?? true)
        try await launcher.launch(LaunchPlanner.terminal(terminal, running: line))
    }

    static func keysFile(path: String) -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let trimmed = path.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return home.appending(path: ".api_keys") }
        return URL(fileURLWithPath: (trimmed as NSString).expandingTildeInPath)
    }

    private static func keysModel(path: String) -> APIKeysModel {
        // One backup of the previous version, private to you, under ~/.quiver.
        let backups = FileManager.default.homeDirectoryForCurrentUser.appending(path: ".quiver/backups/api-keys")
        return APIKeysModel(store: APIKeysStore(url: keysFile(path: path), backupDirectory: backups),
                            shell: LoginShellEnvironment())
    }

    private static func client(for path: String) -> SweClient {
        SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
    }
}

/// Provider visibility lives in the app's own preferences.
private struct UserDefaultsStore: KeyValueStoring {
    func strings(forKey key: String) -> [String]? { UserDefaults.standard.stringArray(forKey: key) }
    func set(_ strings: [String], forKey key: String) { UserDefaults.standard.set(strings, forKey: key) }
}

/// The swe chosen in Settings at the moment of each call.
private struct CurrentSwe: SessionListing {
    func sessions(days: Int, limit: Int) async throws -> SessionList {
        let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
        return try await SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
            .sessions(days: days, limit: limit)
    }
}
