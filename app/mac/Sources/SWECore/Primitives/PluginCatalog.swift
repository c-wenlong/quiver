import Foundation
import Observation

// MARK: - The catalog

/// `swe plugins catalog --json`: every plugin in every marketplace a harness
/// has registered or `~/.swe/plugins` holds, installed or not, with each
/// harness's state.
public struct PluginCatalog: SnapshotValue {
    /// 2: version 1 was a catalog bundled with the skills, never released.
    public static let snapshotVersion = 2

    /// The harnesses swe read, in its order.
    public let harnesses: [String]
    /// The harnesses swe can install, uninstall, enable and disable in.
    public let actions: [String]
    public let marketplaces: [PluginMarketplace]
    /// What swe could not read, in its words.
    public let errors: [Problem]

    public struct Problem: Codable, Sendable, Equatable {
        public let harness: String
        public let marketplace: String?
        public let detail: String

        public init(harness: String, marketplace: String? = nil, detail: String) {
            self.harness = harness
            self.marketplace = marketplace
            self.detail = detail
        }
    }

    public init(harnesses: [String], actions: [String], marketplaces: [PluginMarketplace],
                errors: [Problem] = []) {
        self.harnesses = harnesses
        self.actions = actions
        self.marketplaces = marketplaces
        self.errors = errors
    }

    enum CodingKeys: String, CodingKey { case harnesses, actions, marketplaces, errors }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        harnesses = try c.decodeIfPresent([String].self, forKey: .harnesses) ?? []
        actions = try c.decodeIfPresent([String].self, forKey: .actions) ?? []
        // Required: without it, any JSON object would read as an empty catalog.
        marketplaces = try c.decode([PluginMarketplace].self, forKey: .marketplaces)
        errors = (try? c.decodeIfPresent([Problem].self, forKey: .errors)) ?? []
    }

    /// Every plugin, marketplace by marketplace.
    public var plugins: [CatalogPlugin] { marketplaces.flatMap(\.plugins) }

    /// What can be done to `plugin` in `harness` now, in the order a menu
    /// lists them. Empty for a harness swe cannot act in (Cursor), and for a
    /// state swe does not know.
    public func actions(for plugin: CatalogPlugin, in harness: String) -> [PluginAction] {
        guard actions.contains(harness), let state = plugin.harnesses[harness] else { return [] }
        let verbs: [PluginAction.Verb]
        switch state.installed {
        case true?:
            switch state.enabled {
            case true?: verbs = [.disable, .uninstall]
            case false?: verbs = [.enable, .uninstall]
            case nil: verbs = [.uninstall]
            }
        case false?:
            // Only from a marketplace this harness lists: anything else
            // would fail in the harness's own CLI.
            verbs = state.available ? [.install] : []
        case nil:
            verbs = []
        }
        return verbs.map { PluginAction(verb: $0, ref: plugin.ref, harness: harness) }
    }
}

public struct PluginMarketplace: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    /// A marketplace in `~/.swe/plugins`.
    public let hub: Bool
    /// Its folder in the hub; nil elsewhere.
    public let path: String?
    public let description: String?
    /// The harnesses that have it registered, and from where.
    public let harnesses: [String: Registration]
    public let plugins: [CatalogPlugin]

    public struct Registration: Codable, Sendable, Equatable {
        public let sourceType: String?
        public let location: String?
        public let readable: Bool

        public init(sourceType: String? = nil, location: String? = nil, readable: Bool = true) {
            self.sourceType = sourceType
            self.location = location
            self.readable = readable
        }

        enum CodingKeys: String, CodingKey { case sourceType, location, readable }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            sourceType = try? c.decodeIfPresent(String.self, forKey: .sourceType)
            location = try? c.decodeIfPresent(String.self, forKey: .location)
            readable = (try? c.decodeIfPresent(Bool.self, forKey: .readable)) ?? true
        }
    }

    public init(name: String, hub: Bool = false, path: String? = nil, description: String? = nil,
                harnesses: [String: Registration], plugins: [CatalogPlugin]) {
        self.name = name
        self.hub = hub
        self.path = path
        self.description = description
        self.harnesses = harnesses
        self.plugins = plugins
    }

    enum CodingKeys: String, CodingKey { case name, hub, path, description, harnesses, plugins }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        hub = (try? c.decodeIfPresent(Bool.self, forKey: .hub)) ?? false
        path = try? c.decodeIfPresent(String.self, forKey: .path)
        description = try? c.decodeIfPresent(String.self, forKey: .description)
        harnesses = try c.decodeIfPresent([String: Registration].self, forKey: .harnesses) ?? [:]
        plugins = try c.decodeIfPresent([CatalogPlugin].self, forKey: .plugins) ?? []
    }

    /// No harness has it registered and it is not in the hub: all that is
    /// left of it is what was installed from it before.
    public var isRegistered: Bool { hub || !harnesses.isEmpty }
}

