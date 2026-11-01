import Foundation
import Observation

/// One install route swe knows for a harness (`swe install --json`).
public struct InstallMethod: Decodable, Sendable, Equatable {
    public let via: String
    public let package: String
    public let command: String
    public let available: Bool

    public init(via: String, package: String, command: String, available: Bool) {
        self.via = via
        self.package = package
        self.command = command
        self.available = available
    }

    /// The package manager's logo (a `HarnessIcons` id) in place of its
    /// name; nil for a vendor script, which has none.
    public var logo: String? {
        switch via {
        case "brew", "cask": "homebrew"
        case "npm": "npm"
        case "uv": "uv"
        case "nix": "nix"
        default: nil
        }
    }

    /// The package manager's name, for its logo's tooltip and label.
    public var managerTitle: String {
        switch via {
        case "brew": "Homebrew"
        case "cask": "Homebrew cask"
        case "npm": "npm"
        case "uv": "uv"
        case "nix": "Nix"
        case "script": "Script"
        default: via
        }
    }
}

/// One row of `swe install --json`: a harness swe can name an installer for.
/// `registered` (the harness is already in harness.json), `description`,
/// `uninstall`, `app` and `website` arrived after the first shape; an older
/// swe leaves them out and the sheet degrades instead of failing.
/// `uninstall` is how to remove it, read off where its binary resolves to,
/// and nil when it isn't installed or a vendor script put it there.
public struct InstallableHarness: Decodable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let command: String
    public let installed: Bool
    public let registered: Bool
    public let description: String
    /// The install evidence: the binary on PATH, or for a GUI-only
    /// provider its `.app` bundle or Homebrew Caskroom record.
    public let path: String?
    /// The provider's `.app` bundle when one is on disk. Informational
    /// for a provider with a CLI — the IDE existing does not mean the
    /// command is installed — and the install evidence for a GUI-only one.
    public let app: String?
    /// The vendor's download page, for providers swe has audited. Lets the
    /// sheet link out instead of dead-ending when no install method exists,
    /// and offers the GUI download when the methods only install a CLI.
    public let website: String?
    public let methods: [InstallMethod]
    public let uninstall: InstallMethod?

    public init(name: String, command: String, installed: Bool,
                registered: Bool = false, description: String = "",
                path: String? = nil, app: String? = nil, website: String? = nil,
                methods: [InstallMethod] = [],
                uninstall: InstallMethod? = nil) {
        self.name = name
        self.command = command
        self.installed = installed
        self.registered = registered
        self.description = description
        self.path = path
        self.app = app
        self.website = website
        self.methods = methods
        self.uninstall = uninstall
    }

    private enum CodingKeys: String, CodingKey {
        case name, command, installed, registered, description, path, app, website, methods, uninstall
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        command = try container.decode(String.self, forKey: .command)
        installed = try container.decode(Bool.self, forKey: .installed)
        registered = try container.decodeIfPresent(Bool.self, forKey: .registered) ?? false
        description = try container.decodeIfPresent(String.self, forKey: .description) ?? ""
        path = try container.decodeIfPresent(String.self, forKey: .path)
        app = try container.decodeIfPresent(String.self, forKey: .app)
        website = try container.decodeIfPresent(String.self, forKey: .website)
        methods = try container.decodeIfPresent([InstallMethod].self, forKey: .methods) ?? []
        uninstall = try container.decodeIfPresent(InstallMethod.self, forKey: .uninstall)
    }
}

private struct InstallList: Decodable {
    let harnesses: [InstallableHarness]
}

extension SweClient {
    /// Every harness swe knows an installer for. Read-only: it never runs an
    /// install and never creates harness.json.
    public func installCatalog() async throws -> [InstallableHarness] {
        let data: InstallList = try await call(["install", "--json"])
        return data.harnesses
    }
}

/// The provider sheet's data: swe's installer catalog, looked up one
/// harness at a time. Settings › Providers opens the sheet from a row: an
/// installed harness gets its uninstall command (and Register when swe
/// doesn't know it yet), a missing one its install commands.
@MainActor
@Observable
public final class InstallCatalogModel {
    public private(set) var state: Loadable<[InstallableHarness]> = .idle
    public private(set) var actionError: String?
    /// Names currently being registered, so their buttons can disable.
    public private(set) var registering: Set<String> = []

    private let swe: SweClient
    private var generation = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        generation += 1
        let run = generation
        if case .idle = state { state = .loading }
        do {
            let harnesses = try await swe.installCatalog()
            if run == generation { state = .loaded(harnesses) }
        } catch is CancellationError {
            // Do not roll the generation back: a second load already in
            // flight keeps its claim, and its result still publishes.
            // Rewinding would make its `run == generation` check fail and
            // leave the sheet at .loading forever.
        } catch {
            if run == generation { state = .failed(error.localizedDescription) }
        }
    }

    /// The catalog row for one harness, nil until loaded or when swe has
    /// no installer on file for it.
    public func harness(_ name: String) -> InstallableHarness? {
        guard case .loaded(let all) = state else { return nil }
        return all.first { $0.name == name }
    }

    /// Report an action that failed outside a swe call (like opening the
    /// terminal), shown by the sheet's ErrorCard.
    public func report(_ error: Error) {
        actionError = error.localizedDescription
    }

    /// Clears the sheet's error line; a new action starts clean.
    public func clearError() {
        actionError = nil
    }

    /// `swe add` the harness, then reload so the sheet sees it registered.
    /// The write takes the same gate as the Harnesses page's edits so two
    /// registry writes never touch harness.json at once. Returns the error
    /// text for inline display, or nil on success.
    @discardableResult
    public func register(_ harness: InstallableHarness) async -> String? {
        actionError = nil
        registering.insert(harness.name)
        defer { registering.remove(harness.name) }
        await HarnessesModel.registryWrites.acquire()
        defer { HarnessesModel.registryWrites.release() }
        do {
            try await swe.addHarness(name: harness.name, command: harness.command,
                                     description: harness.description, aliases: [], tags: [])
        } catch is CancellationError {
            return nil
        } catch {
            let message = error.localizedDescription
            actionError = message
            return message
        }
        await load()
        return nil
    }

    /// The line Run hands to `runInTerminal(line:)`, typed into a login
    /// shell as-is. Installs through swe so it also registers the harness;
    /// the terminal is needed because casks and scripts can prompt for a
    /// password. Every token is quoted on its own: quoting the whole line
    /// would make it one command name.
    public static func installLine(_ name: String, via: String, swe: String) -> String {
        [swe, "install", name, "--via=\(via)"]
            .map(LaunchPlanner.shellQuote)
            .joined(separator: " ")
    }

    /// The line Run hands to `runInTerminal(line:)` for an uninstall, or nil
    /// for a method swe never reports for one. Built from the package rather
    /// than run from `command`, so every token is quoted on its own.
    public static func uninstallLine(_ method: InstallMethod) -> String? {
        let argv: [String]
        switch method.via {
        case "brew": argv = ["brew", "uninstall", method.package]
        case "cask": argv = ["brew", "uninstall", "--cask", method.package]
        case "npm": argv = ["npm", "uninstall", "-g", method.package]
        case "uv": argv = ["uv", "tool", "uninstall", method.package]
        case "nix": argv = ["nix", "profile", "remove", method.package]
        default: return nil
        }
        return argv.map(LaunchPlanner.shellQuote).joined(separator: " ")
    }
}
