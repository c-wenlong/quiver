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

    /// Harnesses that configure at least one server. One with none is not
    /// using MCP, so it gets no column and does not make every server look
    /// out of sync; it can still be a sync target.
    public var activeHarnesses: [String] {
        harnesses.filter { harness in servers.contains { $0.harnesses.contains(harness) } }
    }

    public var idleHarnesses: [String] {
        harnesses.filter { !activeHarnesses.contains($0) }
    }

    /// The servers `filter` keeps, judged across `compared` (default: the
    /// active harnesses). A conflict counts only between compared harnesses.
    public func servers(_ filter: McpFilter, across compared: [String]? = nil) -> [McpServer] {
        let compared = compared ?? activeHarnesses
        switch filter {
        case .all:
            return servers
        case .drift:
            return servers.filter { server in
                !server.missing(from: compared).isEmpty || server.conflicts(among: compared)
            }
        case .conflicts:
            return servers.filter { $0.conflicts(among: compared) }
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

    /// Two of `harnesses` hold different definitions.
    public func conflicts(among harnesses: [String]) -> Bool {
        Set(harnesses.compactMap { group(of: $0) }).count > 1
    }

    /// The command line or URL, for a one-line summary, with anything that
    /// may be a secret hidden. swe swaps known keys for `${NAME}`, but a
    /// token it doesn't know would otherwise show as typed.
    public var summary: String {
        if let url { return McpRedaction.url(url) }
        let words = McpRedaction.words(command: command ?? "", args: args)
        return McpRedaction.args(words).joined(separator: " ").trimmingCharacters(in: .whitespaces)
    }

    /// `--only` takes fnmatch globs, comma separated. A comma cannot be
    /// escaped there, so such a name cannot be synced on its own; nor can an
    /// empty one, since `--only=` with no pattern selects every server.
    public var canSyncAlone: Bool { !name.isEmpty && !name.contains(",") }

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

/// Hides the parts of an MCP command line or URL that may hold a secret.
enum McpRedaction {
    /// Flags whose value is a secret, compared without dashes, underscores
    /// or case, so `--api-key`, `--api_key` and `--apiKey` all match, as do
    /// longer names ending in one (`--access-token`, `--client-secret`).
    static let secretFlagEndings = ["token", "key", "secret", "password", "passwd", "credential", "credentials",
                                    "auth", "authorization", "header"]

    static func isSecretFlag(_ flag: Substring) -> Bool {
        guard flag.hasPrefix("-") else { return false }
        if flag == "-H" { return true }
        let name = flag.filter { $0 != "-" && $0 != "_" }.lowercased()
        return !name.isEmpty && secretFlagEndings.contains { name.hasSuffix($0) }
    }

    /// The command line as words, so every flag and its value can be judged.
    /// A whole command line typed into `command` is split the way a shell
    /// would, keeping a quoted value with spaces as one word. An argument
    /// holding a flag and its value (`--token abc`) becomes two.
    static func words(command: String, args: [String]) -> [String] {
        var words = shellWords(command)
        for arg in args {
            let space = arg.firstIndex(where: \.isWhitespace)
            if arg.hasPrefix("-"), let space, !arg[..<space].contains("=") {
                words.append(String(arg[..<space]))
                words.append(arg[space...].trimmingCharacters(in: .whitespaces))
            } else {
                words.append(arg)
            }
        }
        return words
    }

    /// Words split at unquoted whitespace, quotes kept so the text reads as
    /// typed. With an unclosed quote every space splits, so no word hides a
    /// later flag.
    static func shellWords(_ line: String) -> [String] {
        var words: [String] = []
        var word = ""
        var quote: Character?
        var escaped = false
        for character in line {
            if escaped {
                word.append(character)
                escaped = false
            } else if character == "\\", quote != "'" {
                word.append(character)
                escaped = true
            } else if let open = quote {
                word.append(character)
                if character == open { quote = nil }
            } else if character == "\"" || character == "'" {
                word.append(character)
                quote = character
            } else if character.isWhitespace {
                if !word.isEmpty { words.append(word) }
                word = ""
            } else {
                word.append(character)
            }
        }
        if quote != nil { return line.split(whereSeparator: \.isWhitespace).map(String.init) }
        if !word.isEmpty { words.append(word) }
        return words
    }

    static func args(_ args: [String]) -> [String] {
        var out: [String] = []
        var hideNext: Substring?
        for arg in args {
            if let flag = hideNext {
                out.append(secretValue(arg[...], after: flag))
                hideNext = nil
            } else if let equals = arg.firstIndex(of: "="), isSecretFlag(arg[..<equals]) {
                let flag = arg[..<equals]
                out.append(flag + "=" + secretValue(arg[arg.index(after: equals)...], after: flag))
            } else if isSecretFlag(arg[...]) {
                out.append(arg)
                hideNext = arg[...]
            } else if let match = arg.firstMatch(of: /^[A-Za-z_][A-Za-z0-9_]*=/) {
                // An environment assignment, as `env NAME=value cmd` takes.
                out.append(match.output + "…")
            } else if arg.contains("://") {
                out.append(url(arg))
            } else {
                out.append(SecretText.maskingKeys(arg[...]))
            }
        }
        return out
    }

    /// A header keeps its name (`Authorization:…`); any other value is hidden.
    private static func secretValue(_ value: Substring, after flag: Substring) -> String {
        let header = flag == "-H" || flag.lowercased().hasSuffix("header")
        if header, let colon = value.firstIndex(of: ":") { return value[...colon] + "…" }
        return "…"
    }

    /// The URL with its userinfo, query values and fragment hidden, and any
    /// key-like path segment too, since some services put the key in the path.
    static func url(_ url: String) -> String {
        var rest = Substring(url)
        var out = ""
        if let scheme = rest.range(of: "://") {
            out += rest[..<scheme.upperBound]
            rest = rest[scheme.upperBound...]
        }
        let authorityEnd = rest.firstIndex(where: { "/?#".contains($0) }) ?? rest.endIndex
        var authority = rest[..<authorityEnd]
        if let at = authority.lastIndex(of: "@") {
            out += "…@"
            authority = authority[authority.index(after: at)...]
        }
        out += authority
        rest = rest[authorityEnd...]
        var fragment: Substring?
        if let hash = rest.firstIndex(of: "#") {
            fragment = rest[rest.index(after: hash)...]
            rest = rest[..<hash]
        }
        var query: Substring?
        if let mark = rest.firstIndex(of: "?") {
            query = rest[rest.index(after: mark)...]
            rest = rest[..<mark]
        }
        out += SecretText.maskingKeys(rest)
        if let query {
            out += "?" + query.split(separator: "&", omittingEmptySubsequences: false).map { pair in
                if pair.isEmpty { return "" }
                guard let equals = pair.firstIndex(of: "=") else { return "…" }
                return pair[...equals] + "…"
            }.joined(separator: "&")
        }
        if let fragment, !fragment.isEmpty { out += "#…" }
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

    /// The server as the latest load has it.
    public func server(named name: String) -> McpServer? {
        guard case .loaded(let inventory) = state else { return nil }
        return inventory.servers.first { $0.name == name }
    }

    /// Copies `server` from `source`: to `adding` without `--force`, so a
    /// definition that appeared there since is left alone, and to the
    /// approved `replacing` targets with it. One report for both.
    public func sync(_ server: McpServer, from source: String, adding: [String], replacing: [String],
                     dryRun: Bool, swe: SweClient) async throws -> McpSyncReport {
        var parts: [McpSyncReport] = []
        func merged() -> McpSyncReport {
            McpSyncReport(source: source, dryRun: dryRun, selected: parts.first?.selected ?? [],
                          targets: parts.flatMap(\.targets))
        }
        let runs = [(adding, false), (replacing, true)].filter { !$0.0.isEmpty }
        for (targets, overwrite) in runs {
            do {
                parts.append(try await swe.mcpSync(server: server, from: source, to: targets,
                                                   overwrite: overwrite, dryRun: dryRun))
            } catch {
                // What already happened has to stay visible.
                if parts.isEmpty { throw error }
                throw McpPartialSync(report: merged(), message: error.localizedDescription)
            }
        }
        return merged()
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

/// A sync whose first call went through and a later one failed.
public struct McpPartialSync: Error, LocalizedError, Sendable {
    public let report: McpSyncReport
    public let message: String
    public var errorDescription: String? { message }
}

public struct SyncTargets: Equatable, Sendable {
    public let missing: [String]
    public let differing: [String]

    /// `--force` is needed exactly when a chosen target already has its own copy.
    public func needsOverwrite(_ chosen: Set<String>) -> Bool {
        !chosen.isDisjoint(with: differing)
    }
}
