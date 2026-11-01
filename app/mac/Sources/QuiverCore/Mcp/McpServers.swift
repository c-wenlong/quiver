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
    /// `${NAME}` references in the hub's definition with no value in the
    /// secrets file. Absent from an older swe, decoded as none missing.
    public let secretsMissing: [String]

    /// Reached at a URL (http, sse) rather than launched with a command on
    /// this machine; the MCP page's Remote group.
    public var isRemote: Bool { transport == "http" || transport == "sse" }

    /// Whether the definition a sync copies from `source` is at a URL: a
    /// server can be local in one harness and remote in another.
    public func isRemote(from source: String) -> Bool {
        remoteIn.map { $0.contains(source) } ?? isRemote
    }

    /// Harnesses whose config launches local servers only, as swe's
    /// `local_only` marks them: Claude Desktop adds a remote server in the
    /// app itself (Settings › Connectors).
    public static let localOnly: Set<String> = ["claude-desktop"]
    /// Whether ~/.quiver/mcp.json defines this server. A hub-only row has
    /// no harnesses; an older swe sends nothing and decodes false.
    public let inHub: Bool
    /// The places (harnesses, "quiver" for the hub) whose definition is at
    /// a URL. Absent from an older swe, which leaves `transport` to say.
    public let remoteIn: [String]?
    /// Set only on a saved server, which keeps `summary` in place of the
    /// command line or URL.
    let savedSummary: String?

    public init(name: String, transport: String = "stdio", command: String? = nil, args: [String] = [],
                url: String? = nil, envKeys: [String] = [], headerKeys: [String] = [],
                harnesses: [String], conflict: Bool = false, variants: [[String]]? = nil,
                health: String? = nil, detail: String? = nil, secretsMissing: [String] = [],
                inHub: Bool = false, savedSummary: String? = nil, remoteIn: [String]? = nil) {
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
        self.secretsMissing = secretsMissing
        self.inHub = inHub
        self.savedSummary = savedSummary
        self.remoteIn = remoteIn
    }

    private enum CodingKeys: String, CodingKey {
        case name, transport, command, args, url, envKeys, headerKeys
        case harnesses, conflict, variants, health, detail
        case secretsMissing, inHub, savedSummary, remoteIn
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        transport = try container.decode(String.self, forKey: .transport)
        command = try container.decodeIfPresent(String.self, forKey: .command)
        args = try container.decodeIfPresent([String].self, forKey: .args) ?? []
        url = try container.decodeIfPresent(String.self, forKey: .url)
        envKeys = try container.decodeIfPresent([String].self, forKey: .envKeys) ?? []
        headerKeys = try container.decodeIfPresent([String].self, forKey: .headerKeys) ?? []
        harnesses = try container.decode([String].self, forKey: .harnesses)
        conflict = try container.decodeIfPresent(Bool.self, forKey: .conflict) ?? false
        variants = try container.decodeIfPresent([[String]].self, forKey: .variants)
        health = try container.decodeIfPresent(String.self, forKey: .health)
        detail = try container.decodeIfPresent(String.self, forKey: .detail)
        secretsMissing = try container.decodeIfPresent([String].self, forKey: .secretsMissing) ?? []
        inHub = try container.decodeIfPresent(Bool.self, forKey: .inHub) ?? false
        savedSummary = try container.decodeIfPresent(String.self, forKey: .savedSummary)
        remoteIn = try container.decodeIfPresent([String].self, forKey: .remoteIn)
    }

    /// The server as it may be written to disk. No command line or URL:
    /// a short key in an argument or URL path slips past both swe's and
    /// the app's scrubbers, so only the program or the scheme and host
    /// are kept, with "…" for the rest. No health either, a probe of the
    /// moment whose detail may quote a URL. Env and header values never
    /// reach the app at all.
    public var saved: McpServer {
        McpServer(name: name, transport: transport, envKeys: envKeys, headerKeys: headerKeys, harnesses: harnesses,
                  conflict: conflict, variants: variants, secretsMissing: secretsMissing,
                  inHub: inHub, savedSummary: savedSummary ?? outline, remoteIn: remoteIn)
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
    /// Words that mark a secret anywhere in a flag or key name, compared
    /// without dashes, underscores or case: `--api-key`, `--apiKey`,
    /// `--keyfile`, `SESSION_COOKIE`. The same words as swe's
    /// `_SECRET_FLAG_RE` (src/swe/mcp/secrets.py), so the app hides
    /// whatever `swe mcp` hides.
    static let secretNameParts = ["key", "token", "secret", "passw", "bearer", "credential", "cookie",
                                  "signature", "authorization", "authorisation"]
    /// Endings that mark one too: `--auth`, and a header flag's value
    /// (`--header`, `--headers`, `--proxy-header`).
    static let secretNameEndings = ["auth", "header", "headers"]

    static func isSecretName(_ name: String) -> Bool {
        let name = name.filter { $0 != "-" && $0 != "_" }.lowercased()
        return !name.isEmpty && (secretNameParts.contains { name.contains($0) }
                                 || secretNameEndings.contains { name.hasSuffix($0) })
    }

    static func isSecretFlag(_ flag: Substring) -> Bool {
        let flag = bare(flag)
        guard flag.hasPrefix("-") else { return false }
        if flag == "-H" { return true }
        return isSecretName(flag)
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
            || flag.lowercased().hasSuffix("headers")
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

    /// `swe mcp secret set NAME` with the value on stdin: never in argv,
    /// logs or the error text, where it would leak.
    @discardableResult
    public func mcpSecretSet(name: String, value: String) async throws -> McpSecretSetResult {
        try await call(["mcp", "secret", "set", name, "--json"],
                       input: Data((value + "\n").utf8))
    }

    /// `swe mcp secret remove NAME`: drops a stored value. True when one
    /// was there.
    @discardableResult
    public func mcpSecretRemove(name: String) async throws -> Bool {
        let result: McpSecretRemoveResult = try await call(["mcp", "secret", "remove", name, "--json"])
        return result.removed
    }

    /// Adds `spec` to the hub, and to each of `harnesses` when asked.
    public func mcpAdd(_ spec: McpServerSpec, to harnesses: [String],
                       force: Bool) async throws -> McpAddResult {
        var args = ["mcp", "add", spec.name] + spec.flags
            + ["--json"]
        if !harnesses.isEmpty { args += ["--to=" + harnesses.joined(separator: ",")] }
        if force { args.append("--force") }
        args += spec.tail
        return try await call(args)
    }

    /// Removes `name` from each place; swe reports `quiver` for the hub.
    public func mcpRemove(name: String, from places: [String]) async throws -> McpRemoveResult {
        try await call(["mcp", "remove", name, "--from=" + places.joined(separator: ","), "--json"])
    }
}

/// `swe mcp secret set --json`: where the value landed, never the value.
public struct McpSecretSetResult: Decodable, Sendable, Equatable {
    public let name: String
    public let path: String
    public let created: Bool
    /// Whether the name held a value before. Nil from a swe too old to
    /// say, which a caller undoing the set must read as true.
    public let replaced: Bool?
}

/// `swe mcp secret remove --json`.
public struct McpSecretRemoveResult: Decodable, Sendable, Equatable {
    public let name: String
    public let removed: Bool
}

/// What `swe mcp add` writes, before it becomes argv.
public struct McpServerSpec: Sendable {
    public enum Kind: Sendable {
        case command(command: String, args: [String], env: [(String, String)])
        case url(url: String, transport: String, headers: [(String, String)])
    }

    public let name: String
    public let kind: Kind

    public init(name: String, kind: Kind) {
        self.name = name
        self.kind = kind
    }

    /// The `--env`/`--url`/`--transport`/`--header` flags, which all come
    /// before any `--` tail.
    public var flags: [String] {
        switch kind {
        case .command(_, _, let env):
            return env.flatMap { ["--env=\($0.0)=\($0.1)"] }
        case .url(let url, let transport, let headers):
            var out = ["--url=\(url)"]
            if transport != "http" { out.append("--transport=\(transport)") }
            return out + headers.flatMap { ["--header=\($0.0)=\($0.1)"] }
        }
    }

    /// The bare `--` plus command and args, or empty for a URL server.
    /// Must be the last words on the line: swe never interprets them.
    public var tail: [String] {
        switch kind {
        case .command(let command, let args, _): return ["--", command] + args
        case .url: return []
        }
    }
}

/// `swe mcp add --json`: what landed where.
public struct McpAddResult: Decodable, Sendable, Equatable {
    public struct Target: Decodable, Sendable, Equatable {
        public let name: String
        public let status: String
    }
    public let name: String
    public let hub: String
    public let targets: [Target]
}

/// `swe mcp remove --json`.
public struct McpRemoveResult: Decodable, Sendable, Equatable {
    public let name: String
    public let removed: [String]
    public let absent: [String]
}

/// A typed MCP command line as command + args, split like a shell: quotes
/// group and unquote, backslash escapes outside double quotes also escape
/// `"` `\` `$` ``` ` ``` inside, and nil means an unterminated quote.
public func mcpCommandWords(_ line: String) -> [String]? {
    var words: [String] = []
    var word = ""
    var open = false
    var quote: Character = " "
    var chars = line[line.startIndex...]
    while let ch = chars.first {
        chars = chars.dropFirst()
        switch ch {
        case "\\":
            if quote == "'" {
                // \ is literal inside '…' and doesn't escape: 'C:\' is a
                // complete quoted word ending on its own quote.
                word += "\\"
                open = true
                continue
            }
            guard let next = chars.first else { word += "\\"; continue }
            chars = chars.dropFirst()
            if quote == "\"" && next != "\"" && next != "\\"
                && next != "$" && next != "`" {
                word += "\\" + String(next)
            } else {
                word += String(next)
            }
            open = true
        case "'":
            if quote == "\"" { word += "'"; open = true; continue }
            if quote == "'" { quote = " " } else if !open || quote == " " { quote = "'" }
            open = true
        case "\"":
            if quote == "'" { word += "\""; open = true; continue }
            if quote == "\"" { quote = " " } else if quote == " " { quote = "\"" }
            open = true
        case " ", "\t", "\n":
            // The `where` on a multi-pattern case binds only the last
            // pattern, so the quote check lives in the body.
            if quote == " " {
                if open { words.append(word); word = ""; open = false }
            } else {
                word.append(ch)
                open = true
            }
        default:
            word.append(ch)
            open = true
        }
    }
    if quote != " " { return nil }
    if open { words.append(word) }
    return words
}

/// Whether a flag or key name looks like it holds a credential.
public func mcpIsSecretName(_ name: String) -> Bool {
    McpRedaction.isSecretName(name)
}

/// The ${NAME} an env var or header stores its value under:
/// SERVER_KEY in caps, runs of anything but ASCII letters and digits
/// collapsed to one `_`, and `MCP_` prepended when it would start with a
/// digit. set_secret takes only `[A-Za-z_][A-Za-z0-9_]*`, so an accented
/// server name (`café`) must not reach it as `CAFÉ_KEY`. Server-scoped,
/// so two servers can't collide.
public func mcpSecretName(server: String, key: String) -> String {
    var name = (server + "_" + key)
        .map { $0.isASCII && ($0.isLetter || $0.isNumber) ? Character($0.uppercased()) : "_" }
        .reduce(into: "") { out, ch in if ch != "_" || out.last != "_" { out.append(ch) } }
        .trimmingCharacters(in: CharacterSet(charactersIn: "_"))
    if name.isEmpty { return "MCP_SECRET" }
    if name.first?.isNumber == true { name = "MCP_" + name }
    return name
}

/// The face of an add sheet's "Add to" dropdown: "4 of 10 harnesses".
/// Counts only picks that are still in the list.
public func mcpTargetsSummary(_ targets: Set<String>, of harnesses: [String]) -> String {
    let picked = harnesses.filter(targets.contains).count
    return "\(picked) of \(harnesses.count) \(harnesses.count == 1 ? "harness" : "harnesses")"
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
    /// The source choices a sheet offers: each definition group, plus the
    /// hub when mcp.json holds the server.
    public func sources(for server: McpServer) -> [String] {
        var out = server.groups.map { $0.first ?? "" }.filter { !$0.isEmpty }
        if server.inHub && !out.contains("quiver") { out.append("quiver") }
        return out
    }

    public func targets(for server: McpServer, from source: String, in harnesses: [String]) -> SyncTargets {
        // A config that launches local servers only can't take a URL one.
        let harnesses = server.isRemote(from: source) ? harnesses.filter { !McpServer.localOnly.contains($0) } : harnesses
        if source == "quiver" {
            // The hub isn't a matrix column: every harness lacks it, and
            // one with its own copy gets it replaced only with approval.
            var missing: [String] = []
            var differing: [String] = []
            for harness in harnesses {
                if server.harnesses.contains(harness) { differing.append(harness) }
                else { missing.append(harness) }
            }
            return SyncTargets(missing: missing, differing: differing)
        }
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
