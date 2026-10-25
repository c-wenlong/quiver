import Foundation
import Observation

/// `swe hooks list --json`: every hook script under `~/.quiver/hooks`, with
/// the destination each harness would link it to and the declarations found
/// in its settings files. The `findings` ride along from `swe hooks check
/// --json`, which the same fetch runs: the check exits 1 while findings
/// remain, so its result is decoded under `allowFailure`, and a check that
/// cannot be read at all (an swe too old to know `hooks check`, say)
/// degrades to `checkError` rather than hiding the list.
public struct HookInventory: SnapshotValue {
    public static let snapshotVersion = 1

    public let hooks: [Hook]
    /// Declaration/link drift from `swe hooks check`; empty when clean or
    /// unreadable.
    public let findings: [HookFinding]
    /// Why the check's findings are missing, nil when the check ran.
    public let checkError: String?

    public init(hooks: [Hook], findings: [HookFinding] = [], checkError: String? = nil) {
        self.hooks = hooks
        self.findings = findings
        self.checkError = checkError
    }

    enum CodingKeys: String, CodingKey { case hooks, findings, checkError }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // One bad row degrades instead of failing the list; a newer swe's
        // extra keys decode through `Hook`'s optional fields.
        hooks = try c.decode([LossyRow<Hook>].self, forKey: .hooks).compactMap(\.value)
        findings = try c.decodeIfPresent([HookFinding].self, forKey: .findings) ?? []
        checkError = try c.decodeIfPresent(String.self, forKey: .checkError)
    }
}

/// One hook script: its file under `~/.quiver/hooks/<harness>` (`source`),
/// where `swe init` would link it (`destination`), and the declarations the
/// harness's settings files hold for it. `state` is `swe init`'s link
/// vocabulary (linked, create, relink, absorb, keep, conflict, skipped,
/// ignored), so an older app still reads states a newer swe adds.
public struct Hook: Codable, Sendable, Equatable, Identifiable {
    /// Where the script belongs, when a newer swe says so. `shared` is the
    /// hub's common pool, `harness` belongs to one harness, and `foreign`
    /// is a script quiver lists but does not manage.
    public enum Tier: String, Codable, Sendable, CaseIterable {
        case shared, harness, foreign

        public var title: String {
            switch self {
            case .shared: "Shared"
            case .harness: "Harness"
            case .foreign: "Foreign"
            }
        }
    }

    public var id: String { source }
    /// The file name, `prose-guard.py`.
    public let script: String
    /// The registry name the hooks folder is for.
    public let harness: String
    /// The script in the hub, under `~/.quiver/hooks/<harness>`.
    public let source: String
    /// Where the harness reads it, linked by `swe init`.
    public let destination: String
    public let state: LinkState
    /// swe's note on the state, empty when there is nothing to add.
    public let detail: String
    /// The settings declarations that name this script.
    public let declared: [HookDeclaration]
    /// Optional fields a newer swe sends; nil on the current one.
    public let tier: Tier?
    public let enabled: Bool?
    /// The events the script says it handles, when swe reports them.
    public let eventsSupported: [String]?

    public init(script: String, harness: String, source: String, destination: String,
                state: LinkState, detail: String = "", declared: [HookDeclaration] = [],
                tier: Tier? = nil, enabled: Bool? = nil, eventsSupported: [String]? = nil) {
        self.script = script
        self.harness = harness
        self.source = source
        self.destination = destination
        self.state = state
        self.detail = detail
        self.declared = declared
        self.tier = tier
        self.enabled = enabled
        self.eventsSupported = eventsSupported
    }

    /// Linked into a harness but declared by no settings file, so it will
    /// not fire — what `swe hooks check` reports as `undeclared`.
    public var isUndeclared: Bool {
        state == .linked && declared.isEmpty
    }

    enum CodingKeys: String, CodingKey {
        case script, harness, source, destination, state, detail, declared
        case tier, enabled, eventsSupported
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        script = try c.decode(String.self, forKey: .script)
        harness = try c.decode(String.self, forKey: .harness)
        source = try c.decode(String.self, forKey: .source)
        destination = try c.decodeIfPresent(String.self, forKey: .destination) ?? source
        state = try c.decode(LinkState.self, forKey: .state)
        detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
        declared = try c.decodeIfPresent([HookDeclaration].self, forKey: .declared) ?? []
        // An unlisted tier reads as none: the row just groups by harness.
        tier = try? c.decodeIfPresent(Tier.self, forKey: .tier)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled)
        eventsSupported = try c.decodeIfPresent([String].self, forKey: .eventsSupported)
    }
}

