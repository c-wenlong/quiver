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
            skills.use(swe, for: swePath)
            others = OtherLimitsModel(swe: swe)
            Task { await others.load() }
        }
    }

    /// Which terminal Open in Terminal and Sign in use.
    /// Saved menu bar choices brought up to date before they're read.
    @ObservationIgnored private let migrated: Void = MenuBarMigrations.run(on: .standard)

    /// What the menu bar item shows, and whether it's there at all.
    var menuBar = MenuBarDisplay(defaults: .standard) {
        didSet {
            menuBar.save(to: .standard)
            if menuBar.isVisible != oldValue.isVisible { applyDockPresence() }
        }
    }

    /// What the menu bar popup lists.
    var popup = MenuBarPopup(defaults: .standard) {
        didSet { popup.save(to: .standard) }
    }

    var terminal: TerminalApp {
        didSet { UserDefaults.standard.set(terminal.rawValue, forKey: "terminalApp") }
    }

    /// The Dock icon, light unless changed in Settings.
    var appIcon: AppIconStyle {
        didSet {
            UserDefaults.standard.set(appIcon.rawValue, forKey: "appIcon")
            applyAppIcon()
        }
    }

    /// Shows the chosen icon in the Dock and the app switcher. The light one
    /// is the bundle's own, so it needs nothing set.
    func applyAppIcon() {
        guard appIcon != .light,
              let url = Bundle.main.url(forResource: appIcon.resourceName, withExtension: "icns") else {
            NSApp.applicationIconImage = nil
            return
        }
        NSApp.applicationIconImage = NSImage(contentsOf: url)
    }

    /// Light, dark, or macOS's own setting (the default).
    var appearance: AppearanceMode {
        didSet {
            UserDefaults.standard.set(appearance.rawValue, forKey: AppearanceMode.defaultsKey)
            applyAppearance()
        }
    }

    /// Every window and the menu bar panel follow NSApp's appearance; the
    /// Theme colours resolve against it.
    func applyAppearance() {
        NSApp.appearance = switch appearance {
        case .system: nil
        case .light: NSAppearance(named: .aqua)
        case .dark: NSAppearance(named: .darkAqua)
        }
    }

    /// On by default. Off makes Quiver a menu bar app: no Dock icon and no
    /// place in the app switcher, unless the menu bar item is hidden too.
    var showsInDock: Bool {
        didSet {
            UserDefaults.standard.set(showsInDock, forKey: DockPresence.defaultsKey)
            applyDockPresence()
        }
    }

    /// Whether the Dock icon actually shows, after the menu bar item rule.
    var dockIconShown: Bool {
        DockPresence.showsIcon(requested: showsInDock, menuBarVisible: menuBar.isVisible)
    }

    func applyDockPresence() {
        let policy: NSApplication.ActivationPolicy = dockIconShown ? .regular : .accessory
        guard NSApp.activationPolicy() != policy else { return }
        NSApp.setActivationPolicy(policy)
        // A new Dock icon starts from the bundle's; the switch also sends
        // the open window behind other apps, so bring it back.
        if policy == .regular { applyAppIcon() }
        NSApp.activate()
    }

    let accounts: AccountsModel
    /// Limits of the harnesses with no Quiver account, from swe.
    private(set) var others: OtherLimitsModel
    /// Every limit together: the status item, notifications and widgets.
    let limits: LimitsMonitor
    /// Where each harness's desktop app is installed, if anywhere.
    let apps = AppLocator()
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
    /// The Skills, MCP servers and Plugins pages' last lists, kept while the app runs.
    let skills: SkillsModel
    let mcp: McpModel
    let plugins: PluginsModel
    /// Every plugin in every marketplace: the Plugins page's Explore tab,
    /// and what the Skills page moves plugin skills out by.
    let pluginCatalog: PluginCatalogModel
    /// The Plugins page's tab, so another page can open Explore.
    var pluginsTab: PluginsTab = .health
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
        appIcon = AppIconStyle(saved: UserDefaults.standard.string(forKey: "appIcon"))
        appearance = AppearanceMode(saved: UserDefaults.standard.string(forKey: AppearanceMode.defaultsKey))
        showsInDock = UserDefaults.standard.object(forKey: DockPresence.defaultsKey) == nil
            || UserDefaults.standard.bool(forKey: DockPresence.defaultsKey)

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
                                     defaultHomes: [home.appending(path: ".claude").path, home.appending(path: ".codex").path],
                                     snapshots: SessionSnapshotFile(url: quiver.appending(path: "cache/sessions.json")))
        } catch {
            overview = nil
            sessions = nil
            overviewError = "Couldn't open the usage ledger: \(error.localizedDescription)"
        }
        skills = SkillsModel(swe: client, snapshots: SnapshotFile(url: quiver.appending(path: "cache/skills.json")))
        // The Skills page's hub tab is now Plugins > Explore, kept in
        // plugin-catalog.json; its old snapshot has no reader left.
        try? FileManager.default.removeItem(at: quiver.appending(path: "cache/skills-hub.json"))
        skills.use(client, for: saved)
        mcp = McpModel(snapshots: SnapshotFile(url: quiver.appending(path: "cache/mcp-servers.json")))
        // One plugin change at a time, whichever Plugins tab starts it.
        let pluginWork = PluginWork()
        plugins = PluginsModel(snapshots: SnapshotFile(url: quiver.appending(path: "cache/plugins.json")),
                               work: pluginWork)
        pluginCatalog = PluginCatalogModel(snapshots: SnapshotFile(url: quiver.appending(path: "cache/plugin-catalog.json")),
                                           work: pluginWork, disclosure: UserDefaultsStore())
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
            // The last session list shows at once; swe lists again behind it,
            // so the page is usually ready before it is opened.
            // The other swe pages only read what they showed last; swe runs
            // when one is opened.
            Task { [skills] in await skills.restore() },
            Task { [mcp] in await mcp.restore() },
            Task { [plugins] in await plugins.restore() },
            Task { [pluginCatalog] in await pluginCatalog.restore() },
            // Skills and Plugins > Explore place one inventory against one
            // catalog, so each skill shows on exactly one of them: each
            // model is handed the other's latest.
            Task { [weak self] in
                let changes = Observations { [weak self] in self?.skills.inventory }
                for await _ in changes {
                    guard let self else { return }
                    pluginCatalog.skills = skills.allSkills
                }
            },
            Task { [weak self] in
                let changes = Observations { [weak self] in self?.pluginCatalog.revision }
                for await _ in changes {
                    guard let self else { return }
                    skills.plugins = pluginCatalog.lookup
                }
            },
            Task { [weak self] in
                guard let sessions = self?.sessions else { return }
                await sessions.restore()
                await sessions.loadIfStale(for: self?.swePath ?? "")
            },
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
                    if let self { await self.providers.recheck(for: self.swePath) }
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

/// Provider visibility and the Explore tab's open state live in the app's own preferences.
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
