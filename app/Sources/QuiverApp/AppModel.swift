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
    /// Nil when the ledger database could not be opened; `overviewError` says why.
    let overview: OverviewModel?
    let overviewError: String?
    let serviceStatus: ServiceStatusModel
    /// Which providers show, in what order (Settings).
    let providers: ProviderVisibilityModel

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
        } catch {
            overview = nil
            overviewError = "Couldn't open the usage ledger: \(error.localizedDescription)"
        }
        serviceStatus = ServiceStatusModel(service: ServiceStatusService(
            transport: URLSessionTransport(), cacheURL: quiver.appending(path: "cache/service-status.json")))
        providers = ProviderVisibilityModel(store: UserDefaultsStore(), detect: {
            // swe's installed harnesses, plus the extra providers' own signs.
            let path = UserDefaults.standard.string(forKey: SweLocation.defaultsKey)
            let swe = SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
            let harnesses = (try? await swe.harnesses(withUsage: false)) ?? []
            let shellPath = await LoginShell.cachedEnvironment()["PATH"] ?? ""
            return Set(ProviderDetection.installed(
                sweHarnesses: Set(harnesses.map(\.name)),
                commandExists: { ProviderDetection.commandExists($0, path: shellPath) },
                pathExists: { FileManager.default.fileExists(atPath: $0) }))
        })
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

    private static func client(for path: String) -> SweClient {
        SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
    }
}

/// Provider visibility lives in the app's own preferences.
private struct UserDefaultsStore: KeyValueStoring {
    func strings(forKey key: String) -> [String]? { UserDefaults.standard.stringArray(forKey: key) }
    func set(_ strings: [String], forKey key: String) { UserDefaults.standard.set(strings, forKey: key) }
}
