import Foundation
import Observation

/// `swe mcp list --json` / `swe mcp status --json`.
///
/// Env and header values never reach the app: swe sends only their names,
/// and scrubs credentials out of commands, arguments and URLs.
public struct McpInventory: Decodable, Sendable, Equatable {
    /// Every harness config swe read, servers or not, in its order.
    public let harnesses: [String]
    public let servers: [McpServer]

    public init(harnesses: [String], servers: [McpServer]) {
        self.harnesses = harnesses
        self.servers = servers
    }

    enum CodingKeys: String, CodingKey { case harnesses, servers }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        servers = try container.decode([McpServer].self, forKey: .servers)
        // A swe from before `harnesses` existed: the ones with servers will do.
        if let named = try container.decodeIfPresent([String].self, forKey: .harnesses) {
            harnesses = named
        } else {
            var seen: [String] = []
            for name in servers.flatMap(\.harnesses) where !seen.contains(name) { seen.append(name) }
            harnesses = seen
        }
    }

    public func servers(_ filter: McpFilter) -> [McpServer] {
        switch filter {
        case .all: servers
        case .drift: servers.filter { $0.drifts(across: harnesses) }
        case .conflicts: servers.filter(\.conflict)
        }
    }
}

public enum McpFilter: String, CaseIterable, Sendable {
    case all, drift, conflicts

    public var title: String {
        switch self {
        case .all: "All"
        case .drift: "Out of sync"
        case .conflicts: "Conflicts"
        }
    }
}

public struct McpServer: Decodable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let transport: String
    public let command: String?
    public let args: [String]
    public let url: String?
    public let envKeys: [String]
    public let headerKeys: [String]
    /// Harnesses that define this server, in swe's order.
    public let harnesses: [String]
    public let conflict: Bool
    /// The harnesses grouped by identical definition. Absent from an older
    /// swe, which only says whether there is a conflict.
    public let variants: [[String]]?
    /// From `swe mcp status` only: ok, error or unknown.
    public let health: String?
    public let detail: String?

    public init(name: String, transport: String = "stdio", command: String? = nil, args: [String] = [],
                url: String? = nil, envKeys: [String] = [], headerKeys: [String] = [],
                harnesses: [String], conflict: Bool = false, variants: [[String]]? = nil,
                health: String? = nil, detail: String? = nil) {
        self.name = name
        self.transport = transport
        self.command = command
        self.args = args
        self.url = url
        self.envKeys = envKeys
        self.headerKeys = headerKeys
        self.harnesses = harnesses
        self.conflict = conflict
        self.variants = variants
        self.health = health
        self.detail = detail
    }

    /// Definitions grouped by who agrees. Without `variants`, everyone is
    /// one group when there is no conflict and nothing can be said when there is.
    public var groups: [[String]] {
        if let variants { return variants }
        return conflict ? harnesses.map { [$0] } : [harnesses]
    }

    /// Which group a harness's definition is in, nil when it lacks the server.
    public func group(of harness: String) -> Int? {
        groups.firstIndex { $0.contains(harness) }
    }

    public func missing(from all: [String]) -> [String] {
        all.filter { !harnesses.contains($0) }
    }

    /// Missing from some harness, or defined differently in two.
    public func drifts(across all: [String]) -> Bool {
        conflict || !missing(from: all).isEmpty
    }

    /// The command line or URL, for a one-line summary.
    public var summary: String {
        if let url { return url }
        return ([command ?? ""] + args).joined(separator: " ").trimmingCharacters(in: .whitespaces)
    }

    /// `--only` takes fnmatch globs, comma separated. A comma cannot be
    /// escaped there, so such a name cannot be synced on its own.
    public var canSyncAlone: Bool { !name.contains(",") }

    /// The name as an fnmatch pattern matching only itself.
    public var onlyPattern: String {
        var out = ""
        for character in name {
            switch character {
            case "[": out += "[[]"
            case "*", "?": out += "[\(character)]"
            default: out.append(character)
            }
        }
        return out
    }
}

/// `swe mcp sync … --json`: which server names moved where.
public struct McpSyncReport: Decodable, Sendable, Equatable {
    public let source: String?
    public let dryRun: Bool
    public let selected: [String]
    public let targets: [Target]