public struct CatalogPlugin: Codable, Sendable, Equatable, Identifiable {
    public var id: String { ref }
    public let name: String
    /// `<plugin>@<marketplace>`, what every harness CLI takes.
    public let ref: String
    public let marketplace: String
    public let displayName: String?
    public let description: String?
    public let version: String?
    public let category: String?
    public let tags: [String]
    public let author: String?
    public let homepage: String?
    /// A marketplace lists it; false for one only installed.
    public let listed: Bool
    /// Its folder in the marketplace, when the marketplace is a folder.
    public let sourcePath: String?
    /// How many of each component it ships: `skills`, `agents`,
    /// `commands`, `hooks`, `mcp_servers`.
    public let components: [String: Int]
    public let skills: [CatalogSkill]
    /// Keyed by harness: `claude`, `codex`, `cursor`.
    public let harnesses: [String: PluginHarnessState]
    /// A hub plugin's gaps, as the Health tab reports them; nil elsewhere.
    public let drift: [String: PluginDriftState]?

    public init(name: String, marketplace: String, description: String? = nil, version: String? = nil,
                listed: Bool = true, sourcePath: String? = nil, components: [String: Int] = [:],
                skills: [CatalogSkill] = [], harnesses: [String: PluginHarnessState],
                drift: [String: PluginDriftState]? = nil) {
        self.name = name
        self.ref = "\(name)@\(marketplace)"
        self.marketplace = marketplace
        self.displayName = nil
        self.description = description
        self.version = version
        self.category = nil
        self.tags = []
        self.author = nil
        self.homepage = nil
        self.listed = listed
        self.sourcePath = sourcePath
        self.components = components
        self.skills = skills
        self.harnesses = harnesses
        self.drift = drift
    }

    enum CodingKeys: String, CodingKey {
        case name, ref, marketplace, displayName, description, version, category, tags, author, homepage,
             listed, sourcePath, components, skills, harnesses, drift
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        marketplace = try c.decode(String.self, forKey: .marketplace)
        ref = (try? c.decodeIfPresent(String.self, forKey: .ref)) ?? "\(name)@\(marketplace)"
        // Text from a manifest: a value of another type reads as missing.
        displayName = try? c.decodeIfPresent(String.self, forKey: .displayName)
        description = try? c.decodeIfPresent(String.self, forKey: .description)
        version = try? c.decodeIfPresent(String.self, forKey: .version)
        category = try? c.decodeIfPresent(String.self, forKey: .category)
        tags = (try? c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        author = try? c.decodeIfPresent(String.self, forKey: .author)
        homepage = try? c.decodeIfPresent(String.self, forKey: .homepage)
        listed = (try? c.decodeIfPresent(Bool.self, forKey: .listed)) ?? true
        sourcePath = try? c.decodeIfPresent(String.self, forKey: .sourcePath)
        // swe's snake_case keys come through the decoder's key conversion
        // as camelCase ("mcpServers"); put them back, so a saved snapshot
        // and a fresh reply say the same.
        let raw = (try? c.decodeIfPresent([String: Int].self, forKey: .components)) ?? [:]
        components = Dictionary(raw.map { (Self.snakeCase($0.key), $0.value) }, uniquingKeysWith: +)
        skills = try c.decodeIfPresent([CatalogSkill].self, forKey: .skills) ?? []
        harnesses = try c.decodeIfPresent([String: PluginHarnessState].self, forKey: .harnesses) ?? [:]
        drift = try? c.decodeIfPresent([String: PluginDriftState].self, forKey: .drift)
    }

    static func snakeCase(_ key: String) -> String {
        key.reduce(into: "") { out, ch in
            if ch.isUppercase {
                out += "_" + ch.lowercased()
            } else {
                out.append(ch)
            }
        }
    }

