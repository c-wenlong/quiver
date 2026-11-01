import Foundation

// MARK: - The Explore tab

/// One plugin as the Explore tab knows it, worked out once per catalog.
public struct ExploreRow: Sendable, Equatable, Identifiable {
    public var id: String { plugin.ref }
    public let plugin: CatalogPlugin
    /// From a marketplace in `~/.quiver/plugins`.
    public let isHub: Bool
    public let skills: [PluginSkill]
    /// Lowercased name, ref, marketplace, description and skill names, for search.
    let haystack: String
}

/// The Explore tab's top level: where a marketplace comes from. Each holds
/// its marketplaces, each marketplace its plugins.
public struct ExploreSource: Sendable, Equatable, Identifiable {
    public enum Kind: Sendable, Hashable {
        /// The hub, `~/.quiver/plugins`, whichever harnesses register it.
        case local
        /// Marketplaces this harness registers, the hub's aside.
        case harness(String)
        /// What was installed from a marketplace the installing harness no
        /// longer registers, or that no harness registers at all.
        case unregistered

        /// Stable across launches: what the open state is kept under.
        public var id: String {
            switch self {
            case .local: "local"
            case .harness(let name): name
            case .unregistered: "unregistered"
            }
        }
    }

    public var id: String { kind.id }
    public let kind: Kind
    public let title: String
    /// One line under the title.
    public let note: String
    public let marketplaces: [ExploreMarketplace]

    /// Plugin rows across its marketplaces.
    public var plugins: Int { marketplaces.reduce(0) { $0 + $1.plugins.count } }

    /// "3 marketplaces · 40 plugins", or "5 matches" while searching.
    public func countLine(searching: Bool) -> String {
        if searching { return Self.count(plugins, "match", "matches") }
        return Self.count(marketplaces.count, "marketplace", "marketplaces") + " · " + Self.count(plugins, "plugin", "plugins")
    }

    static func count(_ n: Int, _ one: String, _ many: String) -> String {
        "\(n) \(n == 1 ? one : many)"
    }

    func with(marketplaces: [ExploreMarketplace]) -> ExploreSource {
        ExploreSource(kind: kind, title: title, note: note, marketplaces: marketplaces)
    }
}

/// One marketplace under one source.
public struct ExploreMarketplace: Sendable, Equatable, Identifiable {
    /// `<source>/<marketplace>`, like `claude/claude-plugins-official`:
    /// stable across launches, and unique when a marketplace sits under
    /// two sources.
    public let id: String
    public let name: String
    /// Where it comes from, as the catalog says (`local` for a folder,
    /// `github`, `git`, `cache`, ...); nil when no harness says.
    public let tag: String?
    public let plugins: [ExplorePlugin]
    /// Plugins installed in the source's harness; for the hub and the
    /// unregistered, in any harness its rows show.
    public let installed: Int

    init(id: String, name: String, tag: String?, plugins: [ExplorePlugin]) {
        self.id = id
        self.name = name
        self.tag = tag
        // The rows are a ForEach keyed by id, and SwiftUI misbehaves on a
        // repeated one: the first of each id stays.
        let plugins = plugins.uniqued(by: \.id)
        self.plugins = plugins
        installed = plugins.filter { entry in
            entry.harnesses.contains { entry.row.plugin.harnesses[$0]?.status.isInstalled ?? false }
        }.count
    }

    /// "316 plugins · 2 installed", or "4 matches" while searching.
    public func countLine(searching: Bool) -> String {
        if searching { return ExploreSource.count(plugins.count, "match", "matches") }
        return ExploreSource.count(plugins.count, "plugin", "plugins") + " · \(installed) installed"
    }

    func with(plugins: [ExplorePlugin]) -> ExploreMarketplace {
        ExploreMarketplace(id: id, name: name, tag: tag, plugins: plugins)
    }

    /// The catalog's source type in a word: a folder is `local`.
    static func tag(_ sourceType: String?) -> String? {
        guard let sourceType, !sourceType.isEmpty else { return nil }
        return sourceType == "directory" ? "local" : sourceType
    }
}

