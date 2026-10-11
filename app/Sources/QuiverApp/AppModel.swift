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
        terminalChoice = { [unowned self] in self.terminal }
    }

    private static func client(for path: String) -> SweClient {
        SweClient(runner: ProcessRunner(executable: SweLocation.executable(override: path)))
    }
}
