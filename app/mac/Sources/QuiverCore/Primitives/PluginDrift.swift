import Foundation
import Observation

/// `swe find plugins --json`: where the plugins in `~/.swe/plugins` have
/// not reached Claude Code or Codex.
public struct PluginDriftReport: SnapshotValue {
    public static let snapshotVersion = 1

    /// Plugins under `~/.swe/plugins`; 0 means there is nothing to reach.
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
    public var state: Loadable<PluginDriftReport> { loader.state }
    /// The finding whose fix is running.
    public private(set) var fixing: String?
    public private(set) var fixError: String?
    /// The last fix that ran, for a one-line confirmation.
    public private(set) var lastFixed: String?
    /// While Fix all runs: how many fixes have run, out of how many it began with.
    public private(set) var fixAllProgress: FixAllProgress?
    private let loader: SnapshotLoader<PluginDriftReport>

    public struct FixAllProgress: Equatable, Sendable {
        public var done: Int
        public var total: Int
    }

    /// The findings Fix all would run now: the ones swe can fix itself, in
    /// report order. The confirmation lists exactly these.
    public var fixAllIDs: [String] {
        guard case .loaded(let report) = state else { return [] }
        return report.drift.filter(\.runnable).map(\.id)
    }

    public var fixAllCount: Int { fixAllIDs.count }

    /// Held while a fix runs, shared with the Explore tab's actions.
    public let work: PluginWork

    /// - Parameters:
    ///   - snapshots: where the last report is kept between launches.
    ///   - work: shared with `PluginCatalogModel`, so a fix and an Explore
    ///     action never run at once.
    public init(snapshots: (any SnapshotStoring<Snapshot<PluginDriftReport>>)? = nil, maxAge: TimeInterval = 60,
                timeout: Duration = .seconds(60), now: @escaping @Sendable () -> Date = Date.init,
                work: PluginWork = PluginWork()) {
        loader = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now)
        self.work = work
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
        guard fixing == nil, finding.runnable, work.begin() else { return }
        fixing = finding.id
        fixError = nil
        lastFixed = nil
        defer {
            fixing = nil
            work.end()
        }
        do {
            try await swe.fixPluginDrift(id: finding.id)
            lastFixed = finding.fix
        } catch {
            fixError = error.localizedDescription
        }
        await reload(swe: swe, for: source)
    }

    /// After a fix: the reload is dropped if another swe was picked
    /// meanwhile; with no source it reloads anyway.
    /// - Returns: whether the report on show is this swe's fresh one.
    @discardableResult
    private func reload(swe: SweClient, for source: String?) async -> Bool {
        if let source {
            return await loader.reload(from: source) { try await swe.pluginDrift() }
        }
        await load(swe: swe)
        return true
    }

    /// Runs the confirmed fixes, one at a time, reloading between them.
    /// The next fix is taken from the fresh report, so a finding an earlier
    /// fix already cleared is skipped rather than failing. Only `confirmed`
    /// ids run: a finding a fix newly exposes (registering a marketplace
    /// reveals its uninstalled plugins) waits for its own confirmation.
    /// Stops at the first failure. Edits to make by hand are never sent.
    /// - Parameter source: as for `fix`.
    public func fixAll(confirmed: [String], swe: SweClient, for source: String? = nil) async {
        let confirmed = Set(confirmed)
        guard fixing == nil, !confirmed.isEmpty, work.begin() else { return }
        fixError = nil
        lastFixed = nil
        fixAllProgress = FixAllProgress(done: 0, total: confirmed.count)
        defer {
            fixing = nil
            fixAllProgress = nil
            work.end()
        }
        var tried: Set<String> = []
        var ran = 0
        while case .loaded(let report) = state,
              let next = report.drift.first(where: {
                  $0.runnable && confirmed.contains($0.id) && !tried.contains($0.id)
              }) {
            tried.insert(next.id)
            fixing = next.id
            do {
                try await swe.fixPluginDrift(id: next.id)
                ran += 1
                fixAllProgress?.done = ran
            } catch is CancellationError {
                return
            } catch {
                fixError = error.localizedDescription
                await reload(swe: swe, for: source)
                return
            }
            // The next pick must come from this swe's fresh report. If
            // another swe was picked in Settings, the report on show no
            // longer says what this swe still has to fix.
            guard await reload(swe: swe, for: source) else {
                fixError = "Stopped: another swe was picked in Settings."
                break
            }
        }
        if ran > 0 { lastFixed = ran == 1 ? "1 fix" : "\(ran) fixes" }
    }
}
