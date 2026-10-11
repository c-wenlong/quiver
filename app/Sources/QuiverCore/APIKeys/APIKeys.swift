import Foundation
import Observation

public enum APIKeysError: Error, Equatable, LocalizedError {
    case invalidName(String)
    case invalidProvider
    case duplicate(String)
    case multilineValue
    case notFound(String)
    case changedOnDisk
    case unreadable(String)
    case unwritable(String)

    public var errorDescription: String? {
        switch self {
        case .invalidName(let name):
            "\(name) isn't a valid variable name. Use letters, digits and underscores, not starting with a digit."
        case .invalidProvider:
            "A provider name has to be a short label: up to 60 letters, digits, spaces and ( ) . , & / -."
        case .duplicate(let name): "\(name) is exported more than once in the file. Remove the extra line in an editor, then edit it here."
        case .multilineValue: "A key can't span more than one line."
        case .notFound(let name): "\(name) isn't in the file any more."
        case .changedOnDisk: "The file changed since Quiver opened it. Reload to see the new version, then make the change again."
        case .unreadable(let detail): "Couldn't read the keys file: \(detail)"
        case .unwritable(let detail): "Couldn't save the keys file: \(detail)"
        }
    }
}

/// A shell file of `export NAME="value"` lines grouped under `# PROVIDER`
/// comments, as `~/.api_keys` is. Edits rewrite only the lines they touch;
/// every other line, comment and blank line comes back exactly as it was.
public struct APIKeysFile: Equatable, Sendable {
    public struct Entry: Equatable, Sendable, Identifiable {
        public var id: String { name }
        public var provider: String
        public var name: String
        public var value: String
        /// Zero-based line in the file.
        var line: Int
    }

    /// A line that looks like a key but sets nothing.
    public struct Problem: Equatable, Sendable {
        /// One-based, as an editor shows it.
        public var line: Int
        public var text: String
        public var reason: String
    }

    private var lines: [String]
    private var endsWithNewline: Bool

    public init(text: String) {
        endsWithNewline = text.hasSuffix("\n")
        var lines = text.components(separatedBy: "\n")
        if endsWithNewline { lines.removeLast() }
        self.lines = text.isEmpty ? [] : lines
    }

    public var text: String {
        lines.joined(separator: "\n") + (endsWithNewline && !lines.isEmpty ? "\n" : "")
    }

    // MARK: Reading

    public var entries: [Entry] {
        var provider = ""
        var entries: [Entry] = []
        for (index, line) in lines.enumerated() {
            if let heading = Self.heading(line) {
                provider = heading
            } else if let (name, value) = Self.export(line) {
                entries.append(Entry(provider: provider, name: name, value: value, line: index))
            }
        }
        return entries
    }

    /// One per variable: the value a shell ends up with, which for a name
    /// exported twice is the later line.
    public var keys: [Entry] {
        var last: [String: Entry] = [:]
        for entry in entries { last[entry.name] = entry }
        var seen = Set<String>()
        return entries.compactMap { entry in
            guard seen.insert(entry.name).inserted else { return nil }
            return last[entry.name]
        }
    }

    /// Lines that set nothing, set a key Quiver can't edit safely, or are
    /// overridden later in the file. Values are always hidden.
    public var problems: [Problem] {
        let all = entries
        var lastLine: [String: Int] = [:]
        for entry in all { lastLine[entry.name] = entry.line }
        var problems: [Problem] = lines.enumerated().compactMap { index, line in
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.range(of: #"^EXPORT\s+[A-Za-z_]"#, options: .regularExpression) != nil {
                return Problem(line: index + 1, text: Self.redacted(line),
                               reason: "zsh only accepts lowercase export, so this line sets nothing")
            }
            if Self.commentedAssignment(line) != nil || (Self.heading(line) == nil && Self.commentedKey(line)) {
                return Problem(line: index + 1, text: Self.redacted(line),
                               reason: "a commented-out key still holds a value; delete the line")
            }
            if trimmed.hasPrefix("export "), Self.export(line) == nil {
                return Problem(line: index + 1, text: Self.redacted(line),
                               reason: "Quiver can't edit this line safely, so it leaves it alone")
            }
            return nil
        }
        for entry in all where lastLine[entry.name] != entry.line {
            problems.append(Problem(
                line: entry.line + 1, text: Self.redacted(lines[entry.line]),
                reason: "\(entry.name) is exported again on line \((lastLine[entry.name] ?? 0) + 1), which wins; remove one of them"))
        }
        return problems.sorted { $0.line < $1.line }
    }

