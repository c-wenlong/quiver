import Foundation
import Observation

/// Something SWE can show usage for: one of swe's harnesses, or one of
/// the extra harnesses TokenTracker lists (Antigravity, Kimi, Qoder…).
/// Harnesses only: a model plan like Ark Coding Plan is a subscription on a
/// model provider, not something that runs sessions.
public struct Provider: Identifiable, Sendable, Equatable {
    public var id: String
    /// Its name and logo come from the harness tables (`HarnessMark`), so
    /// Settings › Providers names a harness the way every other page does.
    public var name: String { HarnessMark.accountName(id) }
    /// The `HarnessIcons` id for its logo.
    public var icon: String { HarnessMark(id).icon ?? id }
    /// Install signs for providers swe doesn't list: commands on PATH, or
    /// paths that exist (`~` is the user's home). Only existence is checked;
    /// nothing inside them is read.
    public var commands: [String] = []
    public var paths: [String] = []
}

public enum ProviderCatalog {
    /// TokenTracker's order first, then the rest of swe's harnesses.
    public static let all: [Provider] = [
        Provider(id: "claude"),
        Provider(id: "codex"),
        Provider(id: "devin"),
        Provider(id: "cursor"),
        Provider(id: "antigravity",
                 commands: ["agy"],
                 paths: ["/Applications/Antigravity.app", "~/Applications/Antigravity.app",
                         "~/.gemini/antigravity", "~/.gemini/antigravity-ide"]),
        Provider(id: "copilot"),
        Provider(id: "kimi", commands: ["kimi"], paths: ["~/.kimi"]),
        Provider(id: "kiro"),
        Provider(id: "grok", commands: ["grok"]),
        Provider(id: "zcode", paths: ["~/.zcode"]),
        Provider(id: "opencode"),
        Provider(id: "commandcode",
                 commands: ["command-code"], paths: ["~/.commandcode"]),
        Provider(id: "qoder",
                 commands: ["qodercli", "qoder"],
                 paths: ["/Applications/Qoder.app", "~/Applications/Qoder.app", "~/.qoder"]),
        Provider(id: "qoder-cn",
                 commands: ["qoderclicn"],
                 paths: ["/Applications/Qoder.app", "~/Applications/Qoder.app", "~/.qoder"]),
        Provider(id: "gemini"),
        Provider(id: "qwen-code"),
        Provider(id: "mistral-vibe"),
        Provider(id: "crush"),
        Provider(id: "cline"),
        Provider(id: "kilo"),
        Provider(id: "goose"),
        Provider(id: "aider"),
        Provider(id: "continue"),
        Provider(id: "pi"),
        Provider(id: "forge"),
        Provider(id: "droid"),
        Provider(id: "augment"),
        Provider(id: "blackbox"),
        Provider(id: "freebuff"),
    ]

    public static func provider(_ id: String) -> Provider? { all.first { $0.id == id } }
}

public enum ProviderDetection {
    /// Ids of the providers on this Mac, in catalog order: swe's harnesses as
    /// `swe list` reports them, plus any provider whose own signs are present.
    public static func installed(
        sweHarnesses: Set<String>, commandExists: (String) -> Bool, pathExists: (String) -> Bool,
        home: URL = FileManager.default.homeDirectoryForCurrentUser
    ) -> [String] {
        func expand(_ path: String) -> String {
            path.hasPrefix("~/") ? home.appendingPathComponent(String(path.dropFirst(2))).path : path
        }
        return ProviderCatalog.all.filter { provider in
            sweHarnesses.contains(provider.id)
                || provider.commands.contains(where: commandExists)
                || provider.paths.contains { pathExists(expand($0)) }
        }.map(\.id)
    }

    /// Names of the harnesses `swe list` says are installed. Older swe lists
    /// only installed ones and has no flag, so a missing flag counts as yes.
    public static func installedHarnesses(_ harnesses: [Harness]) -> Set<String> {
        Set(harnesses.filter { $0.installed ?? true }.map(\.name))
    }