    /// Harness names with something to show, in `order`: listed there,
    /// installed there, or a copy on disk.
    public func shownHarnesses(in order: [String]) -> [String] {
        order.filter { harnesses[$0].map { $0.status != .absent } ?? false }
    }

    /// Every folder the plugin occupies on disk: its source folder, each
    /// harness's loaded copy and every cached copy.
    public var folders: [String] {
        var out: [String] = []
        for folder in [sourcePath] + harnesses.values.flatMap({ [$0.path] + $0.copies.map(Optional.some) }) {
            guard let folder, !folder.isEmpty, !out.contains(folder) else { continue }
            out.append(folder)
        }
        return out
    }

    /// The worst gap the Health tab reports for this plugin, if any, and
    /// the harnesses it is in.
    public var worstDrift: (state: PluginDriftState.Kind, harnesses: [String])? {
        guard let drift else { return nil }
        let gaps = drift.filter { $0.value.state != .installed }
        guard let worst = gaps.values.map(\.state).min(by: { $0.severity < $1.severity }) else { return nil }
        return (worst, gaps.filter { $0.value.state == worst }.keys.sorted())
    }
}

public struct CatalogSkill: Codable, Sendable, Equatable {
    public let name: String
    public let description: String
    public let path: String
    /// The harnesses whose loaded copy holds it; empty for a plugin no
    /// harness has installed.
    public let harnesses: [String]

    public init(name: String, description: String = "", path: String, harnesses: [String] = []) {
        self.name = name
        self.description = description
        self.path = path
        self.harnesses = harnesses
    }

    enum CodingKeys: String, CodingKey { case name, description, path, harnesses }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        description = (try? c.decodeIfPresent(String.self, forKey: .description)) ?? ""
        path = try c.decode(String.self, forKey: .path)
        harnesses = (try? c.decodeIfPresent([String].self, forKey: .harnesses)) ?? []
    }
}

/// One harness's view of one plugin.
public struct PluginHarnessState: Codable, Sendable, Equatable {
    /// A marketplace this harness has registered lists it.
    public let available: Bool
    /// Nil when swe cannot tell (Cursor keeps only a cache).
    public let installed: Bool?
    /// Nil when not installed, or when the harness does not say.
    public let enabled: Bool?
    public let version: String?
    public let scopes: [String]
    /// The copy the harness loads.
    public let path: String?
    /// Every cached copy.
    public let copies: [String]

    public enum Status: Sendable, Equatable {
        case enabled, disabled, installed, available, cached, absent

        public var title: String {
            switch self {
            case .enabled: "Enabled"
            case .disabled: "Disabled"
            case .installed: "Installed"
            case .available: "Available"
            case .cached: "Cached"
            case .absent: "Not listed"
            }
        }
    }

    public init(available: Bool, installed: Bool?, enabled: Bool? = nil, version: String? = nil,
                scopes: [String] = [], path: String? = nil, copies: [String] = []) {
        self.available = available
        self.installed = installed
        self.enabled = enabled
        self.version = version
        self.scopes = scopes
        self.path = path
        self.copies = copies
    }

    enum CodingKeys: String, CodingKey { case available, installed, enabled, version, scopes, path, copies }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = (try? c.decodeIfPresent(Bool.self, forKey: .available)) ?? false
        installed = try? c.decodeIfPresent(Bool.self, forKey: .installed)
        enabled = try? c.decodeIfPresent(Bool.self, forKey: .enabled)
        version = try? c.decodeIfPresent(String.self, forKey: .version)
        scopes = (try? c.decodeIfPresent([String].self, forKey: .scopes)) ?? []
        path = try? c.decodeIfPresent(String.self, forKey: .path)
        copies = (try? c.decodeIfPresent([String].self, forKey: .copies)) ?? []
    }

    public var status: Status {
        switch installed {
        case true?:
            switch enabled {
            case true?: .enabled
            case false?: .disabled
            case nil: .installed
            }
        case false?:
            available ? .available : .absent
        case nil:
            path != nil || !copies.isEmpty ? .cached : available ? .available : .absent
        }
    }
}

