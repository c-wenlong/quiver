import Foundation
import Observation
import QuiverCore

/// App-wide state shared by the window and the menu bar panel.
@MainActor
@Observable
final class AppModel {
    static let shared = AppModel()

    private(set) var swe: SweClient
    var selection: AppSection? = .overview

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
    /// Nil when the ledger database could not be opened; `overviewError` says why.
    let overview: OverviewModel?
    let overviewError: String?

    init() {
        let saved = UserDefaults.standard.string(forKey: SweLocation.defaultsKey) ?? ""
        swePath = saved
        let client = Self.client(for: saved)
        swe = client
        others = OtherLimitsModel(swe: client)
        terminal = UserDefaults.standard.string(forKey: "terminalApp")
            .flatMap(TerminalApp.init(rawValue:)) ?? .terminal

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
        } catch {
            overview = nil
            overviewError = "Couldn't open the usage ledger: \(error.localizedDescription)"
        }
        terminalChoice = { [unowned self] in self.terminal }
    }

    private var refreshing: Task<Void, Never>?

    private var watchers: [Task<Void, Never>] = []

    /// Keep the ledger scanned and every limit fetched while the app runs.
    /// One set of loops serves the window, the menu bar and the widgets.
    func start() {
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
            Task { await limits.checkNotificationPermission() },
        ]
    }

    private func refreshLimits() {
        limits.update(LimitReading.all(cards: accounts.cards, others: others.rows, now: .now))
    }

    private static func client(for path: String) -> SweClient {
        SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
    }
}
