import Foundation
import Observation

/// `swe mcp list --json` / `swe mcp status --json`.
///
/// Env and header values never reach the app: swe sends only their names,
/// and scrubs credentials out of commands, arguments and URLs.
public struct McpInventory: SnapshotValue {
    public static let snapshotVersion = 1

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

    /// What may be saved: every server as `McpServer.saved` keeps it.
    public var saved: McpInventory {
        McpInventory(harnesses: harnesses, servers: servers.map(\.saved))
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

public struct McpServer: Codable, Sendable, Equatable, Identifiable {
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
    /// Set only on a saved server, which keeps `summary` in place of the
    /// command line or URL.
    let savedSummary: String?

    public init(name: String, transport: String = "stdio", command: String? = nil, args: [String] = [],
                url: String? = nil, envKeys: [String] = [], headerKeys: [String] = [],
                harnesses: [String], conflict: Bool = false, variants: [[String]]? = nil,
                health: String? = nil, detail: String? = nil, savedSummary: String? = nil) {
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
        self.savedSummary = savedSummary
    }

    /// The server as it may be written to disk. No command line or URL:
    /// a short key in an argument or URL path slips past both swe's and
    /// the app's scrubbers, so only the program or the scheme and host
    /// are kept, with "…" for the rest. No health either, a probe of the
    /// moment whose detail may quote a URL. Env and header values never
    /// reach the app at all.
    public var saved: McpServer {
        McpServer(name: name, transport: transport, envKeys: envKeys, headerKeys: headerKeys, harnesses: harnesses,
                  conflict: conflict, variants: variants, savedSummary: savedSummary ?? outline)
    }

    /// The program or the scheme and host, and "…" when there is more.
    var outline: String {
        if let url {
            let redacted = McpRedaction.url(url)
            let start = redacted.range(of: "://")?.upperBound ?? redacted.startIndex
            guard let end = redacted[start...].firstIndex(where: { "/?#".contains($0) }) else { return redacted }
            return redacted[..<end] + "/…"
        }
        let words = McpRedaction.words(command: command ?? "", args: args)
        guard let program = words.first else { return "" }
        let shown = McpRedaction.args([program]).first ?? "…"
        return words.count > 1 ? shown + " …" : shown
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


    /// Two of `harnesses` hold different definitions.
    public func conflicts(among harnesses: [String]) -> Bool {
        Set(harnesses.compactMap { group(of: $0) }).count > 1
    }

    /// The command line or URL, for a one-line summary, with anything that
    /// may be a secret hidden. swe swaps known keys for `${NAME}`, but a
    /// token it doesn't know would otherwise show as typed.
    public var summary: String {
        if let savedSummary { return savedSummary }
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
        let flag = bare(flag)
        guard flag.hasPrefix("-") else { return false }
        if flag == "-H" { return true }
        let name = flag.filter { $0 != "-" && $0 != "_" }.lowercased()
        return !name.isEmpty && secretFlagEndings.contains { name.hasSuffix($0) }
    }

    /// The flag as the shell reads it: `--"token"` and `--\token` are `--token`.
    static func bare(_ flag: Substring) -> String {
        flag.filter { $0 != "\"" && $0 != "'" && $0 != "\\" }
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
        if quote != nil { return looseWords(line) }
        if !word.isEmpty { words.append(word) }
        return words
    }

    /// Split at every space and quote mark, for text the shell split can't
    /// handle, so a flag glued to a quote (`"--token`) is still seen.
    static func looseWords(_ line: String) -> [String] {
        line.split { $0.isWhitespace || $0 == "\"" || $0 == "'" }.map(String.init)
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
            } else if let redacted = urlWord(arg) {
                out.append(redacted)
            } else if let quote = arg.first, quote == "\"" || quote == "'", arg.count >= 2, arg.last == quote {
                // A quoted word may be a whole command, as `bash -c '…'` takes.
                out.append(String(quote) + line(String(arg.dropFirst().dropLast())) + String(quote))
            } else if arg.contains(where: \.isWhitespace) {
                out.append(line(arg))
            } else if arg.contains("://") {
                out.append(url(arg))
            } else {
                out.append(SecretText.maskingKeys(arg[...]))
            }
        }
        return out
    }

    /// A word that is a URL, quoted or not, redacted before anything splits
    /// it. A URL holds no spaces, so everything after one is hidden: it may
    /// be the rest of a query value.
    private static func urlWord(_ arg: String) -> String? {
        var quote = ""
        var inner = Substring(arg)
        if let first = arg.first, first == "\"" || first == "'", arg.count >= 2, arg.last == first {
            quote = String(first)
            inner = inner.dropFirst().dropLast()
        }
        guard inner.contains(/^[A-Za-z][A-Za-z0-9+.\-]*:\/\//) else { return nil }
        guard let space = inner.firstIndex(where: \.isWhitespace) else { return quote + url(String(inner)) + quote }
        return quote + url(String(inner[..<space])) + " …" + quote
    }

    /// A command line inside one word, redacted word by word. A word the
    /// shell split can't break up (`x"a b"`) is split at every space and
    /// quote mark, so each pass works on shorter text and no flag hides.
    private static func line(_ text: String) -> String {
        var words = shellWords(text)
        if words == [text] { words = looseWords(text) }
        return args(words).joined(separator: " ")
    }

    /// A header keeps its name (`Authorization:…`); any other value is hidden.
    private static func secretValue(_ value: Substring, after flag: Substring) -> String {
        let flag = bare(flag)
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
        // A segment is judged decoded, so `sk%2Dproj%2D…` reads as `sk-proj-…`.
        out += rest.split(separator: "/", omittingEmptySubsequences: false).map { segment in
            let decoded = segment.removingPercentEncoding ?? String(segment)
            if decoded != segment, SecretText.maskingKeys(decoded[...]) != decoded { return "…" }
            return SecretText.maskingKeys(segment)
        }.joined(separator: "/")
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
    public var state: Loadable<McpInventory> { loader.state }
    /// True while `swe mcp status` probes every server.
    public private(set) var checkingHealth = false
    /// The list on show came with health, from `swe mcp status`.
    public var hasHealth: Bool {
        guard case .loaded(let inventory) = state else { return false }
        return inventory.servers.contains { $0.health != nil }
    }
    private let loader: SnapshotLoader<McpInventory>
    private var generation = 0

    /// - Parameter snapshots: where the last list is kept between launches,
    ///   as `McpInventory.saved` has it.
    public init(snapshots: (any SnapshotStoring<Snapshot<McpInventory>>)? = nil, maxAge: TimeInterval = 60,
                timeout: Duration = .seconds(60), now: @escaping @Sendable () -> Date = Date.init) {
        loader = SnapshotLoader(snapshots: snapshots, maxAge: maxAge, timeout: timeout, now: now,
                                saving: \.saved)
    }

    /// Shows the last saved list, without health, until swe answers.
    public func restore() async {
        await loader.restore()
    }

    /// Reads the list again unless this swe's is fresh; joins a read in flight.
    public func loadIfStale(swe: SweClient, for source: String) async {
        await loader.loadIfStale(from: source) { try await swe.mcpServers(health: false) }
    }

    /// The list, or with `health` the list plus a probe of every server,
    /// which takes a few seconds per unreachable URL, so it gets longer
    /// before it counts as hung.
    /// - Parameter source: the swe asked, as set in Settings; nil keeps the last one.
    public func load(swe: SweClient, for source: String? = nil, health: Bool = false) async {
        generation += 1
        let mine = generation
        if health { checkingHealth = true }
        defer { if mine == generation { checkingHealth = false } }
        await loader.load(from: source, timeout: health ? .seconds(300) : nil) {
            try await swe.mcpServers(health: health)
        }
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