/// A hub plugin's state in one harness, as `swe find plugins` judges it.
public struct PluginDriftState: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable {
        case installed
        case unregistered
        case notInstalled = "not-installed"
        /// The source's `.codex-plugin` is a symlink, which Codex skips.
        case linkedManifest = "linked-manifest"
        case stale
        case disabledHere = "disabled-here"
        /// Disabled in every harness that has it.
        case disabled
        /// A state from a newer swe.
        case unknown

        public var title: String {
            switch self {
            case .installed: "Installed"
            case .unregistered: "Marketplace not added"
            case .notInstalled: "Not installed"
            case .linkedManifest: "Linked manifest"
            case .stale: "Out of date"
            case .disabledHere, .disabled: "Disabled"
            case .unknown: "Needs a look"
            }
        }

        /// Lower is worse.
        var severity: Int {
            switch self {
            case .unregistered: 0
            case .notInstalled: 1
            case .stale: 2
            case .linkedManifest: 3
            case .disabledHere: 4
            case .disabled: 5
            case .unknown: 6
            case .installed: 7
            }
        }
    }

    public let state: Kind
    /// The fix as text, nil when there is nothing to fix.
    public let fix: String?
    /// What `swe find plugins --fix=` takes; nil for an edit by hand.
    public let id: String?

    public init(state: Kind, fix: String? = nil, id: String? = nil) {
        self.state = state
        self.fix = fix
        self.id = id
    }

    enum CodingKeys: String, CodingKey { case state, fix, id }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = (try? c.decode(Kind.self, forKey: .state)) ?? .unknown
        fix = try? c.decodeIfPresent(String.self, forKey: .fix)
        id = try? c.decodeIfPresent(String.self, forKey: .id)
    }
}

// MARK: - Actions

/// One install, uninstall, enable or disable of one plugin in one harness.
public struct PluginAction: Sendable, Hashable, Identifiable {
    public enum Verb: String, Sendable, CaseIterable {
        case install, uninstall, enable, disable

        public var title: String {
            switch self {
            case .install: "Install"
            case .uninstall: "Uninstall"
            case .enable: "Enable"
            case .disable: "Disable"
            }
        }

        var past: String {
            switch self {
            case .install: "Installed"
            case .uninstall: "Uninstalled"
            case .enable: "Enabled"
            case .disable: "Disabled"
            }
        }
    }

    public let verb: Verb
    public let ref: String
    public let harness: String

    public init(verb: Verb, ref: String, harness: String) {
        self.verb = verb
        self.ref = ref
        self.harness = harness
    }

    public var id: String { "\(verb.rawValue) \(harness) \(ref)" }

    /// Codex has no enable verb: swe sets the flag in config.toml itself.
    public var isEdit: Bool { harness == "codex" && (verb == .enable || verb == .disable) }

    /// What swe will run, or the edit it will make, as swe words it
    /// (src/swe/plugins/actions.py).
    public var command: String {
        switch (harness, verb) {
        case ("codex", .install): "codex plugin add \(ref)"
        case ("codex", .uninstall): "codex plugin remove \(ref)"
        case ("codex", .enable), ("codex", .disable):
            "set [plugins.\"\(ref)\"] enabled = \(verb == .enable) in ~/.codex/config.toml"
        default: "\(harness) plugin \(verb.rawValue) \(ref)"
        }
    }

    /// "Install in Claude Code", for a menu.
    public var menuTitle: String {
        let name = HarnessMark(harness).name
        return verb == .uninstall ? "Uninstall from \(name)" : "\(verb.title) in \(name)"
    }

    /// "Install tool@kit in Claude Code?", for the confirmation.
    public var question: String {
        let name = HarnessMark(harness).name
        return "\(verb.title) \(ref) \(verb == .uninstall ? "from" : "in") \(name)?"
    }

    var arguments: [String] { ["plugins", verb.rawValue, ref, "--harness=" + harness, "--json"] }
}

/// `swe plugins <action> --json`: what happened.
public struct PluginActionResult: Decodable, Sendable, Equatable {
    public let action: String
    public let harness: String
    public let ref: String
    public let changed: Bool
    /// Why nothing was done, when nothing was.
    public let detail: String?