/// One plugin row under one marketplace under one source.
public struct ExplorePlugin: Sendable, Equatable, Identifiable {
    /// `<source>/<ref>`: the same plugin can sit under two sources.
    public var id: String { source + "/" + row.id }
    public let source: String
    public let row: ExploreRow
    /// The harnesses whose chip the row shows, in the catalog's order: the
    /// source's own harness, both of the hub's, or wherever an
    /// unregistered plugin is installed.
    public let harnesses: [String]
}

/// The Explore tab's plugins, worked out once per catalog and inventory so
/// filtering 385 plugins on each keystroke is cheap.
public struct PluginExploreIndex: Sendable, Equatable {
    public let catalog: PluginCatalog
    /// The skills `swe skills` found, the same list the Skills page places.
    public let skills: [Skill]
    public let placement: SkillPlacement
    /// Every plugin once, in the catalog's order.
    public let rows: [ExploreRow]
    /// Every source, marketplace and plugin row, unfiltered.
    public let sources: [ExploreSource]
    /// The marketplaces the user may rename: those whose folder is in
    /// `~/.quiver/plugins` (`PrimitiveOwnership.isEditable`). Worked out
    /// here, off the main actor, since it resolves symlinks on disk.
    public let editableMarketplaces: Set<String>
    /// What swe could not read, with the marketplaces missing on this Mac
    /// set apart.
    public let problems: CatalogProblems