    /// Whether `command` is an executable in one of `path`'s directories.
    public static func commandExists(_ command: String, path: String) -> Bool {
        path.split(separator: ":").contains { directory in
            FileManager.default.isExecutableFile(atPath: "\(directory)/\(command)")
        }
    }
}

/// Where the choices are kept; UserDefaults in the app.
public protocol KeyValueStoring: Sendable {
    func strings(forKey key: String) -> [String]?
    func set(_ strings: [String], forKey key: String)
}

/// Settings' provider visibility, after TokenTracker: every provider in a
/// draggable order, a switch for each one that is installed, and Enable all
/// / Disable all. A provider not installed is shown greyed out and can't be
/// switched on. What is kept is the order and the providers switched off,
/// so one installed later turns on by itself.
@MainActor
@Observable
public final class ProviderVisibilityModel {
    public struct Row: Identifiable, Equatable, Sendable {
        public var id: String { provider.id }
        public var provider: Provider
        public var isInstalled: Bool
        public var isEnabled: Bool
    }

    public private(set) var rows: [Row] = []
    public private(set) var isLoading = false
    /// False until the first check finishes; until then nothing is known
    /// to be missing.
    public private(set) var hasChecked = false
    /// Why the last check failed. What was known before it still stands.
    public private(set) var checkError: String?

    static let orderKey = "providerOrder"
    static let disabledKey = "providerDisabled"

    private let store: KeyValueStoring
    private let detect: @Sendable () async throws -> Set<String>
    private var installed: Set<String> = []
    private var order: [String] = []
    private var disabled: Set<String> = []
    /// Bumped by each check; only the newest one writes its result.
    private var request = 0
    /// What the checks run against (the swe path), and the key whose
    /// successful check is what the rows show now. Nil once a later check
    /// fails, since the rows then mix that error with older results.
    private var key: String?
    private var checkedKey: String?
    /// When the last check finished, pass or fail; `recheck` uses it to
    /// skip checks that would run back to back.
    private var lastCheckedAt: Date?
    private let now: () -> Date

    public init(store: KeyValueStoring, detect: @escaping @Sendable () async throws -> Set<String>,
                now: @escaping () -> Date = Date.init) {
        self.store = store
        self.detect = detect
        self.now = now
        order = Self.completeOrder(store.strings(forKey: Self.orderKey) ?? [])
        disabled = Set(store.strings(forKey: Self.disabledKey) ?? [])
        rebuild()
    }

    /// Checks once per `key` (the swe path in use), so a view that is
    /// rebuilt or visited again doesn't run swe again. It skips only when
    /// the rows already show a finished, successful check of this key.
    /// A check that failed or was cancelled hasn't counted, so the next
    /// call runs it again. A check still running is replaced rather than
    /// waited on: it may be for another swe, and the view that started it
    /// may be gone with its task cancelled.
    public func loadIfNeeded(for key: String) async {
        // Always the current key, so "Try again" and any check started
        // from here are saved under the swe they actually ran against.
        self.key = key
        guard key != checkedKey || isLoading else { return }
        await load()
    }

    /// Checks again, for a harness installed while SWE was in the
    /// background (call it when the app becomes active). `key` is the swe
    /// path in use now, which may have changed since the last check; the
    /// result is saved under it. Quiet when the rows already show this
    /// key: they keep their result while it runs, and if it fails they
    /// stay as they were with no error, since nobody asked for it. Does
    /// nothing before the first check, or while one is running. A quiet
    /// recheck for the same swe under `recheckInterval` after the last
    /// finished check is skipped: app activation fires this on every
    /// focus, and a detection that fresh says nothing new.
    public func recheck(for key: String) async {
        guard self.key != nil, !isLoading else { return }
        let same = key == checkedKey
        // The last check was against `self.key`; a repeat of it, pass or
        // fail, is throttled. A different swe still checks right away.
        if key == self.key, let lastCheckedAt,
           now().timeIntervalSince(lastCheckedAt) < Self.recheckInterval { return }
        self.key = key
        await load(quiet: same)
    }

