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

    /// The package manager's name on the badge.
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
/// `registered` (the harness is already in harness.json) and `description`
/// arrived after the first shape; an older swe leaves them out and the sheet
/// degrades instead of failing.
public struct InstallableHarness: Decodable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let command: String
    public let installed: Bool
    public let registered: Bool
    public let description: String
    public let path: String?
    public let methods: [InstallMethod]

    public init(name: String, command: String, installed: Bool,
                registered: Bool = false, description: String = "",
                path: String? = nil, methods: [InstallMethod] = []) {
        self.name = name
        self.command = command
        self.installed = installed
        self.registered = registered
        self.description = description
        self.path = path
        self.methods = methods
    }

    private enum CodingKeys: String, CodingKey {
        case name, command, installed, registered, description, path, methods
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        command = try container.decode(String.self, forKey: .command)
        installed = try container.decode(Bool.self, forKey: .installed)
        registered = try container.decodeIfPresent(Bool.self, forKey: .registered) ?? false
        description = try container.decodeIfPresent(String.self, forKey: .description) ?? ""
        path = try container.decodeIfPresent(String.self, forKey: .path)
        methods = try container.decodeIfPresent([InstallMethod].self, forKey: .methods) ?? []
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

    /// `swe add <name> <command> [description]`: registers a harness that is
    /// already installed. `swe add` has no --json, so this runs plain.
    public func addHarness(_ name: String, command: String, description: String = "") async throws {
        var arguments = ["add", name, command]
        if !description.isEmpty { arguments.append(description) }
        _ = try await runPlain(arguments)
    }
}

/// The Add harness sheet's data: swe's installer catalog minus the harnesses
/// already registered, grouped so the installed-but-unregistered ones come
/// first (for those the action is Register, not an install command).
@MainActor
@Observable
public final class InstallCatalogModel {
    public private(set) var state: Loadable<[InstallableHarness]> = .idle
    public var query = ""
    public private(set) var actionError: String?
    /// Names currently being registered, so their buttons can disable.
    public private(set) var registering: Set<String> = []

    private let swe: SweClient
    /// Names the registry already knows, supplied by the page's harness
    /// list. An older swe has no `registered` field, so without this the
    /// sheet would offer Register for harnesses that are already in.
    private let registeredNames: @MainActor () -> Set<String>
    private var generation = 0

    public init(swe: SweClient,
                registeredNames: @escaping @MainActor () -> Set<String> = { [] }) {
        self.swe = swe
        self.registeredNames = registeredNames
    }

    private func isRegistered(_ harness: InstallableHarness) -> Bool {
        harness.registered || registeredNames().contains(harness.name)
    }

    public func load() async {
        generation += 1
        let run = generation
        if case .idle = state { state = .loading }
        do {
            let harnesses = try await swe.installCatalog()
            if run == generation { state = .loaded(harnesses) }
        } catch is CancellationError {
            generation -= 1
        } catch {
            if run == generation { state = .failed(error.localizedDescription) }
        }
    }

    /// The rows to show: registered harnesses never appear, whatever the
    /// query. The query matches the name and the catalog description.
    public var rows: [InstallableHarness] {
        guard case .loaded(let all) = state else { return [] }
        let needle = query.trimmingCharacters(in: .whitespaces).lowercased()
        return all
            .filter { !isRegistered($0) }
            .filter {
                needle.isEmpty
                    || $0.name.lowercased().contains(needle)
                    || $0.description.lowercased().contains(needle)
            }
    }

    /// Installed-but-unregistered first, then the ones swe can't find on PATH.
    public var groups: (installed: [InstallableHarness], missing: [InstallableHarness]) {
        (rows.filter(\.installed), rows.filter { !$0.installed })
    }

    /// True when nothing swe knows about is still unregistered, so the sheet
    /// can tell "all registered" apart from "the query hid everything".
    public var everythingRegistered: Bool {
        guard case .loaded(let all) = state else { return false }
        return !all.contains { !isRegistered($0) }
    }

    /// Report an action that failed outside a swe call (like opening the
    /// terminal), shown by the sheet's ErrorCard.
    public func report(_ error: Error) {
        actionError = error.localizedDescription
    }

    /// `swe add` the harness, then reload so it leaves the list. Returns the
    /// error text for inline display, or nil on success.
    @discardableResult
    public func register(_ harness: InstallableHarness) async -> String? {
        registering.insert(harness.name)
        defer { registering.remove(harness.name) }
        do {
            try await swe.addHarness(harness.name, command: harness.command,
                                     description: harness.description)
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

    /// The line Run hands to the terminal. Installs through swe so it also
    /// registers the harness; the terminal is needed because casks and
    /// scripts can prompt for a password.
    public static func installLine(_ name: String, via: String, swe: String) -> String {
        // openTerminal wraps the whole line as one -c argument, so only a
        // path with spaces or quotes needs quoting inside it.
        let executable = swe.range(of: "[\\s']", options: .regularExpression) != nil
            ? LaunchPlanner.shellQuote(swe)
            : swe
        return "\(executable) install \(name) --via=\(via)"
    }
}
