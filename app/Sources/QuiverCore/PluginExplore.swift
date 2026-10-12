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

    public init(catalog: PluginCatalog, skills: [Skill]) {
        self.catalog = catalog
        self.skills = skills
        let placement = SkillPlacement(skills: skills, catalog: catalog)
        self.placement = placement
        var rows: [ExploreRow] = []
        for market in catalog.marketplaces {
            for plugin in market.plugins {
                let skills = placement.skills(of: plugin)
                let haystack = ([plugin.name, plugin.ref, plugin.marketplace,
                                 plugin.displayName ?? "", plugin.description ?? ""]
                    + skills.map(\.name)).joined(separator: "\n").lowercased()
                rows.append(ExploreRow(plugin: plugin, isHub: market.hub, skills: skills, haystack: haystack))
            }
        }
        self.rows = rows
        sources = Self.sources(catalog, rows: Dictionary(rows.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a }))
    }

    public var total: Int { rows.count }

    /// Local first, then each harness in swe's order, then the
    /// unregistered; marketplaces and plugins in the catalog's order.
    private static func sources(_ catalog: PluginCatalog, rows: [String: ExploreRow]) -> [ExploreSource] {
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
            if market.hub {
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

        var sources: [ExploreSource] = []
        if !local.isEmpty {
            sources.append(ExploreSource(kind: .local, title: "Local", note: "Your Quiver hub, ~/.quiver/plugins",
                                         marketplaces: local))
        }
        for harness in order {
            guard let markets = byHarness[harness] else { continue }
            let name = HarnessMark(harness).name
            sources.append(ExploreSource(kind: .harness(harness), title: name,
                                         note: catalog.actions.contains(harness) ? "Marketplaces \(name) registers" : "Read only",
                                         marketplaces: markets))
        }
        if !unregistered.isEmpty {
            sources.append(ExploreSource(kind: .unregistered, title: "Not registered",
                                         note: "Installed from a marketplace its harness no longer lists",
                                         marketplaces: unregistered))
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