    public struct Target: Decodable, Sendable, Equatable {
        public let name: String
        public let added: [String]
        public let updated: [String]
        public let skipped: [String]
        public let pruned: [String]

        public init(name: String, added: [String] = [], updated: [String] = [],
                    skipped: [String] = [], pruned: [String] = []) {
            self.name = name
            self.added = added
            self.updated = updated
            self.skipped = skipped
            self.pruned = pruned
        }
    }

    public init(source: String?, dryRun: Bool, selected: [String], targets: [Target]) {
        self.source = source
        self.dryRun = dryRun
        self.selected = selected
        self.targets = targets
    }

    /// One sentence for the page: "Added to cursor and cline. Updated codex."
    public var sentence: String {
        func names(_ list: [String]) -> String {
            switch list.count {
            case 0: ""
            case 1: list[0]
            default: list.dropLast().joined(separator: ", ") + " and " + list.last!
            }
        }
        let added = targets.filter { !$0.added.isEmpty }.map(\.name)
        let updated = targets.filter { !$0.updated.isEmpty }.map(\.name)
        let skipped = targets.filter { !$0.skipped.isEmpty }.map(\.name)
        var parts: [String] = []
        if !added.isEmpty { parts.append((dryRun ? "Would add to " : "Added to ") + names(added) + ".") }
        if !updated.isEmpty { parts.append((dryRun ? "Would update " : "Updated ") + names(updated) + ".") }
        if !skipped.isEmpty { parts.append((dryRun ? "Would leave " : "Left ") + names(skipped) + " as it was.") }
        return parts.isEmpty ? "Nothing to change." : parts.joined(separator: " ")
    }
}

extension SweClient {
    public func mcpServers(health: Bool) async throws -> McpInventory {
        try await call(["mcp", health ? "status" : "list", "--json"])
    }

    /// Copies one server from `source` to `targets` through `swe mcp sync`.
    /// `overwrite` replaces a different definition a target already has.
    public func mcpSync(server: McpServer, from source: String, to targets: [String],
                        overwrite: Bool, dryRun: Bool) async throws -> McpSyncReport {
        var args = ["mcp", "sync", source] + targets
        args += ["--only=" + server.onlyPattern, "--no-interactive", "--json"]
        if overwrite { args.append("--force") }
        if dryRun { args.append("--dry-run") }
        return try await call(args)
    }
}

@MainActor
@Observable
public final class McpModel {
    public private(set) var state: Loadable<McpInventory> = .idle
    /// True while `swe mcp status` probes every server.
    public private(set) var checkingHealth = false
    public private(set) var hasHealth = false
    private var generation = 0

    public init() {}

    /// The list, or with `health` the list plus a probe of every server,
    /// which takes a few seconds per unreachable URL.
    public func load(swe: SweClient, health: Bool = false) async {
        generation += 1
        let mine = generation
        if case .loaded = state {} else { state = .loading }
        if health { checkingHealth = true }
        defer { if mine == generation { checkingHealth = false } }
        let next: Loadable<McpInventory>
        do {
            next = .loaded(try await swe.mcpServers(health: health))
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        guard mine == generation else { return }
        state = next
        hasHealth = health && { if case .loaded = next { true } else { false } }()
    }

    /// What a sync of `server` from `source` can reach: harnesses without
    /// the server (picked by default) and ones with a different definition
    /// (left for the user to opt into, since that overwrites theirs).
    public func targets(for server: McpServer, from source: String, in harnesses: [String]) -> SyncTargets {
        let sourceGroup = server.group(of: source)
        var missing: [String] = []
        var differing: [String] = []
        for harness in harnesses where harness != source {
            switch server.group(of: harness) {
            case nil: missing.append(harness)
            case sourceGroup: continue
            default: differing.append(harness)
            }
        }
        return SyncTargets(missing: missing, differing: differing)
    }
}

public struct SyncTargets: Equatable, Sendable {
    public let missing: [String]
    public let differing: [String]

    /// `--force` is needed exactly when a chosen target already has its own copy.
    public func needsOverwrite(_ chosen: Set<String>) -> Bool {
        !chosen.isDisjoint(with: differing)
    }
}