    enum CodingKeys: String, CodingKey { case action, harness, ref, changed, detail }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        action = try c.decode(String.self, forKey: .action)
        harness = try c.decode(String.self, forKey: .harness)
        ref = try c.decode(String.self, forKey: .ref)
        changed = try c.decode(Bool.self, forKey: .changed)
        detail = try? c.decodeIfPresent(String.self, forKey: .detail)
    }

    /// One line for the page: "Installed tool@kit in Claude Code".
    public var summary: String {
        let name = HarnessMark(harness).name
        guard changed, let verb = PluginAction.Verb(rawValue: action) else {
            return "\(ref) in \(name): \(detail ?? "nothing changed")"
        }
        return "\(verb.past) \(ref) \(verb == .uninstall ? "from" : "in") \(name)"
    }
}

extension SweClient {
    public func pluginCatalog() async throws -> PluginCatalog {
        try await call(["plugins", "catalog", "--json"])
    }

    /// swe reads the harness's state first and does nothing when the plugin
    /// is already where it was asked to be; the app never sends a command.
    public func run(_ action: PluginAction) async throws -> PluginActionResult {
        try await call(action.arguments)
    }

    /// `swe plugins marketplace add`: exits 1 when any harness failed yet
    /// still prints the document, so allow the failure and decode anyway.
    public func addMarketplace(source: String, harnesses: [String]) async throws -> MarketplaceAddReply {
        try await call(["plugins", "marketplace", "add", source,
                        "--harness=\(harnesses.joined(separator: ","))", "--json"],
                       allowFailure: true)
    }
}

/// `swe plugins marketplace add --json`: one result per harness asked.
public struct MarketplaceAddReply: Codable, Sendable, Equatable {
    public let source: String
    public let results: [Result]

    public struct Result: Codable, Sendable, Equatable {
        public let harness: String
        public let ok: Bool
        public let detail: String?
    }
}

// MARK: - Where each skill is shown

/// One skill in a plugin's row on the Explore tab.
public struct PluginSkill: Sendable, Equatable, Identifiable {
    /// Unique within one plugin.
    public var id: String { name }
    public let name: String
    public let description: String
    /// Every folder holding it that the app knows of: the catalog's, and
    /// each installed copy `swe skills` found.
    public let paths: [String]

    public init(name: String, description: String, paths: [String]) {
        self.name = name
        self.description = description
        self.paths = paths
    }
}

/// Where each skill on the Mac is shown: on the Skills page, or inside the
/// one plugin that ships it, on the Plugins page's Explore tab. Both pages
/// use this, so a skill is never shown twice and never dropped.
///
/// A skill moves only when its source is `plugin` and exactly one catalog
/// plugin owns its folder: the deepest plugin folder (source, loaded copy or
/// cached copy) holding it, as `swe` itself decides in
/// `quiver.plugins.catalog.owners`. Anything else stays on the Skills page.
///
/// Both pages must place the same inventory against the same catalog, so the
/// app keeps one of each (`SkillsModel`, `PluginCatalogModel`) and hands each
/// model the other's.
public struct SkillPlacement: Sendable, Equatable {
    /// What the Skills page lists, in inventory order.
    public let standalone: [Skill]
    /// Plugin ref to the skills found inside its folders.
    public let byPlugin: [String: [Skill]]

    /// - Parameters:
    ///   - catalog: nil while it is unknown; then every skill stays on the
    ///     Skills page.
    ///   - resolve: a path with its symlinks resolved, for a folder the
    ///     catalog and `swe skills` name differently.
    public init(skills: [Skill], catalog: PluginCatalog?,
                resolve: @escaping (String) -> String = FolderOwners.realPath) {
        guard let catalog else {
            standalone = skills
            byPlugin = [:]
            shownElsewhere = []
            return
        }
        let owners = FolderOwners(catalog, resolve: resolve)
        var standalone: [Skill] = []
        var byPlugin: [String: [Skill]] = [:]
        // Where each skill on the Mac is shown, by its folder as given and
        // resolved: the plugin it moved to, or "" for the Skills page.
        var shownAt: [String: String] = [:]
        for skill in skills {
            let owner = skill.source == .plugin ? owners.owner(of: skill.path) : nil
            if let owner {
                byPlugin[owner, default: []].append(skill)
            } else {
                standalone.append(skill)
            }
            for key in [FolderOwners.trimmed(skill.path), FolderOwners.trimmed(resolve(skill.path))] {
                shownAt[key] = owner ?? ""
            }
        }
        // A catalog entry for a skill on the Mac that is shown somewhere else
        // (on Skills, when two plugins share its folder, or in another
        // plugin) is left out of this plugin's row. An entry for a skill
        // not on the Mac stays: it previews what an install would bring.
        var shownElsewhere: Set<String> = []
        for plugin in catalog.plugins {
            for skill in plugin.skills {
                let raw = FolderOwners.trimmed(skill.path)
                guard let at = shownAt[raw] ?? shownAt[FolderOwners.trimmed(resolve(skill.path))],
                      at != plugin.ref else { continue }
                shownElsewhere.insert(Self.key(plugin.ref, raw))
            }
        }
        self.standalone = standalone
        self.byPlugin = byPlugin
        self.shownElsewhere = shownElsewhere
    }