/// One settings-file declaration of a hook: the event it fires on, the
/// matcher it is gated by, and the command line, in the settings file at
/// `settings`. The app never writes these files; the new-hook sheet shows
/// the stanza `swe hooks new` renders for the user to paste.
public struct HookDeclaration: Codable, Sendable, Equatable {
    public let event: String
    /// The matcher's pattern; empty means the event has none.
    public let matcher: String
    /// Seconds the harness gives the script; nil when unset.
    public let timeout: Double?
    public let command: String
    /// The settings file the declaration was read from.
    public let settings: String

    public init(event: String, matcher: String, timeout: Double?, command: String, settings: String) {
        self.event = event
        self.matcher = matcher
        self.timeout = timeout
        self.command = command
        self.settings = settings
    }

    /// `PreToolUse:Bash`, or just `SessionStart` when there is no matcher.
    public var label: String {
        matcher.isEmpty ? event : "\(event):\(matcher)"
    }
}

/// `swe hooks check --json`: the declaration/link drift on this Mac. The
/// command exits 1 while findings remain, so the client reads it under
/// `allowFailure`.
public struct HooksCheckReport: Codable, Sendable, Equatable {
    public let findings: [HookFinding]

    public init(findings: [HookFinding]) {
        self.findings = findings
    }
}

/// One check finding. `kind` is `declared-missing` (a settings file names a
/// script that is nowhere on disk), `undeclared` (a linked script no
/// settings file declares, so it will not fire) or `unlinked` (the script
/// is not linked into the destination; `swe init` fixes it). `harness` is
/// empty for a finding that belongs to no one harness.
public struct HookFinding: Codable, Sendable, Equatable, Identifiable {
    public var id: String { "\(kind):\(harness):\(message)" }
    public let kind: String
    public let harness: String
    public let message: String

    public init(kind: String, harness: String, message: String) {
        self.kind = kind
        self.harness = harness
        self.message = message
    }

    /// `undeclared` and `unlinked` are about the linked side, which
    /// `swe init` or a settings edit puts right.
    public var kindTitle: String {
        switch kind {
        case "declared-missing": "Declared, missing"
        case "undeclared": "Undeclared"
        case "unlinked": "Unlinked"
        default: kind.prefix(1).uppercased() + kind.dropFirst()
        }
    }
}

/// `swe hooks info <name> --json`: every placement of one script.
public struct HookInfo: Decodable, Sendable, Equatable {
    /// The name as asked, without the `.py` a file name carries.
    public let script: String
    public let harnesses: [Placement]

    public init(script: String, harnesses: [Placement]) {
        self.script = script
        self.harnesses = harnesses
    }

    /// The script as one harness sees it: the link state, the docstring
    /// summary swe read off the file, and its declarations.
    public struct Placement: Decodable, Sendable, Equatable {
        public let harness: String
        public let source: String
        public let destination: String
        public let state: LinkState
        public let detail: String
        /// The script's first docstring line; empty when it has none.
        public let summary: String
        public let declared: [HookDeclaration]

        enum CodingKeys: String, CodingKey {
            case harness, source, destination, state, detail, summary, declared
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            harness = try c.decode(String.self, forKey: .harness)
            source = try c.decode(String.self, forKey: .source)
            destination = try c.decodeIfPresent(String.self, forKey: .destination) ?? source
            state = try c.decode(LinkState.self, forKey: .state)
            detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
            summary = try c.decodeIfPresent(String.self, forKey: .summary) ?? ""
            declared = try c.decodeIfPresent([HookDeclaration].self, forKey: .declared) ?? []
        }
    }
}