    // MARK: Editing

    /// Sets a key, in place if it exists, else at the end of its provider's
    /// group, else in a new group at the end of the file.
    public mutating func set(name: String, value: String, provider: String) throws {
        guard Self.isValidName(name) else { throw APIKeysError.invalidName(name) }
        guard !value.contains(where: \.isNewline) else { throw APIKeysError.multilineValue }
        guard !provider.contains(where: \.isNewline) else { throw APIKeysError.invalidProvider }
        let line = Self.render(name: name, value: value)
        let all = entries
        guard all.filter({ $0.name == name }).count <= 1 else { throw APIKeysError.duplicate(name) }
        if let existing = all.first(where: { $0.name == name }) {
            lines[existing.line] = line
            return
        }
        let provider = provider.trimmingCharacters(in: .whitespaces)
        if let last = all.last(where: { $0.provider == provider }) {
            lines.insert(line, at: last.line + 1)
            return
        }
        // A heading the file wouldn't read back would file the key under
        // whichever provider came before it.
        guard Self.isLabel(provider) else { throw APIKeysError.invalidProvider }
        if !lines.isEmpty, lines.last?.isEmpty == false { lines.append("") }
        lines.append("# \(provider)")
        lines.append(line)
        // A new file ends with a newline, as an editor would leave it.
        if lines.count == 2 { endsWithNewline = true }
    }

    /// Removes a key, and its provider's heading once nothing is left under it.
    public mutating func remove(name: String) throws {
        let matches = entries.filter { $0.name == name }
        guard matches.count <= 1 else { throw APIKeysError.duplicate(name) }
        guard let entry = matches.first else { throw APIKeysError.notFound(name) }
        let siblings = entries.filter { $0.provider == entry.provider && $0.name != name }
        lines.remove(at: entry.line)
        guard siblings.isEmpty, !entry.provider.isEmpty,
              let heading = lines[..<min(entry.line, lines.count)].lastIndex(where: { Self.heading($0) == entry.provider })
        else { return }
        lines.remove(at: heading)
        // Leave one blank line between groups, not two.
        if heading > 0, heading < lines.count, lines[heading - 1].isEmpty, lines[heading].isEmpty {
            lines.remove(at: heading)
        }
    }

    // MARK: Syntax