    /// Catalog entries left out of their plugin's row, as `key(ref, path)`.
    private let shownElsewhere: Set<String>

    private static func key(_ ref: String, _ path: String) -> String { ref + "\u{0}" + path }

    /// How many skills moved to the Plugins page.
    public var inPlugins: Int { byPlugin.values.reduce(0) { $0 + $1.count } }

    /// The skills `plugin` ships, for its row: the catalog's list, less any
    /// skill shown elsewhere, plus every installed copy `swe skills` found
    /// inside it, one entry per name (a skill and its nested copy share a
    /// name). Sorted by name.
    public func skills(of plugin: CatalogPlugin) -> [PluginSkill] {
        var order: [String] = []
        var entries: [String: (description: String, paths: [String])] = [:]
        func add(_ name: String, _ description: String, _ path: String) {
            if var entry = entries[name] {
                if !entry.paths.contains(path) { entry.paths.append(path) }
                if entry.description.isEmpty { entry.description = description }
                entries[name] = entry
            } else {
                order.append(name)
                entries[name] = (description, [path])
            }
        }
        for skill in plugin.skills where !shownElsewhere.contains(Self.key(plugin.ref, FolderOwners.trimmed(skill.path))) {
            add(skill.name, skill.description, skill.path)
        }
        for skill in byPlugin[plugin.ref] ?? [] { add(skill.name, skill.description, skill.path) }
        return order
            .sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
            .map { PluginSkill(name: $0, description: entries[$0]!.description, paths: entries[$0]!.paths) }
    }
}

/// Which catalog plugin a folder belongs to.
public final class FolderOwners {
    private let folders: [(folder: String, ref: String)]
    private let resolve: (String) -> String
    /// The folders with symlinks resolved, worked out only if a path needs them.
    private lazy var resolvedFolders: [(folder: String, ref: String)] = folders.map { (Self.trimmed(resolve($0.folder)), $0.ref) }

    init(_ catalog: PluginCatalog, resolve: @escaping (String) -> String) {
        folders = catalog.plugins.flatMap { plugin in
            plugin.folders.map { (Self.trimmed($0), plugin.ref) }
        }
        self.resolve = resolve
    }

    /// The one plugin owning the deepest folder holding `path`; nil when
    /// none does, or when two plugins share that folder. Paths are compared
    /// as given first, which settles nearly every skill without touching
    /// the disk, then with symlinks resolved on both sides, as swe does.
    func owner(of path: String) -> String? {
        switch Self.owner(of: Self.trimmed(path), in: folders) {
        case .one(let ref): return ref
        case .shared: return nil
        case .none: break
        }
        if case .one(let ref) = Self.owner(of: Self.trimmed(resolve(path)), in: resolvedFolders) { return ref }
        return nil
    }

    private enum Found { case none, one(String), shared }

    private static func owner(of path: String, in folders: [(folder: String, ref: String)]) -> Found {
        var best = -1
        var refs: Set<String> = []
        for (folder, ref) in folders where path == folder || path.hasPrefix(folder + "/") {
            if folder.count > best {
                best = folder.count
                refs = [ref]
            } else if folder.count == best {
                refs.insert(ref)
            }
        }
        if refs.isEmpty { return .none }
        return refs.count == 1 ? .one(refs.first!) : .shared
    }

    static func trimmed(_ path: String) -> String {
        var path = path
        while path.count > 1 && path.hasSuffix("/") { path.removeLast() }
        return path
    }

    /// `path` with every symlink resolved, like Python's `os.path.realpath`;
    /// as given when it does not exist.
    public static func realPath(_ path: String) -> String {
        guard let resolved = realpath(path, nil) else { return path }
        defer { free(resolved) }
        return String(cString: resolved)
    }
}