/// `swe hooks new <harness> <name> --json`: the script it wrote, the
/// command line a declaration should run, the settings files it belongs in
/// and the stanza to paste into one. quiver writes only the script; a
/// settings file is never written by the app or swe.
public struct NewHookReply: Decodable, Sendable, Equatable {
    /// The new script under `~/.quiver/hooks/<harness>`, executable.
    public let path: String
    public let harness: String
    /// The command a declaration runs, `~/.claude/hooks/name.py`-spelled.
    public let command: String
    /// The harness's settings files the stanza could go in.
    public let settings: [String]
    /// The exact hooks JSON block to paste into a settings file.
    public let stanza: String
}

/// Whether an opened hook's script is the user's own, for the detail page.
/// Like `SkillFolders`, worked out off the main actor: `realpath` and
/// `fileExists` on a synced or stale folder can block.
public struct HookScript: Sendable, Equatable {
    /// The script exists on disk.
    public let exists: Bool
    /// The script stays under `~/.quiver/hooks` once symlinks resolve, so
    /// it is the user's to edit. A script that resolves elsewhere (the hub
    /// folder linked away, say) is view only.
    public let editable: Bool

    public init(source: String, home: URL,
                resolve: (String) -> String = FolderOwners.realPath,
                exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }) {
        let hooks = home.appending(path: ".quiver/hooks").path
        self.exists = exists(source)
        editable = self.exists && PrimitiveOwnership.isInside(source, root: hooks, resolve: resolve)
    }
}

extension SweClient {
    /// `swe hooks list --json`: every script under `~/.quiver/hooks` and how
    /// each harness links it.
    public func hooksList() async throws -> HookInventory {
        try await call(["hooks", "list", "--json"])
    }

    /// `swe hooks check --json` exits 1 while findings remain, which is a
    /// report, not a failure: `allowFailure` keeps the payload decodable.
    public func hooksCheck() async throws -> HooksCheckReport {
        try await call(["hooks", "check", "--json"], allowFailure: true)
    }

    /// `swe hooks info <name> --json`: every placement of one script, with
    /// the docstring summary the list leaves out.
    public func hookInfo(name: String) async throws -> HookInfo {
        try await call(["hooks", "info", name, "--json"])
    }

    /// `swe hooks new <harness> <name> --json` writes the script skeleton
    /// and nothing else; the reply carries the stanza the user pastes into
    /// a settings file themself.
    public func newHook(harness: String, name: String) async throws -> NewHookReply {
        try await call(["hooks", "new", harness, name, "--json"])
    }
}

/// The Hooks page's model: the scripts under `~/.quiver/hooks`, their link
/// states and the check's findings, kept between launches so the last list
/// shows at once. Linking goes through `swe init` (the only writer of
/// links); creating goes through `swe hooks new`. A change reloads the list
/// through the swe that made it unless another was picked meanwhile.
@MainActor
@Observable
public final class HooksModel {
    public var state: Loadable<HookInventory> { loader.state }
    /// True while `swe init` is linking.
    public private(set) var linking = false
    /// True while `swe hooks new` is writing a script.
    public private(set) var creating = false
    /// The last link or create failure, in swe's words.
    public var actionError: String?
    /// One mutation at a time: swe rewrites the same links on each.
    private let gate = SerialGate()
    private let loader: SnapshotLoader<HookInventory>

