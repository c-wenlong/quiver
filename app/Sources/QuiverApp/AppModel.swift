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
        }
    }

    /// Which terminal Open in Terminal and Sign in use.
    var terminal: TerminalApp {
        didSet { UserDefaults.standard.set(terminal.rawValue, forKey: "terminalApp") }
    }

    let accounts: AccountsModel
    /// Nil when the ledger database could not be opened; `overviewError` says why.
    let overview: OverviewModel?
    let overviewError: String?
    let serviceStatus: ServiceStatusModel
    /// Nil when the ledger is unavailable, like `overview`.
    let sessions: SessionsModel?
    private let launcher = ProcessLauncher()
    private let userHome = FileManager.default.homeDirectoryForCurrentUser

    init() {
        let saved = UserDefaults.standard.string(forKey: SweLocation.defaultsKey) ?? ""
        swePath = saved
        swe = Self.client(for: saved)
        terminal = UserDefaults.standard.string(forKey: "terminalApp")
            .flatMap(TerminalApp.init(rawValue:)) ?? .terminal

        // Everything Quiver keeps lives under ~/.quiver (see AGENTS.md).
        let home = FileManager.default.homeDirectoryForCurrentUser
        let quiver = home.appending(path: ".quiver")
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
            sessions = SessionsModel(source: CurrentSwe(), ledger: ledger)
        } catch {
            overview = nil
            sessions = nil
            overviewError = "Couldn't open the usage ledger: \(error.localizedDescription)"
        }
        serviceStatus = ServiceStatusModel(service: ServiceStatusService(
            transport: URLSessionTransport(), cacheURL: quiver.appending(path: "cache/service-status.json")))
        terminalChoice = { [unowned self] in self.terminal }
    }

    private var refreshing: Task<Void, Never>?

    /// Keep the ledger scanned while the app runs. One loop serves both the
    /// window and the menu bar.
    func start() {
        if statusPolling == nil {
            statusPolling = Task { [serviceStatus] in await serviceStatus.keepFresh() }
        }
        guard refreshing == nil, let overview else { return }
        refreshing = Task { await overview.keepFresh() }
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

    private static func client(for path: String) -> SweClient {
        SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
    }
}

/// The swe chosen in Settings at the moment of each call.
private struct CurrentSwe: SessionListing {
    func sessions(days: Int, limit: Int) async throws -> SessionList {
        let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
        return try await SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
            .sessions(days: days, limit: limit)
    }
}
