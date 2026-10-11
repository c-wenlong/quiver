import Foundation
import Observation

/// `swe find plugins --json`: where the plugins in `~/.quiver/plugins` have
/// not reached Claude Code or Codex.
public struct PluginDriftReport: SnapshotValue {
    public static let snapshotVersion = 1

    /// Plugins under `~/.quiver/plugins`; 0 means there is nothing to reach.
    public let sources: Int
    public let drift: [PluginDrift]

    public init(sources: Int, drift: [PluginDrift]) {
        self.sources = sources
        self.drift = drift
    }
}

public struct PluginDrift: Codable, Sendable, Equatable, Identifiable {
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
    public var state: Loadable<PluginDriftReport> { loader.state }
    /// The finding whose fix is running.
    public private(set) var fixing: String?
    public private(set) var fixError: String?
    /// The last fix that ran, for a one-line confirmation.
    public private(set) var lastFixed: String?
    private let loader: SnapshotLoader<PluginDriftReport>

    /// - Parameter snapshots: where the last report is kept between launches.
    public init(snapshots: (any SnapshotStoring<Snapshot<PluginDriftReport>>)? = nil, maxAge: TimeInterval = 60,
                timeout: Duration = .seconds(60), now: @escaping @Sendable () -> Date = Date.init) {
        loader = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now)
    }

    /// Shows the last saved report until swe answers.
    public func restore() async {
        await loader.restore()
    }

    /// Checks again unless this swe's report is fresh; joins a check in flight.
    public func loadIfStale(swe: SweClient, for source: String) async {
        await loader.loadIfStale(from: source) { try await swe.pluginDrift() }
    }

    /// Always checks. `source` is the swe asked, as set in Settings; nil keeps the last one.
    public func load(swe: SweClient, for source: String? = nil) async {
        await loader.load(from: source) { try await swe.pluginDrift() }
    }

    /// Runs the fix for `finding`, then reloads. Call only after the user
    /// has confirmed the command.
    /// - Parameter source: the swe `swe` is, as set in Settings. The reload
    ///   is dropped if another swe was picked meanwhile; nil reloads anyway.
    public func fix(_ finding: PluginDrift, swe: SweClient, for source: String? = nil) async {
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
        if let source {
            await loader.reload(from: source) { try await swe.pluginDrift() }
        } else {
            await load(swe: swe)
        }
    }
}