    /// Resolves plugin and skill folders on disk, so build it off the main
    /// actor (`PluginCatalogModel` does).
    /// - Parameter home: the home whose `~/.quiver/plugins` makes a
    ///   marketplace editable; nil leaves every marketplace read only.
    public init(catalog: PluginCatalog, skills: [Skill], home: URL? = nil,
                resolve: @escaping (String) -> String = FolderOwners.realPath) {
        self.catalog = catalog
        self.skills = skills
        let placement = SkillPlacement(skills: skills, catalog: catalog, resolve: resolve)
        self.placement = placement
        let problems = CatalogProblems(catalog)
        self.problems = problems
        editableMarketplaces = home.map { home in
            Set(catalog.marketplaces.filter { PrimitiveOwnership.isEditable($0, home: home, resolve: resolve) }.map(\.name))
        } ?? []
        var rows: [ExploreRow] = []
        var seen: Set<String> = []
        for market in catalog.marketplaces {
            // One row per ref: rows are looked up by it.
            for plugin in market.plugins where seen.insert(plugin.ref).inserted {
                let skills = placement.skills(of: plugin)
                let haystack = ([plugin.name, plugin.ref, plugin.marketplace,
                                 plugin.displayName ?? "", plugin.description ?? ""]
                    + skills.map(\.name)).joined(separator: "\n").lowercased()
                rows.append(ExploreRow(plugin: plugin, isHub: market.hub, skills: skills, haystack: haystack))
            }
        }
        self.rows = rows
        sources = Self.sources(catalog, rows: Dictionary(rows.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a }),
                               missing: Set(problems.missing))
    }

    /// The quiet line above the list about marketplaces missing on this
    /// Mac; nil when none are, or when there is no list to put it above
    /// (`emptyText` says it then).
    public var missingNote: String? { total == 0 ? nil : problems.missingLine }

    /// What the page says when it has no plugin at all.
    public var emptyText: String {
        guard let line = problems.missingLine else {
            return "No plugin marketplaces registered in Claude Code, Codex or Cursor."
        }
        return "No plugins to show. \(line)."
    }

    /// Whether the marketplace named `name` can be renamed.
    public func isEditable(marketplace name: String) -> Bool { editableMarketplaces.contains(name) }

    public var total: Int { rows.count }

    /// The entry `ref` sits under now, for a detail view holding a row
    /// an older index produced: a reload swaps the index wholesale, and
    /// the opened plugin follows its ref rather than the snapshot. One
    /// plugin can sit under two sources; the first in source order wins.
    public func entry(for ref: String) -> ExplorePlugin? {
        for source in sources {
            for market in source.marketplaces {
                for entry in market.plugins where entry.row.id == ref { return entry }
            }
        }
        return nil
    }

    /// Local first, then each harness in swe's order, then the
    /// unregistered; marketplaces and plugins in the catalog's order.
    /// - Parameter missing: marketplaces registered but not on this Mac;
    ///   one with no plugins at all is left out rather than shown empty.
    private static func sources(_ catalog: PluginCatalog, rows: [String: ExploreRow],
                                missing: Set<String>) -> [ExploreSource] {
        var local: [ExploreMarketplace] = []
        var byHarness: [String: [ExploreMarketplace]] = [:]
        var unregistered: [ExploreMarketplace] = []
        // swe's harnesses, then any other a marketplace names, by name.
        let extra = Set(catalog.marketplaces.flatMap(\.harnesses.keys)).subtracting(catalog.harnesses).sorted()
        let order = catalog.harnesses + extra

        func entries(_ market: PluginMarketplace, source: String, harnesses: (CatalogPlugin) -> [String]) -> [ExplorePlugin] {
            market.plugins.compactMap { plugin in
                rows[plugin.ref].map { ExplorePlugin(source: source, row: $0, harnesses: harnesses(plugin)) }
            }
        }

        for market in catalog.marketplaces {
            if market.plugins.isEmpty, missing.contains(market.name) {
                // A registration whose folder is not on this Mac, like a
                // settings.json synced from another one: nothing to show,
                // and the page says so in one line instead.
                continue
            } else if market.hub {
                // Both harnesses' chips: the hub is meant for every harness
                // swe acts in, so a missing one is worth seeing.
                let tag = order.lazy.compactMap { market.harnesses[$0]?.sourceType }.first ?? "directory"
                let plugins = entries(market, source: "local") { plugin in
                    order.filter { harness in
                        guard let state = plugin.harnesses[harness] else { return false }
                        return catalog.actions.contains(harness) || state.status != .absent
                    }
                }
                local.append(ExploreMarketplace(id: "local/" + market.name, name: market.name,
                                                tag: ExploreMarketplace.tag(tag), plugins: plugins))
            } else if market.harnesses.isEmpty {
                let plugins = entries(market, source: "unregistered") { $0.shownHarnesses(in: order) }
                unregistered.append(ExploreMarketplace(id: "unregistered/" + market.name, name: market.name,
                                                       tag: nil, plugins: plugins))
            } else {
                let registered = order.filter { market.harnesses[$0] != nil }
                // Under each harness, the plugins that harness lists or has.
                // A copy installed in a harness that no longer registers the
                // marketplace goes under Not registered with that harness's
                // chip, so it can still be disabled or uninstalled. One no
                // harness lists or has goes under the first, so none is lost.
                var placed: [String: [CatalogPlugin]] = [:]
                var orphans: [ExplorePlugin] = []
                for plugin in market.plugins {
                    var homes = registered.filter { (plugin.harnesses[$0]?.status ?? .absent) != .absent }
                    let left = order.filter { market.harnesses[$0] == nil && (plugin.harnesses[$0]?.status.isInstalled ?? false) }
                    if !left.isEmpty, let row = rows[plugin.ref] {
                        orphans.append(ExplorePlugin(source: "unregistered", row: row, harnesses: left))
                    }
                    if homes.isEmpty, left.isEmpty, let first = registered.first { homes = [first] }
                    for harness in homes { placed[harness, default: []].append(plugin) }
                }
                if !orphans.isEmpty {
                    unregistered.append(ExploreMarketplace(id: "unregistered/" + market.name, name: market.name,
                                                           tag: nil, plugins: orphans))
                }
                for harness in registered {
                    let plugins = (placed[harness] ?? []).compactMap { plugin in
                        rows[plugin.ref].map { ExplorePlugin(source: harness, row: $0, harnesses: [harness]) }
                    }
                    guard !plugins.isEmpty else { continue }
                    byHarness[harness, default: []].append(ExploreMarketplace(
                        id: harness + "/" + market.name, name: market.name,
                        tag: ExploreMarketplace.tag(market.harnesses[harness]?.sourceType), plugins: plugins))
                }
            }
        }

        // Marketplace cards are a ForEach keyed by id too; a name the
        // catalog repeats keeps its first.
        var sources: [ExploreSource] = []
        if !local.isEmpty {
            sources.append(ExploreSource(kind: .local, title: "Local", note: "Your Quiver hub, ~/.quiver/plugins",
                                         marketplaces: local.uniqued(by: \.id)))
        }
        for harness in order {
            guard let markets = byHarness[harness] else { continue }
            let name = HarnessMark(harness).name
            sources.append(ExploreSource(kind: .harness(harness), title: name,
                                         note: catalog.actions.contains(harness) ? "Marketplaces \(name) registers" : "Read only",
                                         marketplaces: markets.uniqued(by: \.id)))
        }
        if !unregistered.isEmpty {
            sources.append(ExploreSource(kind: .unregistered, title: "Not registered",
                                         note: "Installed from a marketplace its harness no longer lists",
                                         marketplaces: unregistered.uniqued(by: \.id)))
        }
        return sources
    }

    /// The sources with only the rows matching the filters, empty
    /// marketplaces and sources left out.
    /// - Parameters:
    ///   - harness: only rows showing this harness's chip, with something
    ///     in it (listed, installed or cached); nil for all.
    ///   - query: matched against the name, ref, marketplace, description
    ///     and the names of the skills a plugin ships.
    public func sources(harness: String? = nil, query: String = "") -> [ExploreSource] {
        let needle = Self.needle(query)
        guard harness != nil || !needle.isEmpty else { return sources }
        return sources.compactMap { source in
            let markets = source.marketplaces.compactMap { market -> ExploreMarketplace? in
                let kept = market.plugins.filter { entry in
                    if let harness {
                        guard entry.harnesses.contains(harness),
                              (entry.row.plugin.harnesses[harness]?.status ?? .absent) != .absent else { return false }
                    }
                    return needle.isEmpty || entry.row.haystack.contains(needle)
                }
                return kept.isEmpty ? nil : kept.count == market.plugins.count ? market : market.with(plugins: kept)
            }
            return markets.isEmpty ? nil : source.with(marketplaces: markets)
        }
    }

    /// How many plugins `sources` holds, each counted once however many
    /// sources it sits under.
    public static func distinctPlugins(in sources: [ExploreSource]) -> Int {
        var refs: Set<String> = []
        for source in sources {
            for market in source.marketplaces {
                for entry in market.plugins { refs.insert(entry.row.id) }
            }
        }
        return refs.count
    }

    /// The search as matched: trimmed and lowercased.
    public static func needle(_ query: String) -> String {
        query.trimmingCharacters(in: .whitespaces).lowercased()
    }
}