/// What a page knows of the plugin catalog.
public enum CatalogLookup: Sendable, Equatable {
    /// Not read yet.
    case pending
    case ready(PluginCatalog)
    /// swe could not read it; the reason.
    case unavailable(String)

    public var catalog: PluginCatalog? {
        if case .ready(let catalog) = self { return catalog }
        return nil
    }
}

/// One plugin change at a time across the Plugins page. A Health fix and an
/// Explore action both run harness CLIs on the same plugins, and a fix that
/// removes and adds a plugin again could undo an uninstall made meanwhile.
@MainActor
@Observable
public final class PluginWork {
    /// A fix or an action is running, reload included.
    public private(set) var isBusy = false

    public init() {}

    /// Takes the turn; false when something else has it.
    func begin() -> Bool {
        guard !isBusy else { return false }
        isBusy = true
        return true
    }

    func end() { isBusy = false }
}

/// The Plugins page's Explore tab: the catalog, kept app-wide so the last
/// one shows at once, and the actions run on it.
@MainActor
@Observable
public final class PluginCatalogModel {
    public var state: Loadable<PluginCatalog> { loader.state }
    /// The rows, worked out from the catalog on show and `skills`. Built
    /// off the main actor, since it resolves every plugin and skill folder
    /// on disk; nil until the first one is ready, and the last one stays
    /// on show while the next is built.
    public private(set) var index: PluginExploreIndex?
    /// Bumped whenever the catalog on show or `lookup` changes, for the
    /// skills model to watch cheaply.
    public private(set) var revision = 0
    /// The action running now.
    public private(set) var running: PluginAction?
    public private(set) var actionError: String?
    /// The last action that finished, in one line.
    public private(set) var lastResult: String?

    /// The Skills page's inventory, so each plugin row also lists the
    /// installed copies found inside it. The app hands over the one
    /// `SkillsModel` holds: both pages must place the same list, or a skill
    /// could leave one page without reaching the other.
    public var skills: [Skill] = [] {
        didSet { if skills != oldValue { reindex() } }
    }

    // The filters, kept with the catalog so a return visit finds them set.
    /// A harness name, or nil for every harness.
    public var harness: String?
    public var query = "" {
        didSet {
            if PluginExploreIndex.needle(query) != PluginExploreIndex.needle(oldValue) { closedInSearch = [] }
        }
    }

    /// The marketplaces the user opened this session; they start closed,
    /// and nothing writes the set, so a quit forgets it.
    public private(set) var expanded: Set<String> = []
    /// Marketplaces closed during the current search; forgotten when the
    /// search changes, so the user's own set carries on untouched.
    private var closedInSearch: Set<String> = []

    /// Held while an action runs, shared with the Health tab's fixes.
    public let work: PluginWork
    private let loader: SnapshotLoader<PluginCatalog>
    /// The catalog `revision` last counted.
    private var counted: (catalog: PluginCatalog?, lookup: CatalogLookupKind) = (nil, .pending)
    /// Builds `index` off the main actor, newest inputs only.
    private let indexBuild = LatestBuild<PluginExploreIndex>()
    /// What the index on show, or the one being built, is worked out from.
    private var indexedFrom: (catalog: PluginCatalog, skills: [Skill])?
    /// Whose `~/.swe/plugins` makes a marketplace editable.
    private let home: URL

