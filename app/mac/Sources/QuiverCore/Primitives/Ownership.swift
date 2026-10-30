import Foundation

/// Who may edit a marketplace, a plugin or a skill: the user's own only.
/// A marketplace is editable when a harness registers it as a `directory`
/// source inside `~/.quiver/plugins` (the hub). A plugin is editable when
/// its files sit inside such a marketplace's folder. A skill is editable
/// when its folder, symlinks resolved, lives under `~/.quiver` without
/// being part of a marketplace checkout or a cache. Anything else is
/// view only, because updates from wherever it really lives (a GitHub
/// clone, a harness cache) would silently undo the edits.
public enum PrimitiveOwnership {
    /// The folder an editable marketplace lives in, else nil. A
    /// marketplace counts when its `directory` registration resolves to a
    /// folder inside `~/.quiver/plugins`.
    public static func marketplaceDirectory(_ market: PluginMarketplace, home: URL,
                                            resolve: (String) -> String = FolderOwners.realPath) -> URL? {
        let plugins = resolve(home.appending(path: ".quiver/plugins").path)
        var candidates = market.harnesses.values
            .filter { $0.sourceType == "directory" }
            .compactMap(\.location)
        if let path = market.path { candidates.append(path) }
        for candidate in candidates {
            let resolved = URL(fileURLWithPath: resolve(candidate))
            guard resolved.path.hasPrefix(plugins + "/") else { continue }
            return resolved
        }
        return nil
    }

    /// Whether a marketplace is editable.
    public static func isEditable(_ market: PluginMarketplace, home: URL,
                                  resolve: (String) -> String = FolderOwners.realPath) -> Bool {
        marketplaceDirectory(market, home: home, resolve: resolve) != nil
    }

    /// The folder an editable plugin lives in, else nil. Only a plugin
    /// whose `sourcePath` sits inside its marketplace's editable folder
    /// counts; one checked out of a git remote has no local folder to edit.
    public static func pluginDirectory(_ plugin: CatalogPlugin, in market: PluginMarketplace,
                                       home: URL, resolve: (String) -> String = FolderOwners.realPath) -> URL? {
        guard let market = marketplaceDirectory(market, home: home, resolve: resolve),
              let source = plugin.sourcePath else { return nil }
        let resolved = URL(fileURLWithPath: resolve(source))
        guard resolved.path.hasPrefix(market.path + "/") else { return nil }
        return resolved
    }

    /// Whether a skill's folder is the user's own: under `~/.quiver` after
    /// symlinks resolve, and not inside the cache.
    public static func isEditable(skillPath: String, home: URL,
                                  resolve: (String) -> String = FolderOwners.realPath) -> Bool {
        let quiver = resolve(home.appending(path: ".quiver").path)
        let cache = quiver + "/cache"
        let resolved = resolve(skillPath)
        return resolved.hasPrefix(quiver + "/") && !resolved.hasPrefix(cache + "/")
    }

    /// Whether `path` stays inside `root` once symlinks resolve. A link
    /// inside an owned folder that points outside it is not the user's to
    /// edit, create or delete, even though its name suggests otherwise.
    public static func isInside(_ path: String, root: String,
                                resolve: (String) -> String = FolderOwners.realPath) -> Bool {
        let inside = FolderOwners.trimmed(resolve(root))
        let resolved = FolderOwners.trimmed(resolve(path))
        return resolved.hasPrefix(inside + "/")
    }

    /// Where `path` would really land: its deepest existing ancestor with
    /// symlinks resolved, then the missing rest of `path` on top. `realpath`
    /// stops at the first missing component, so a new part's folder is
    /// checked through the closest ancestor that exists. The missing rest
    /// can't hold a symlink, so its `.` and `..` apply as written: a name
    /// that climbs out of the ancestor lands outside it here too.
    public static func resolvedAncestor(of path: String,
                                        resolve: (String) -> String = FolderOwners.realPath) -> String {
        var candidate = path
        var missing: [String] = []
        while !FileManager.default.fileExists(atPath: candidate), candidate != "/" {
            // NSString drops the last component as written, `..` included;
            // URL's version leaves "/a/b/.." as it is, which looped forever.
            let parent = (candidate as NSString).deletingLastPathComponent
            guard parent != candidate, !parent.isEmpty else { break }
            missing.insert((candidate as NSString).lastPathComponent, at: 0)
            candidate = parent
        }
        var parts = FolderOwners.trimmed(resolve(candidate)).split(separator: "/").map(String.init)
        for part in missing {
            switch part {
            case ".", "": continue
            case "..": if !parts.isEmpty { parts.removeLast() }
            default: parts.append(part)
            }
        }
        return "/" + parts.joined(separator: "/")
    }
}

/// Quiver's own names for marketplaces and plugins. Renaming is display
/// only: a plugin's `name@marketplace` id never changes, so nothing is
/// reinstalled and nothing on disk a harness validates is touched. Kept in
/// `~/.quiver/config/plugin-display-names.json` and written atomically.
public struct PluginDisplayNames: Sendable, Equatable, Codable {
    /// Marketplace id to display name.
    public var marketplaces: [String: String] = [:]
    /// `name@marketplace` ref to display name.
    public var plugins: [String: String] = [:]

    public init(marketplaces: [String: String] = [:], plugins: [String: String] = [:]) {
        self.marketplaces = marketplaces
        self.plugins = plugins
    }

    public func name(forMarketplace id: String) -> String? { marketplaces[id] }
    public func name(forPlugin ref: String) -> String? { plugins[ref] }

    /// Set or clear (nil or empty) a marketplace's display name.
    public mutating func set(_ name: String?, forMarketplace id: String) {
        PluginDisplayNames.set(name, in: &marketplaces, for: id)
    }

    /// Set or clear (nil or empty) a plugin's display name.
    public mutating func set(_ name: String?, forPlugin ref: String) {
        PluginDisplayNames.set(name, in: &plugins, for: ref)
    }

    private static func set(_ name: String?, in map: inout [String: String], for key: String) {
        let trimmed = name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if trimmed.isEmpty { map.removeValue(forKey: key) } else { map[key] = trimmed }
    }

    /// The file `~/.quiver/config/plugin-display-names.json`.
    public static func file(home: URL) -> URL {
        home.appending(path: ".quiver/config/plugin-display-names.json")
    }

    /// What the file holds; an empty overlay when it is missing or unreadable.
    public static func load(from url: URL) -> PluginDisplayNames {
        guard let data = try? Data(contentsOf: url),
              let names = try? JSONDecoder().decode(PluginDisplayNames.self, from: data)
        else { return PluginDisplayNames() }
        return names
    }

    /// Apply `change`, persist it, and roll the overlay back when the
    /// write fails: a failed rename changes nothing. `write` is
    /// injectable so a failing save can be exercised in tests.
    public mutating func saved(_ change: (inout PluginDisplayNames) -> Void, to url: URL,
                               write: (PluginDisplayNames, URL) throws -> Void = { try $0.save(to: $1) }) throws {
        let before = self
        change(&self)
        do {
            try write(self, url)
        } catch {
            self = before
            throw error
        }
    }

    /// Write atomically, creating `config/` when needed.
    public func save(to url: URL) throws {
        let data = try JSONEncoder().encode(self)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
    }
}