/// What swe could not read, with one kind set apart: a marketplace a
/// harness registers whose folder is not on this Mac. A `~/.claude` synced
/// from another machine names several of those at once, and a warning for
/// each is noise nobody can act on from here, so the page says it in one
/// quiet line. Quiver never edits a harness's settings to drop them.
public struct CatalogProblems: Sendable, Equatable {
    /// swe's words for it (`_read_manifest` in src/swe/plugins/catalog.py,
    /// pinned by `test_registered_marketplace_missing_on_disk`).
    static let missingManifest = "no marketplace manifest on disk"

    /// The missing marketplaces' names, once each, sorted.
    public let missing: [String]
    /// Everything else, in swe's order.
    public let other: [PluginCatalog.Problem]

    public init(_ catalog: PluginCatalog) {
        var missing: Set<String> = []
        var other: [PluginCatalog.Problem] = []
        for problem in catalog.errors {
            if problem.detail == Self.missingManifest, let name = problem.marketplace {
                missing.insert(name)
            } else {
                other.append(problem)
            }
        }
        self.missing = missing.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
        self.other = other
    }

    /// "7 marketplaces are registered but missing on this Mac"; nil when none are.
    public var missingLine: String? {
        switch missing.count {
        case 0: nil
        case 1: "1 marketplace is registered but missing on this Mac"
        case let n: "\(n) marketplaces are registered but missing on this Mac"
        }
    }
}

extension Array {
    /// The first element of each key, in order.
    func uniqued<Key: Hashable>(by key: (Element) -> Key) -> [Element] {
        var seen: Set<Key> = []
        return filter { seen.insert(key($0)).inserted }
    }
}

extension PluginHarnessState.Status {
    /// Something of it is in the harness: on, off, installed, or Cursor's
    /// cached copy.
    public var isInstalled: Bool {
        switch self {
        case .enabled, .disabled, .installed, .cached: true
        case .available, .absent: false
        }
    }
}