    static func isValidName(_ name: String) -> Bool {
        name.range(of: #"^[A-Za-z_][A-Za-z0-9_]*$"#, options: .regularExpression) != nil
    }

    /// A comment line's text, used as the provider for the keys below it.
    /// The provider shows in the table, VoiceOver labels and menus, so only
    /// a comment that reads as a label counts. Anything else (a commented-out
    /// key in any shape, a URL, a divider) neither names nor resets it.
    static func heading(_ line: String) -> String? {
        guard let text = commentText(line), isLabel(text) else { return nil }
        return text
    }

    /// Short words of letters and digits with light punctuation: "Anthropic",
    /// "Kimi (Moonshot AI)", "ZAI (01.ai)". A key is one long word, and a
    /// commented-out line has `=`, `:`, quotes or an `export NAME`.
    static func isLabel(_ text: String) -> Bool {
        guard !text.isEmpty, text.count <= 60, text.contains(where: \.isLetter),
              text.allSatisfy({ $0.isLetter || $0.isNumber || $0 == " " || "().,&/-".contains($0) }),
              !text.split(separator: " ").contains(where: { $0.count >= 16 }),
              !text.contains(/\b(?i:export)\s+[A-Za-z_]/)
        else { return false }
        return true
    }

    /// A comment's text without its `#` marks, or nil for a line that isn't one.
    private static func commentText(_ line: String) -> String? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("#") else { return nil }
        return trimmed.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)
    }

    /// A comment holding a key in a shape other than `NAME=value`:
    /// `# export NAME value`, `# NAME: value`, a bare key, or a key in prose.
    static func commentedKey(_ line: String) -> Bool {
        guard let text = commentText(line) else { return false }
        if text.contains(/^(?i:export)\s+[A-Z_][A-Z0-9_]*(?![a-z0-9_])\s*\S/) { return true }
        return text.matches(of: /[A-Za-z0-9_\-]{16,}/).contains { SecretText.looksLikeKey($0.output) }
    }

    /// The variable name of a commented-out assignment, or nil.
    static func commentedAssignment(_ line: String) -> String? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("#") else { return nil }
        let text = trimmed.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)
        guard let match = text.firstMatch(of: /^(?:(?i:export)\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=/) else { return nil }
        return String(match.1)
    }

    /// `export NAME=value`, with the value double-quoted, single-quoted or bare.
    static func export(_ line: String) -> (String, String)? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard let match = trimmed.firstMatch(of: /^export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$/) else { return nil }
        let name = String(match.1)
        let raw = String(match.2).trimmingCharacters(in: .whitespaces)
        guard let value = unquote(raw) else { return nil }
        return (name, value)
    }

    /// The value, or nil when the line carries anything after it other than
    /// a comment (`; export B=…`, `"a""b"`): saving such a line would drop it.
    static func unquote(_ raw: String) -> String? {
        var value = ""
        var rest: Substring
        if raw.hasPrefix("\"") {
            var escaped = false
            var closed: String.Index?
            var index = raw.index(after: raw.startIndex)
            while index < raw.endIndex {
                let character = raw[index]
                if escaped {
                    // In double quotes only these four take a backslash.
                    if !"\"\\$`".contains(character) { value.append("\\") }
                    value.append(character)
                    escaped = false
                } else if character == "\\" {
                    escaped = true
                } else if character == "\"" {
                    closed = index
                    break
                } else {
                    value.append(character)
                }
                index = raw.index(after: index)
            }
            guard let closed else { return nil }
            rest = raw[raw.index(after: closed)...]
        } else if raw.hasPrefix("'") {
            let body = raw.dropFirst()
            guard let end = body.firstIndex(of: "'") else { return nil }
            value = String(body[..<end])
            rest = body[body.index(after: end)...]
        } else {
            let end = raw.firstIndex(where: \.isWhitespace) ?? raw.endIndex
            value = String(raw[..<end])
            guard !value.contains(where: { "$`\"'\\;&|<>()".contains($0) }) else { return nil }
            rest = raw[end...]
        }
        let trailing = rest.trimmingCharacters(in: .whitespaces)
        guard trailing.isEmpty || (trailing.hasPrefix("#") && rest.first?.isWhitespace == true) else { return nil }
        return value
    }

    static func render(name: String, value: String) -> String {
        var escaped = ""
        for character in value {
            if "\"\\$`".contains(character) { escaped.append("\\") }
            escaped.append(character)
        }
        return "export \(name)=\"\(escaped)\""
    }

    /// The line with its value hidden, for messages: only the comment mark,
    /// the `export` keyword and the variable name survive, never what follows
    /// the name, and not the name either when it looks like a key itself.
    static func redacted(_ line: String) -> String {
        let shape = /^(\s*(?:#+\s*)?(?:(?i:export)\s+)?)([A-Za-z_][A-Za-z0-9_]*)?(\s*=)?/
        guard let match = line.firstMatch(of: shape) else { return "…" }
        let prefix = String(match.output.1)
        guard let name = match.output.2, !SecretText.looksLikeKey(name) else { return prefix + "…" }
        return prefix + name + (match.output.3 ?? "") + "…"
    }
}

/// Telling a secret apart from a name or a label.
enum SecretText {
    /// A run like `sk-ant-api03-…`, `AKIA…` or `ghp_…`: 16 or more characters
    /// mixing letters and digits. An upper-case name with underscores such as
    /// `CLOUDFLARE_R2_ACCESS_KEY_ID` is a variable name, not a key.
    static func looksLikeKey(_ text: Substring) -> Bool {
        guard text.count >= 16, text.contains(where: \.isNumber), text.contains(where: \.isLetter) else { return false }
        return text.contains(where: \.isLowercase) || !text.contains("_")
    }

    /// `text` with every key-like run replaced by `…`.
    static func maskingKeys(_ text: Substring) -> String {
        var out = ""
        var rest = text
        while let match = rest.firstMatch(of: /[A-Za-z0-9_\-]{16,}/) {
            out += rest[..<match.range.lowerBound]
            out += looksLikeKey(match.output) ? "…" : String(match.output)
            rest = rest[match.range.upperBound...]
        }
        return out + rest
    }
}

/// How a key is shown until it's revealed.
public enum APIKeyMask {
    public static func mask(_ value: String) -> String {
        guard !value.isEmpty else { return "" }
        let dots = "••••••••"
        guard value.count >= 16 else { return dots }
        // A vendor prefix like `sk-ant-` says which kind of key it is and
        // gives nothing away; keep up to the last dash in the first eight.
        let head = value.prefix(8)
        let prefix = head.lastIndex(of: "-").map { String(head[...$0]) } ?? ""
        return prefix + dots + String(value.suffix(4))
    }
}

/// Reads and writes the keys file.
public struct APIKeysStore: Sendable {
    /// What the file looked like when it was read, to detect outside edits.
    public struct Stamp: Equatable, Sendable {
        var content: Data?
    }

    public let url: URL
    public let backupDirectory: URL
    /// Runs after the new files are prepared, just before the final check
    /// and swap; tests use it to edit the file at the worst moment.
    var beforeReplace: (@Sendable () -> Void)?

    public init(url: URL, backupDirectory: URL) {
        self.url = url
        self.backupDirectory = backupDirectory
    }

    /// The real file behind a symlink, so a save doesn't replace the link.
    var target: URL { url.resolvingSymlinksInPath() }

    public func load() throws -> (APIKeysFile, Stamp) {
        let data: Data
        do {
            data = try Data(contentsOf: target)
        } catch CocoaError.fileReadNoSuchFile {
            return (APIKeysFile(text: ""), Stamp(content: nil))
        } catch {
            throw APIKeysError.unreadable(error.localizedDescription)
        }
        // Lossy decoding would rewrite bytes on lines no one edited.
        guard let text = String(data: data, encoding: .utf8) else {
            throw APIKeysError.unreadable("the file isn't valid UTF-8, so Quiver won't rewrite it")
        }
        return (APIKeysFile(text: text), Stamp(content: data))
    }

    /// Writes `file` atomically with mode 600, after copying the previous
    /// version to the backup, unless the file changed since `stamp`. Both
    /// files are prepared first and the file is checked again just before
    /// they're swapped in, so an edit made meanwhile isn't overwritten.
    public func save(_ file: APIKeysFile, over stamp: Stamp) throws {
        let target = target
        guard (try? Data(contentsOf: target)) == stamp.content else { throw APIKeysError.changedOnDisk }
        let manager = FileManager.default
        var prepared: [URL] = []
        defer { for url in prepared { try? manager.removeItem(at: url) } }
        do {
            let backup = backupDirectory.appendingPathComponent("api_keys.bak")
            var backupTemp: URL?
            if let previous = stamp.content {
                try manager.createDirectory(at: backupDirectory, withIntermediateDirectories: true,
                                            attributes: [.posixPermissions: 0o700])
                // It may already exist with looser permissions; it holds secrets.
                try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: backupDirectory.path)
                backupTemp = try preparePrivately(previous, beside: backup)
                prepared.append(backupTemp!)
            }
            try manager.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            let targetTemp = try preparePrivately(Data(file.text.utf8), beside: target)
            prepared.append(targetTemp)

            beforeReplace?()
            guard (try? Data(contentsOf: target)) == stamp.content else { throw APIKeysError.changedOnDisk }
            if let backupTemp { try swap(backupTemp, into: backup) }
            try swap(targetTemp, into: target)
            prepared.removeAll()
        } catch let error as APIKeysError {
            throw error
        } catch {
            throw APIKeysError.unwritable(error.localizedDescription)
        }
    }

    /// A temp file created 600 beside `destination`, so the secret is never
    /// readable by others even for a moment.
    private func preparePrivately(_ data: Data, beside destination: URL) throws -> URL {
        let temp = destination.deletingLastPathComponent()
            .appendingPathComponent(".\(destination.lastPathComponent).quiver-\(UUID().uuidString)")
        guard FileManager.default.createFile(atPath: temp.path, contents: nil,
                                             attributes: [.posixPermissions: 0o600]) else {
            throw APIKeysError.unwritable("couldn't create a temporary file in \(temp.deletingLastPathComponent().path)")
        }
        do {
            let handle = try FileHandle(forWritingTo: temp)
            defer { try? handle.close() }
            try handle.write(contentsOf: data)
        } catch {
            // A half-written temp file may hold some of the keys.
            try? FileManager.default.removeItem(at: temp)
            throw error
        }
        return temp
    }

    private func swap(_ temp: URL, into destination: URL) throws {
        guard rename(temp.path, destination.path) == 0 else {
            throw APIKeysError.unwritable(String(cString: strerror(errno)))
        }
    }
}

