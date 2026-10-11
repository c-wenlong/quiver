import Foundation
import Observation

/// `swe find plugins --json`: where the plugins in `~/.quiver/plugins` have
/// not reached Claude Code or Codex.
public struct PluginDriftReport: Decodable, Sendable, Equatable {
    /// Plugins under `~/.quiver/plugins`; 0 means there is nothing to reach.
    public let sources: Int
    public let drift: [PluginDrift]

    public init(sources: Int, drift: [PluginDrift]) {
        self.sources = sources
        self.drift = drift
    }
}

public struct PluginDrift: Decodable, Sendable, Equatable, Identifiable {
    /// `<harness>:<kind>:<ref>`, which `swe find plugins --fix=` takes.
    public let id: String
    public let harness: String
    public let marketplace: String
    public let plugin: String?
    public let ref: String
    public let kind: String
    public let detail: String
    /// The fix as text: commands, or an edit when there is no command.
    public let fix: String
    /// True when swe can run the fix; false when it is an edit to make by hand.
    public let runnable: Bool
    /// Stale only: the files that differ, relative to the plugin.
    public let paths: [String]

    public init(id: String, harness: String, marketplace: String, plugin: String?, ref: String,
                kind: String, detail: String, fix: String, runnable: Bool, paths: [String] = []) {
        self.id = id
        self.harness = harness
        self.marketplace = marketplace
        self.plugin = plugin
        self.ref = ref
        self.kind = kind
        self.detail = detail
        self.fix = fix
        self.runnable = runnable
        self.paths = paths
    }

    public var kindTitle: String {
        switch kind {
        case "unregistered": "Marketplace not added"
        case "not-installed": "Not installed"
        case "linked-manifest": "Linked manifest"
        case "stale": "Out of date"
        case "disabled-here": "Disabled here"
        default: kind.prefix(1).uppercased() + kind.dropFirst()
        }
    }

    /// Unregistered means nothing from the marketplace reaches the harness.
    public var isSevere: Bool { kind == "unregistered" }
}

extension SweClient {
    public func pluginDrift() async throws -> PluginDriftReport {
        try await call(["find", "plugins", "--json"])
    }

    /// Asks swe to run one finding's fix. swe checks the finding still
    /// applies and builds the commands itself; the app never sends a command.
    public func fixPluginDrift(id: String) async throws {
        let _: FixedDrift = try await call(["find", "plugins", "--fix=" + id, "--json"])
    }
}

struct FixedDrift: Decodable { let fixed: PluginDrift }

@MainActor
@Observable
public final class PluginsModel {
    public private(set) var state: Loadable<PluginDriftReport> = .idle
    /// The finding whose fix is running.
    public private(set) var fixing: String?
    public private(set) var fixError: String?
    /// The last fix that ran, for a one-line confirmation.
    public private(set) var lastFixed: String?
    private var generation = 0

    public init() {}

    public func load(swe: SweClient) async {
        generation += 1
        let mine = generation
        if case .loaded = state {} else { state = .loading }
        let next: Loadable<PluginDriftReport>
        do {
            next = .loaded(try await swe.pluginDrift())
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == generation { state = next }
    }

    /// Runs the fix for `finding`, then reloads. Call only after the user
    /// has confirmed the command.
    public func fix(_ finding: PluginDrift, swe: SweClient) async {
        guard fixing == nil, finding.runnable else { return }
        fixing = finding.id
        fixError = nil
        lastFixed = nil
        defer { fixing = nil }
        do {
            try await swe.fixPluginDrift(id: finding.id)
            lastFixed = finding.fix
        } catch {
            fixError = error.localizedDescription
        }
        await load(swe: swe)
    }
}
