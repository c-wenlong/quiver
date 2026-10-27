import Foundation

/// Where a harness or account can be launched — its desktop app, or its
/// CLI in a terminal — resolved once and shared by the Accounts page's
/// buttons and the menu bar's logo affordance.
@MainActor
public struct LaunchTarget {
    public enum Kind: Sendable {
        /// A signed-in account: the app runs with its folders, the
        /// terminal as it.
        case account(Profile)
        /// A starred harness with no account: bare app open, bare command.
        case harness(name: String, command: String)
    }

    /// What a click did, for callers that close the panel on a launch.
    public enum Action: Sendable {
        case app
        case terminal
        case none
    }

    public let kind: Kind
    /// The desktop app's path when found in /Applications or ~/Applications.
    public let appPath: String?
    /// Whether the CLI is on PATH; nil while no check has answered, which
    /// still offers the terminal rather than hide it.
    public let cliInstalled: Bool?
    private let model: AccountsModel

    /// `installedApp` answers a harness id's app path, `cliInstalled` its
    /// PATH check: `AppLocator.installedApp` plus swe's report with the
    /// model's own `cliOnPath` as fallback, decided by the caller.
    public init(kind: Kind, model: AccountsModel,
                installedApp: (String) -> String?,
                cliInstalled: (String) -> Bool?) {
        let harness: String = switch kind {
        case .account(let profile): profile.vendor.rawValue
        case .harness(let name, _): name
        }
        self.kind = kind
        self.model = model
        appPath = installedApp(harness)
        self.cliInstalled = cliInstalled(harness)
    }

    /// The harness id, whatever the kind wraps.
    public var harness: String {
        switch kind {
        case .account(let profile): profile.vendor.rawValue
        case .harness(let name, _): name
        }
    }

    /// Its display name for help text and labels.
    public var title: String { UsageFormat.harness(harness) }

    /// Whether any launch is possible: the app, or a CLI that isn't known
    /// to be missing.
    public var canLaunch: Bool { appPath != nil || cliInstalled != false }

    /// The desktop app found at `path`, as this account where there is one.
    public func openApp(at path: String) async throws {
        switch kind {
        case .account(let profile): try await model.openApp(profile, at: path)
        case .harness: try await model.openApp(at: path)
        }
    }

    /// The CLI in a terminal, as this account where there is one.
    public func openTerminal() async throws {
        switch kind {
        case .account(let profile): try await model.openTerminal(profile)
        case .harness(_, let command): try await model.openTerminal(command: command)
        }
    }

    /// The app when there is one, else the terminal, else nothing.
    @discardableResult
    public func openPreferred() async throws -> Action {
        if let appPath {
            try await openApp(at: appPath)
            return .app
        }
        guard cliInstalled != false else { return .none }
        try await openTerminal()
        return .terminal
    }
}