    /// How fresh a finished check has to be for `recheck` to skip running swe.
    static let recheckInterval: TimeInterval = 120

    public func load() async {
        await load(quiet: false)
    }

    private func load(quiet: Bool) async {
        request += 1
        let mine = request
        let key = self.key
        isLoading = true
        let result: Result<Set<String>, Error>
        do { result = .success(try await detect()) } catch { result = .failure(error) }
        // A check replaced while it ran (another swe was chosen) is dropped.
        guard mine == request else { return }
        isLoading = false
        // A cancelled check (Settings closed mid-run) says nothing, so it
        // neither counts against the recheck interval nor changes state.
        if case .failure(let error) = result, error is CancellationError { return }
        // Every finished check, good or bad, counts against the recheck
        // interval: a swe that keeps failing shouldn't be spawned on each
        // activation either.
        lastCheckedAt = now()
        switch result {
        case .success(let found):
            installed = found
            hasChecked = true
            checkedKey = key
            checkError = nil
        case .failure where quiet && hasChecked:
            return
        case .failure(let error):
            // A check that couldn't run says nothing about what is installed.
            checkError = error.localizedDescription
            checkedKey = nil
        }
        rebuild()
    }

    public var enabledCount: Int { rows.filter(\.isEnabled).count }

    /// Installed and not switched off.
    public func isVisible(_ id: String) -> Bool { installed.contains(id) && !disabled.contains(id) }

    /// Switched off by the user. Usage views hide only these: a harness
    /// uninstalled since keeps showing the usage it already has.
    public func isHidden(_ id: String) -> Bool { disabled.contains(id) }

    /// `ids` in the user's order; ids the catalog doesn't know keep their
    /// place after the known ones.
    public func ordered(_ ids: [String]) -> [String] {
        let rank = Dictionary(uniqueKeysWithValues: order.enumerated().map { ($1, $0) })
        return ids.enumerated().sorted { a, b in
            (rank[a.element] ?? order.count + a.offset, a.offset) < (rank[b.element] ?? order.count + b.offset, b.offset)
        }.map(\.element)
    }

    public func setEnabled(_ id: String, _ enabled: Bool) {
        guard installed.contains(id) else { return }
        if enabled { disabled.remove(id) } else { disabled.insert(id) }
        save()
    }

    public func enableAll() {
        disabled.subtract(installed)
        save()
    }

    public func disableAll() {
        disabled.formUnion(installed)
        save()
    }

    /// Moves `id` to sit just before `target`.
    public func move(_ id: String, before target: String) {
        guard id != target, let from = order.firstIndex(of: id) else { return }
        order.remove(at: from)
        order.insert(id, at: order.firstIndex(of: target) ?? order.endIndex)
        save()
    }

    /// Moves `id` to the bottom of the list.
    public func moveToEnd(_ id: String) {
        guard let from = order.firstIndex(of: id) else { return }
        order.remove(at: from)
        order.append(id)
        save()
    }

    private func save() {
        store.set(order, forKey: Self.orderKey)
        store.set(disabled.sorted(), forKey: Self.disabledKey)
        rebuild()
    }

    private func rebuild() {
        rows = order.compactMap { id in
            ProviderCatalog.provider(id).map {
                Row(provider: $0, isInstalled: installed.contains(id), isEnabled: isVisible(id))
            }
        }
    }

    /// The saved order, minus ids the catalog dropped, plus new ones at the end.
    private static func completeOrder(_ saved: [String]) -> [String] {
        let known = Set(ProviderCatalog.all.map(\.id))
        var seen = Set<String>()
        let kept = saved.filter { known.contains($0) && seen.insert($0).inserted }
        return kept + ProviderCatalog.all.map(\.id).filter { !seen.contains($0) }
    }
}