/// The environment a new terminal would get.
public protocol ShellEnvironmentReading: Sendable {
    /// Nil when the shell couldn't be read.
    func freshEnvironment() async -> [String: String]?
}

public struct LoginShellEnvironment: ShellEnvironmentReading {
    public init() {}
    public func freshEnvironment() async -> [String: String]? {
        let base = ProcessInfo.processInfo.environment
        return await LoginShell.environment(shell: base["SHELL"] ?? "/bin/zsh", base: base, timeout: .seconds(8))
    }
}

/// The API keys page.
@MainActor
@Observable
public final class APIKeysModel {
    public enum Status: Sendable, Equatable {
        /// A new terminal gets this value.
        case loaded
        /// A new terminal gets a different value: something later in the
        /// shell's startup sets it again.
        case overridden
        /// A new terminal doesn't have it: the file isn't sourced, or the line is wrong.
        case notLoaded
        /// The shell couldn't be read.
        case unknown
        /// The shell is still being read.
        case checking
    }

    public struct Row: Identifiable, Equatable, Sendable {
        public var id: String { name }
        public var provider: String
        public var name: String
        public var value: String
        public var status: Status
        public var masked: String { APIKeyMask.mask(value) }
    }

    public var search = ""
    public private(set) var rows: [Row] = []
    public private(set) var problems: [APIKeysFile.Problem] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    public private(set) var savedAt: Date?