    /// - Parameters:
    ///   - snapshots: where the last catalog is kept between launches.
    ///   - work: shared with `PluginsModel`, so an action and a fix never
    ///     run at once.
    ///   - home: whose `~/.swe/plugins` makes a marketplace editable.
    public init(snapshots: (any SnapshotStoring<Snapshot<PluginCatalog>>)? = nil, maxAge: TimeInterval = 60,
                timeout: Duration = .seconds(60), now: @escaping @Sendable () -> Date = Date.init,
                work: PluginWork = PluginWork(),
                home: URL = FileManager.default.homeDirectoryForCurrentUser) {
        loader = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now)
        self.work = work
        self.home = home
    }

    /// Returns once the index for the latest catalog and `skills` is on
    /// show. `load`, `restore` and `run` wait for it themselves; this is
    /// for a caller that changed `skills`.
    public func indexed() async {
        await indexBuild.settled()
    }

    /// A search is on: every marketplace it matches shows open.
    public var isSearching: Bool { !PluginExploreIndex.needle(query).isEmpty }

    /// Whether the marketplace `id`'s plugins show now. A search opens
    /// everything it matched without touching the user's own set.
    public func isOpen(_ id: String) -> Bool {
        isSearching ? !closedInSearch.contains(id) : expanded.contains(id)
    }

    /// Opens or closes a marketplace. Kept for the session only; during a
    /// search the change lasts until the search changes.
    public func toggle(_ id: String) {
        if isSearching {
            if closedInSearch.remove(id) == nil { closedInSearch.insert(id) }
        } else if expanded.remove(id) == nil {
            expanded.insert(id)
        }
    }

    /// What the Skills page places skills with.
    public var lookup: CatalogLookup {
        switch loader.state {
        case .idle, .loading: .pending
        case .loaded(let catalog): .ready(catalog)
        case .failed(let message): .unavailable(message)
        }
    }

    /// Shows the last saved catalog until swe answers.
    public func restore() async {
        await loader.restore()
        reindex()
        await indexed()
    }

    /// Reads again unless this swe's catalog is fresh; joins a read in flight.
    public func loadIfStale(swe: SweClient, for source: String) async {
        await loader.loadIfStale(from: source) { try await swe.pluginCatalog() }
        reindex()
        await indexed()
    }

    /// Always reads. `source` is the swe asked, as set in Settings; nil keeps the last one.
    public func load(swe: SweClient, for source: String? = nil) async {
        await loader.load(from: source) { try await swe.pluginCatalog() }
        reindex()
        await indexed()
    }

    /// Runs `action`, then reads the catalog again. Call only after the user
    /// has confirmed the command, and only with an action the catalog on
    /// show offers. The skills it ships change too; the caller lists those
    /// again through `SkillsModel`.
    /// - Parameter source: the swe `swe` is, as set in Settings. The reload
    ///   is dropped if another swe was picked meanwhile; nil reloads anyway.
    public func run(_ action: PluginAction, swe: SweClient, for source: String? = nil) async {
        guard running == nil, let catalog = index?.catalog,
              let plugin = catalog.plugins.first(where: { $0.ref == action.ref }),
              catalog.actions(for: plugin, in: action.harness).contains(action),
              work.begin()
        else { return }
        running = action
        actionError = nil
        lastResult = nil
        defer {
            running = nil
            work.end()
        }
        do {
            lastResult = try await swe.run(action).summary
        } catch is CancellationError {
            // Fall through to the reload: the harness may have changed anyway.
        } catch {
            actionError = error.localizedDescription
        }
        // Reload either way: a failed command may still have changed something.
        if let source {
            await loader.reload(from: source) { try await swe.pluginCatalog() }
        } else {
            await loader.load(from: nil) { try await swe.pluginCatalog() }
        }
        reindex()
        // The chip's spinner stays until the rows show the change.
        await indexed()
    }

    /// Starts a new index when the catalog or `skills` changed. Never on
    /// the main actor: `realpath` and `fileExists` on a folder a file
    /// provider or a stale mount holds can block for as long as they like,
    /// which beachballed the app on a fresh Mac (#580).
    private func reindex() {
        let catalog: PluginCatalog? = if case .loaded(let catalog) = loader.state { catalog } else { nil }
        if let catalog, indexedFrom?.catalog != catalog || indexedFrom?.skills != skills {
            indexedFrom = (catalog, skills)
            let skills = skills
            let home = home
            indexBuild.start({ PluginExploreIndex(catalog: catalog, skills: skills, home: home) }) { [weak self] in
                self?.index = $0
            }
        }
        let kind = CatalogLookupKind(lookup)
        if kind != counted.lookup || (catalog != nil && catalog != counted.catalog) {
            counted = (catalog ?? counted.catalog, kind)
            revision += 1
        }
    }
}

/// `CatalogLookup` without its catalog, cheap to compare.
private enum CatalogLookupKind: Equatable {
    case pending, ready, unavailable(String)

    init(_ lookup: CatalogLookup) {
        switch lookup {
        case .pending: self = .pending
        case .ready: self = .ready
        case .unavailable(let message): self = .unavailable(message)
        }
    }
}
