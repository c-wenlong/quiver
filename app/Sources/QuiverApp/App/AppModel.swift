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

    /// Tapping the sidebar item or tab of the view already shown bumps
    /// this; a detail page open there goes back through its own ask.
    var navReselects = 0

    /// The sidebar pinned to an icon rail; hovering it floats it open.
    var sidebarCollapsed = UserDefaults.standard.bool(forKey: "sidebarCollapsed") {
        didSet { UserDefaults.standard.set(sidebarCollapsed, forKey: "sidebarCollapsed") }
    }

    /// The pinned sidebar's width, clamped on load so a stale value
    /// cannot leave the window unusable.
    var sidebarWidth: Double = {
        let stored = UserDefaults.standard.object(forKey: "sidebarWidth") as? Double
        return stored.map(SidebarWidth.clamped) ?? SidebarWidth.standard
    }() {
        didSet { UserDefaults.standard.set(sidebarWidth, forKey: "sidebarWidth") }
    }

    /// Every non-drag width change goes through here, so menu commands
    /// and VoiceOver share the clamp and the persisted write.
    func adjustSidebarWidth(by delta: Double) {
        sidebarWidth = SidebarWidth.clamped(sidebarWidth + delta)
    }

    func resetSidebarWidth() {
        sidebarWidth = SidebarWidth.standard
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
            others = OtherLimitsModel(swe: swe, extraAccounts: extraAccountsProvider)
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
    /// What each desktop widget lists; a change rewrites their snapshot
    /// and redraws them right away.
    var widgetSelection = WidgetSelection(defaults: .standard) {
        didSet {
            widgetSelection.save(to: .standard)
            let style = widgetHarnessStyle
            limits.writeWidgets(overview: overview, selection: widgetSelection,
                                harnessOrder: style.order, harnessColors: style.colors)
        }
    }

    /// Resolved widget line colours, by "harness|primaryColor":
    /// resolve() reads logo SVGs off disk, and the Widgets page's
    /// snapshot asks for this every body pass.
    @ObservationIgnored private var widgetColourCache: [String: String] = [:]

    /// The Settings provider order and each harness's trend-chart colour
    /// as hex, as the widget snapshot's per-harness lines want them.
    var widgetHarnessStyle: (order: [String], colors: [String: String]) {
        let order = providers.rows.map(\.provider.id)
        let primaries = Dictionary(compute.matchers.map { ($0.harness, $0.primaryColor) },
                                   uniquingKeysWith: { first, _ in first })
        var colors: [String: String] = [:]
        var harnesses = Set(order)
        harnesses.formUnion((overview?.activityRows ?? []).map(\.harness))
        for harness in harnesses {
            let primary = primaries[harness] ?? nil
            let key = "\(harness)|\(primary ?? "")"
            if let cached = widgetColourCache[key] {
                colors[harness] = cached
            } else {
                let hex = HarnessColor.resolve(id: harness, primaryColor: primary).hex
                widgetColourCache[key] = hex
                colors[harness] = hex
            }
        }
        return (order, colors)
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
    /// Account cards whose harness is starred in swe's registry: an
    /// unstarred Claude or Codex hides its card, and its limit with it,
    /// until it is starred again. Extra Devin/Cursor accounts are harness
    /// cards instead (`others`), so they never enter this list.
    var starredAccountCards: [AccountsModel.Card] {
        accounts.cards.filter {
            $0.profile.vendor.hasDefaultProfile && others.isStarred($0.profile.vendor.rawValue)
        }
    }
    /// Every limit together: the status item, notifications and widgets.
    let limits: LimitsMonitor
    /// Where each harness's desktop app is installed, if anywhere.
    let apps = AppLocator()
    /// Extra Devin/Cursor profiles with their account dirs, shared by every
    /// OtherLimitsModel a swe-path change creates.
    @ObservationIgnored private var extraAccountsProvider: @MainActor () -> [(Profile, ProfilePaths)] = { [] }
    /// Every Codex account's CODEX_HOME: the default `~/.codex` plus
    /// each Codex profile's home in the profile store.
    @ObservationIgnored private(set) var codexHomes: @MainActor () -> [String] = { [] }
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

    /// The managed MCP servers folder; empty means ~/.quiver/mcp/servers.
    var mcpServersPath: String {
        didSet { UserDefaults.standard.set(mcpServersPath, forKey: "mcpServersPath") }
    }

    /// The app "Open in editor" uses, kept by name; empty means the first found.
    var codeEditor: String {
        didSet { UserDefaults.standard.set(codeEditor, forKey: "codeEditor") }
    }

    /// The editor "Open in editor" uses: the chosen one, or the first installed.
    var editor: CodeEditor? {
        let installed = CodeEditors.installed()
        return installed.first { $0.name == codeEditor } ?? installed.first
    }
    /// Nil when the ledger database could not be opened; `overviewError` says why.
    let overview: OverviewModel?
    let overviewError: String?
    let serviceStatus: ServiceStatusModel
    /// Which providers show, in what order (Settings).
    let providers: ProviderVisibilityModel
    /// What the starred harnesses are running right now (the Compute page).
    let compute: ComputeModel
    /// Nil when the ledger is unavailable, like `overview`.
    let sessions: SessionsModel?
    /// The Calendar page's activity blocks; nil with `sessions`.
    let calendar: CalendarModel?
    /// Cursor's per-event usage feed into the ledger; nil with `sessions`.
    let cursorFeed: CursorUsageFeed?
    /// Each Codex account's daily totals against what this Mac logged;
    /// nil with `sessions`.
    let codexUsage: CodexAccountUsage?
    /// The Pricing page's per-model rates; nil with `sessions`.
    let pricingModel: PricingModel?
    /// The Skills, MCP servers and Plugins pages' last lists, kept while the app runs.
    let skills: SkillsModel
    let mcp: McpModel
    /// The MCP servers page's "Server folders" section.
    let mcpRepo = McpServerRepoModel()
    /// The Settings > Updates section's build and release state.
    let updates: UpdatesModel
    let plugins: PluginsModel
    /// Every plugin in every marketplace: the Plugins page's Explore tab,
    /// and what the Skills page moves plugin skills out by.
    let pluginCatalog: PluginCatalogModel
    /// The Plugins page's tab, so another page can open Explore.
    var pluginsTab: PluginsTab = .health
    /// Quiver's display names for marketplaces and plugins, from
    /// ~/.quiver/config/plugin-display-names.json.
    var primitiveNames: PluginDisplayNames
    private let launcher = ProcessLauncher()
    private let userHome = FileManager.default.homeDirectoryForCurrentUser

    init() {
        let saved = UserDefaults.standard.string(forKey: SweLocation.defaultsKey) ?? ""
        swePath = saved
        let client = Self.client(for: saved)
        swe = client
        terminal = UserDefaults.standard.string(forKey: "terminalApp")
            .flatMap(TerminalApp.init(rawValue:)) ?? .terminal
        appIcon = AppIconStyle(saved: UserDefaults.standard.string(forKey: "appIcon"))
        appearance = AppearanceMode(saved: UserDefaults.standard.string(forKey: AppearanceMode.defaultsKey))
        showsInDock = UserDefaults.standard.object(forKey: DockPresence.defaultsKey) == nil
            || UserDefaults.standard.bool(forKey: DockPresence.defaultsKey)

        let keysPath = UserDefaults.standard.string(forKey: "apiKeysPath") ?? ""
        apiKeysPath = keysPath
        apiKeys = Self.keysModel(path: keysPath)
        mcpServersPath = UserDefaults.standard.string(forKey: "mcpServersPath") ?? ""
        codeEditor = UserDefaults.standard.string(forKey: "codeEditor") ?? ""

        // Everything Quiver keeps lives under ~/.quiver (see AGENTS.md).
        let home = FileManager.default.homeDirectoryForCurrentUser
        let quiver = home.appending(path: ".quiver")
        primitiveNames = PluginDisplayNames.load(from: PluginDisplayNames.file(home: home))
        limits = LimitsMonitor(quiver: quiver)
        let usage = UsageService(
            transport: URLSessionTransport(),
            secrets: KeychainCLI(),
            files: LocalFiles(),
            cacheURL: quiver.appending(path: "cache/app-usage.json"))
        let store = ProfileStore(root: quiver.appending(path: "accounts"), userHome: home)
        // Non-default Devin/Cursor profiles are accounts of a harness: swe
        // reads their usage from the account's own directory.
        extraAccountsProvider = {
            ((try? store.load()) ?? [])
                .filter { !$0.isDefault && !$0.vendor.hasDefaultProfile }
                .map { ($0, store.paths(for: $0)) }
        }
        others = OtherLimitsModel(swe: client, extraAccounts: extraAccountsProvider)
        codexHomes = {
            var homes = [home.appending(path: ".codex").path]
            for profile in (try? store.load()) ?? []
            where profile.vendor == .codex && !profile.isDefault {
                homes.append(store.paths(for: profile).home.path)
            }
            return homes
        }
        var terminalChoice: () -> TerminalApp = { .terminal }
        accounts = AccountsModel(store: store, usage: usage, launcher: ProcessLauncher(),
                                 terminal: { terminalChoice() },
                                 installedApp: { [apps] in apps.installedApp(for: $0) })

        // Built once per profiles.json mtime, not per call: the calendar
        // asks for account labels once per rendered block.
        let directoryCache = CachedAccountDirectory(
            file: quiver.appending(path: "accounts/profiles.json")) {
                AccountDirectory(profiles: (try? store.load()) ?? [],
                                 paths: store.paths(for:), home: home)
            }
        let directory: @Sendable () -> AccountDirectory = { directoryCache.current }
        do {
            // User prices (config/pricing.json) win over both published lists.
            let pricing = PricingStore(cacheFile: quiver.appending(path: "cache/pricing/litellm.json"),
                                       overridesFile: quiver.appending(path: "config/pricing.json"))
            let ledger = try Ledger(cacheDirectory: quiver.appending(path: "cache/ledger"),
                                    pricing: (try? pricing.load()) ?? .empty)
            overview = OverviewModel(ledger: ledger, roots: { directory().roots },
                                     accountLabel: { directory().label(for: $0) },
                                     refreshPricing: { await pricing.refreshIfStale() })
            overviewError = nil
            sessions = SessionsModel(source: CurrentSwe(), ledger: ledger,
                                     defaultHomes: [home.appending(path: ".claude").path, home.appending(path: ".codex").path],
                                     snapshots: SessionSnapshotFile(url: quiver.appending(path: "cache/sessions.json")))
            let calendarCache = quiver.appending(path: "cache/calendar")
            let calendarSnapshots = SnapshotFile<CalendarSnapshot>(
                url: quiver.appending(path: "cache/app-calendar.json"))
            let fileProviders: [any ActivityProviding] = [
                DroidActivity(sessionsRoot: home.appending(path: ".factory/sessions"),
                              accountHome: home.appending(path: ".factory").path,
                              index: ActivityFileIndex(url: calendarCache.appending(path: "droid.json"))),
                PiActivity(sessionsRoot: home.appending(path: ".pi/agent/sessions"),
                           accountHome: home.appending(path: ".pi").path,
                           index: ActivityFileIndex(url: calendarCache.appending(path: "pi.json"))),
                FreebuffActivity(projectsRoot: home.appending(path: ".config/manicode/projects"),
                                 accountHome: home.appending(path: ".config/manicode").path,
                                 index: ActivityFileIndex(url: calendarCache.appending(path: "freebuff.json"))),
                CursorAppActivity(userDataDirs: {
                    var dirs = [home.appending(path: "Library/Application Support/Cursor")]
                    dirs += ((try? store.load()) ?? [])
                        .filter { $0.vendor == .cursor && !$0.isDefault }
                        .compactMap { store.paths(for: $0).desktopData }
                    return dirs
                }, index: ActivityFileIndex(url: calendarCache.appending(path: "cursor.json"))),
            ]
            calendar = CalendarModel(providers: [ledger] + fileProviders,
                                     accountLabel: { directory().label(for: $0) },
                                     knownAccounts: { directory().homes(for: $0) },
                                     snapshots: calendarSnapshots,
                                     restored: calendarSnapshots.load())
            // Cursor's account usage feed covers the editor, cursor-agent,
            // cloud agents and Grok Bot; each account's user-data dir is
            // both the ledger's account dim and swe's --account-dir.
            // An ingest that wrote rows refreshes the same way a scan does.
            cursorFeed = CursorUsageFeed(
                ledger: ledger, swe: client,
                accounts: {
                    var accounts = [CursorUsageFeed.Account(
                        home: home.appending(path: "Library/Application Support/Cursor").path)]
                    for profile in (try? store.load()) ?? []
                    where profile.vendor == .cursor && !profile.isDefault {
                        if let dir = store.paths(for: profile).desktopData {
                            accounts.append(
                                CursorUsageFeed.Account(home: dir.path, accountDir: dir.path))
                        }
                    }
                    return accounts
                },
                progressFile: quiver.appending(path: "cache/cursor-feed.json"),
                onIngested: { [overview] in await overview?.reloadAfterLedgerWrite() })
            codexUsage = CodexAccountUsage(
                cacheFile: quiver.appending(path: "cache/codex-account-usage.json"),
                fetch: { home in try await client.codexUsageDaily(accountDir: home) },
                macDays: { home, range in
                    await ledger.dailyTokens(harness: "codex", accountHome: home,
                                             in: range, calendar: .current)
                })
            let overviewModel = overview
            pricingModel = PricingModel(
                ledger: ledger,
                overrides: PricingOverrides(url: quiver.appending(path: "config/pricing.json")),
                makeCatalog: { try pricing.load() },
                apply: { catalog in await overviewModel?.pricingUpdated(catalog) })
        } catch {
            overview = nil
            sessions = nil
            calendar = nil
            cursorFeed = nil
            codexUsage = nil
            pricingModel = nil
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
                                           work: pluginWork)
        updates = UpdatesModel(
            build: BuildInfo(infoDictionary: Bundle.main.infoDictionary),
            preferences: UpdatePreferences(),
            releaseCheck: ReleaseCheck(
                transport: URLSessionTransport(),
                cacheURL: quiver.appending(path: "cache/latest-release.json")),
            swe: {
                let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
                return SweClient(runner: ProcessRunner(
                    executable: SweLocation.executable(override: path)))
            })
        serviceStatus = ServiceStatusModel(service: ServiceStatusService(
            transport: URLSessionTransport(), cacheURL: quiver.appending(path: "cache/service-status.json")),
            store: CustomStatusServices())
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
        compute = ComputeModel(sampler: ProcessSampler(), matchers: { [apps] in
            // The swe chosen in Settings right now, like ProviderVisibility.
            let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
            let swe = SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
            let home = FileManager.default.homeDirectoryForCurrentUser.path
            return try await swe.harnesses(withUsage: false)
                .filter { $0.state == .starred }
                .map { harness in
                    var commands = Set(harness.aliases)
                    commands.insert((harness.command as NSString).lastPathComponent)
                    return HarnessMatcher(harness: harness.name,
                                          appPath: apps.installedApp(for: harness.name),
                                          commands: commands,
                                          homeFolder: home + "/." + harness.name,
                                          primaryColor: harness.primaryColor)
                }
        }, managedPIDs: { await Self.launchdManagedPIDs() }, defaults: .standard)
        terminalChoice = { [unowned self] in self.terminal }
        compute.startTrends()
    }

    /// Pids launchd manages, from `launchctl list`: user agents look like
    /// orphans (ppid 1, a script runner) but are services, not leftovers.
    /// Anything going wrong answers with an empty set.
    private nonisolated static func launchdManagedPIDs() async -> Set<Int32> {
        await runOffPool { Self.launchdManagedPIDsSync() }
    }

    /// Synchronous so it can block on the child, off the caller's actor.
    /// Five seconds, then the process is let go and the list is empty.
    private nonisolated static func launchdManagedPIDsSync() -> Set<Int32> {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = ["list"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do { try process.run() } catch { return [] }
        // Read to EOF while it runs, so a long list can't fill the pipe and
        // stall it; a hung launchctl is stopped after five seconds, which
        // ends the read.
        let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 5, execute: deadline)
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        deadline.cancel()
        guard process.terminationStatus == 0 else { return [] }
        // Columns are tab-separated: PID (or "-"), status, label.
        return Set(String(decoding: data, as: UTF8.self).split(separator: "\n")
            .compactMap { Int32($0.split(separator: "\t").first ?? "") }
            .filter { $0 > 1 })
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
        ]
        if let cursorFeed { watchers.append(Task { await cursorFeed.keepFresh() }) }
        if let codexUsage {
            watchers.append(Task { await codexUsage.keepFresh(homes: codexHomes) })
        }
        watchers += watchCatalogPlacement()
        watchers.append(watchSessions())
        watchers += watchLimits()
        watchers.append(watchActivation())

        // The refresh window feeds the account reads and swe's cache TTL.
        accounts.freshFor = limits.refreshFreshSeconds
        others.cacheTTL = limits.refreshFreshSeconds
    }

    /// Skills and Plugins > Explore place one inventory against one
    /// catalog, so each skill shows on exactly one of them: each model is
    /// handed the other's latest.
    private func watchCatalogPlacement() -> [Task<Void, Never>] {
        [
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
        ]
    }

    private func watchSessions() -> Task<Void, Never> {
        Task { [weak self] in
            guard let sessions = self?.sessions else { return }
            await sessions.restore()
            await sessions.loadIfStale(for: self?.swePath ?? "")
        }
    }

    /// Limits change on a fetch; whether a window has reset changes with
    /// the clock, so look again every minute as well.
    private func watchLimits() -> [Task<Void, Never>] {
        [
            Task { [weak self] in
                while !Task.isCancelled {
                    await self?.others.load()
                    try? await Task.sleep(for: .seconds(300))
                }
            },
            Task { [weak self] in
                let changes = Observations { [weak self] in
                    self.map { ($0.starredAccountCards, $0.others.rows) }
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
                // The refresh window changing re-times both readers.
                let changes = Observations { [weak self] in
                    self?.limits.refreshFreshMinutes ?? 0
                }
                for await _ in changes {
                    guard let self else { continue }
                    accounts.freshFor = limits.refreshFreshSeconds
                    others.cacheTTL = limits.refreshFreshSeconds
                }
            },
            Task { [weak self] in
                let changes = Observations { [weak self] in
                    // The series order and colours also feed the file:
                    // a provider reorder or a newly loaded primary colour
                    // must rewrite it like a reading change does.
                    self.map { ($0.overview?.activity, $0.overview?.recentModels,
                               $0.limits.readings, $0.providers.rows, $0.compute.matchers) }
                }
                for await _ in changes {
                    guard let self else { continue }
                    let style = widgetHarnessStyle
                    limits.writeWidgets(overview: overview, selection: widgetSelection,
                                        harnessOrder: style.order, harnessColors: style.colors)
                }
            },
        ]
    }

    /// Notifications may be switched on or off in System Settings while
    /// Quiver is in the background, and a harness installed from a
    /// terminal; the provider check runs again, quietly.
    private func watchActivation() -> Task<Void, Never> {
        Task { [weak self] in
            await self?.limits.checkNotificationPermission()
            for await _ in NotificationCenter.default.notifications(named: NSApplication.didBecomeActiveNotification) {
                await self?.limits.checkNotificationPermission()
                if let self { await self.providers.recheck(for: self.swePath) }
            }
        }
    }

    private func refreshLimits() {
        limits.update(LimitReading.all(cards: starredAccountCards, others: others.rows, now: .now,
                                     sweFreshFor: others.acceptedCacheTTL))
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

    static func mcpServersFolder(path: String) -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let trimmed = path.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return home.appending(path: ".quiver/mcp/servers") }
        return URL(fileURLWithPath: (trimmed as NSString).expandingTildeInPath)
    }

    private static func keysModel(path: String) -> APIKeysModel {
        // One backup of the previous version, private to you, under ~/.quiver.
        let backups = FileManager.default.homeDirectoryForCurrentUser.appending(path: ".quiver/backups/api-keys")
        return APIKeysModel(store: APIKeysStore(url: keysFile(path: path), backupDirectory: backups),
                            shell: LoginShellEnvironment(),
                            checkCache: APIKeyCheckStore(url: FileManager.default.homeDirectoryForCurrentUser
                                .appending(path: ".quiver/cache/api-key-checks.json")))
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
    func sessions(days: Int?, limit: Int) async throws -> SessionList {
        let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
        return try await SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
            .sessions(days: days, limit: limit)
    }
}