    public var path: String { store.url.path }

    private let store: APIKeysStore
    private let shell: ShellEnvironmentReading
    private var file = APIKeysFile(text: "")
    private var stamp = APIKeysStore.Stamp(content: nil)
    private var environment: [String: String]?
    private var shellRead = false

    public init(store: APIKeysStore, shell: ShellEnvironmentReading) {
        self.store = store
        self.shell = shell
    }

    public var providers: [String] {
        var seen: [String] = []
        for row in rows where !seen.contains(row.provider) { seen.append(row.provider) }
        return seen
    }

    public var visible: [Row] {
        let needle = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return rows }
        return rows.filter { $0.provider.lowercased().contains(needle) || $0.name.lowercased().contains(needle) }
    }

    /// Reads the file, then what a new shell sees.
    public func load() async {
        isLoading = true
        defer { isLoading = false }
        readFile()
        // Show the file now; the shell can take seconds to start.
        shellRead = false
        rebuild()
        environment = await shell.freshEnvironment()
        shellRead = true
        rebuild()
    }

    public func set(name: String, value: String, provider: String) async throws {
        var edited = file
        try edited.set(name: name, value: value, provider: provider)
        try write(edited)
    }

    public func remove(name: String) async throws {
        var edited = file
        try edited.remove(name: name)
        try write(edited)
    }

    private func write(_ edited: APIKeysFile) throws {
        try store.save(edited, over: stamp)
        savedAt = Date()
        readFile()
        // The shell is read again on the next load; until then a changed key
        // shows what the file now says against the last shell reading.
        rebuild()
    }

    private func readFile() {
        do {
            (file, stamp) = try store.load()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func rebuild() {
        problems = file.problems
        rows = file.keys.map { entry in
            let status: Status
            if !shellRead {
                status = .checking
            } else if let environment {
                switch environment[entry.name] {
                case entry.value: status = .loaded
                case nil: status = .notLoaded
                default: status = .overridden
                }
            } else {
                status = .unknown
            }
            return Row(provider: entry.provider.isEmpty ? "Other" : entry.provider, name: entry.name,
                       value: entry.value, status: status)
        }
    }
}
