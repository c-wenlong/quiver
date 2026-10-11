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
        }
    }

    /// Which terminal Open in Terminal and Sign in use.
    var terminal: TerminalApp {
        didSet { UserDefaults.standard.set(terminal.rawValue, forKey: "terminalApp") }
    }

    let accounts: AccountsModel
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

    init() {
        let saved = UserDefaults.standard.string(forKey: SweLocation.defaultsKey) ?? ""
        swePath = saved
        swe = Self.client(for: saved)
        terminal = UserDefaults.standard.string(forKey: "terminalApp")
            .flatMap(TerminalApp.init(rawValue:)) ?? .terminal

        let keysPath = UserDefaults.standard.string(forKey: "apiKeysPath") ?? ""
        apiKeysPath = keysPath
        apiKeys = Self.keysModel(path: keysPath)

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
        } catch {
            overview = nil
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