    /// - Parameters:
    ///   - snapshots: where the last list is kept between launches.
    ///   - maxAge: how long a list counts as fresh.
    ///   - timeout: how long swe may take before a list counts as failed.
    public init(snapshots: (any SnapshotStoring<Snapshot<HookInventory>>)? = nil,
                maxAge: TimeInterval = 60, timeout: Duration = .seconds(60),
                now: @escaping @Sendable () -> Date = Date.init) {
        loader = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now)
    }

    /// Shows the last saved list until swe answers.
    public func restore() async {
        await loader.restore()
    }

    /// Lists the hooks unless this swe's list is fresh; joins one in flight.
    /// - Parameter source: the swe asked, as set in Settings.
    public func loadIfStale(swe: SweClient, for source: String) async {
        await loader.loadIfStale(from: source) { try await Self.fetch(swe) }
    }

    /// Always lists. `source` is the swe asked, as set in Settings; nil
    /// keeps the last one.
    public func load(swe: SweClient, for source: String? = nil) async {
        await loader.load(from: source) { try await Self.fetch(swe) }
    }

    /// The list plus the check's findings in one load: the check exits 1
    /// while findings remain, and a check that cannot be read at all — an
    /// swe from before `hooks` existed has already failed the list anyway —
    /// degrades to `checkError`, never hiding the scripts.
    private static func fetch(_ swe: SweClient) async throws -> HookInventory {
        async let listed = swe.hooksList()
        async let checked = swe.hooksCheck()
        let list = try await listed
        do {
            let report = try await checked
            return HookInventory(hooks: list.hooks, findings: report.findings)
        } catch {
            return HookInventory(hooks: list.hooks, findings: [],
                                 checkError: error.localizedDescription)
        }
    }

    /// `swe init`, the only linking the app runs; `force` backs up each
    /// real file in the way to `~/.quiver/backups` first. Reloads either
    /// way: a failed init may still have linked some hooks.
    /// - Parameter source: the swe `swe` is, as set in Settings. The reload
    ///   is dropped if another swe was picked meanwhile; nil reloads anyway.
    public func link(force: Bool = false, swe: SweClient, for source: String? = nil) async {
        guard !linking else { return }
        linking = true
        actionError = nil
        defer { linking = false }
        await gate.acquire()
        defer { gate.release() }
        do {
            try await swe.runInit(force: force)
        } catch is CancellationError {
            return
        } catch {
            actionError = error.localizedDescription
        }
        await reload(swe: swe, for: source)
    }

    /// `swe hooks new <harness> <name>` writes the script skeleton, then the
    /// list reloads so the new row shows. The reply's stanza is the caller's
    /// to show; no settings file is ever written here.
    /// - Parameter source: as for `link`.
    public func create(harness: String, name: String, swe: SweClient,
                       for source: String? = nil) async throws -> NewHookReply {
        await gate.acquire()
        defer { gate.release() }
        creating = true
        actionError = nil
        defer { creating = false }
        let reply = try await swe.newHook(harness: harness, name: name)
        await reload(swe: swe, for: source)
        return reply
    }

    /// After a change made through `swe`: the reload is dropped if another
    /// swe was picked meanwhile; with no source it reloads anyway.
    private func reload(swe: SweClient, for source: String?) async {
        if let source {
            await loader.reload(from: source) { try await Self.fetch(swe) }
        } else {
            await load(swe: swe)
        }
    }

    /// The hooks on show, empty until a list loads.
    public var hooks: [Hook] {
        guard case .loaded(let inventory) = state else { return [] }
        return inventory.hooks
    }

    /// The check's findings, empty until a list loads.
    public var findings: [HookFinding] {
        guard case .loaded(let inventory) = state else { return [] }
        return inventory.findings
    }

    /// Whether the loaded list carries a tier, so the page can group by it.
    public var hasTiers: Bool {
        hooks.contains { $0.tier != nil }
    }

    /// The groups the list shows. A list that knows tiers splits into
    /// "Shared", one group per harness, and "Foreign" for scripts quiver
    /// does not manage; without tiers every row groups by its harness.
    /// Shared comes first and foreign last; harnesses keep the order the
    /// list first names them.
    public var groups: [HookGroup] {
        var order: [String] = []
        var byKey: [String: [Hook]] = [:]
        for hook in hooks {
            let key = groupKey(of: hook)
            if byKey[key] == nil { order.append(key) }
            byKey[key, default: []].append(hook)
        }
        let groups = order.map { HookGroup(key: $0, hooks: byKey[$0] ?? []) }
        guard hasTiers else { return groups }
        return groups.filter { $0.key == "shared" }
            + groups.filter { $0.key != "shared" && $0.key != "foreign" }
            + groups.filter { $0.key == "foreign" }
    }

    private func groupKey(of hook: Hook) -> String {
        guard hasTiers else { return hook.harness }
        switch hook.tier {
        case .shared: return "shared"
        case .foreign: return "foreign"
        default: return hook.harness
        }
    }

    /// One list section: the shared pool, one harness, or the foreign pile.
    public struct HookGroup: Identifiable {
        /// "shared", "foreign", or a harness name.
        public let key: String
        public let hooks: [Hook]
        public var id: String { key }
    }
}
