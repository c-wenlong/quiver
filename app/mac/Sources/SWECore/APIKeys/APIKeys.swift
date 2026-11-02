import Foundation
import Observation

public enum APIKeysError: Error, Equatable, LocalizedError {
    case invalidName(String)
    case invalidProvider
    case duplicate(String)
    /// The name is also set on a line SWE can't edit safely, which
    /// may be the one the shell ends up with.
    case unsafeLine(String)
    /// The name a rename asked for already belongs to another export.
    case nameTaken(String)
    /// The provider a rename asked for already heads another group.
    case providerTaken(String)
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
        case .unsafeLine(let name): "\(name) is also set on a line SWE can't edit safely. Change or remove that line in an editor, then edit it here."
        case .nameTaken(let name): "\(name) is already exported; pick another variable name."
        case .providerTaken(let name): "\(name) already exists; move keys into it instead."
        case .multilineValue: "A key can't span more than one line."
        case .notFound(let name): "\(name) isn't in the file any more."
        case .changedOnDisk: "The file changed since SWE opened it. Reload to see the new version, then make the change again."
        case .unreadable(let detail): "Couldn't read the keys file: \(detail)"
        case .unwritable(let detail): "Couldn't save the keys file: \(detail)"
        }
    }
}

/// A shell file of `export NAME="value"` lines grouped under `# PROVIDER`
/// comments, as the API keys file is. Edits rewrite only the lines they touch;
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
    /// exported twice is the later line. A name whose last assignment is a
    /// line SWE can't read has no known value, so it isn't listed.
    public var keys: [Entry] {
        var last: [String: Entry] = [:]
        for entry in entries { last[entry.name] = entry }
        let unsafe = unsafeLines
        var seen = Set<String>()
        return entries.compactMap { entry in
            guard seen.insert(entry.name).inserted, let winner = last[entry.name],
                  (unsafe[entry.name] ?? -1) < winner.line else { return nil }
            return winner
        }
    }

    /// The last line index of each name assigned by an `export` line
    /// SWE can't edit safely (`export A="$OTHER"`, `export A=x; …`).
    var unsafeLines: [String: Int] {
        var found: [String: Int] = [:]
        for (index, line) in lines.enumerated() where Self.export(line) == nil {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if let match = trimmed.firstMatch(of: /^export\s+([A-Za-z_][A-Za-z0-9_]*)=/) {
                found[String(match.1)] = index
            }
        }
        return found
    }

    /// Refuses an edit to `name` while an unsafe line also sets it: the
    /// edit could leave a second assignment the shell reads instead.
    private func refuseUnsafe(_ name: String) throws {
        guard unsafeLines[name] == nil else { throw APIKeysError.unsafeLine(name) }
    }

    /// Lines that set nothing, set a key SWE can't edit safely, or are
    /// overridden later in the file. Values are always hidden.
    public var problems: [Problem] {
        let all = entries
        var lastLine: [String: Int] = [:]
        for entry in all { lastLine[entry.name] = entry.line }
        // An unsafe line later in the file wins over an editable one too.
        for (name, line) in unsafeLines where line > (lastLine[name] ?? -1) {
            lastLine[name] = line
        }
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
                               reason: "SWE can't edit this line safely, so it leaves it alone")
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
        let existing = all.first(where: { $0.name == name })
        // A value set in place still wins when it comes after the unsafe
        // line; a new line, or one the unsafe line overrides, wouldn't.
        if let unsafe = unsafeLines[name], (existing?.line ?? -1) < unsafe {
            throw APIKeysError.unsafeLine(name)
        }
        if let existing {
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
        try refuseUnsafe(name)
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

    /// Renames a key where it sits, keeping the line's value and position.
    /// The new name has to be a valid variable name not already exported.
    public mutating func rename(name: String, to newName: String) throws {
        guard Self.isValidName(newName) else { throw APIKeysError.invalidName(newName) }
        let all = entries
        guard all.filter({ $0.name == name }).count <= 1 else { throw APIKeysError.duplicate(name) }
        guard let entry = all.first(where: { $0.name == name }) else { throw APIKeysError.notFound(name) }
        // Renaming a key to its own name is a no-op, once it is known to exist.
        guard newName != name else { return }
        guard !all.contains(where: { $0.name == newName }), unsafeLines[newName] == nil else {
            throw APIKeysError.nameTaken(newName)
        }
        try refuseUnsafe(name)
        // Only the name changes: the line keeps its quoting, its value
        // expression and any trailing comment byte-for-byte.
        let line = lines[entry.line]
        guard let match = line.firstMatch(of: /^(\s*export\s+)[A-Za-z_][A-Za-z0-9_]*/) else {
            throw APIKeysError.notFound(name)
        }
        lines[entry.line] = String(match.1) + newName + line[match.range.upperBound...]
    }

    /// Moves a key to the end of an existing provider's group, and starts
    /// a new group near where the key left when the provider isn't in the
    /// file yet: a rename in place when the key was its group's only one,
    /// right after the old group when it wasn't, or where the ungrouped
    /// block ends for an ungrouped key. A move to its own provider does
    /// nothing, and the old heading goes once its group empties.
    public mutating func move(name: String, toProvider provider: String) throws {
        guard !provider.contains(where: \.isNewline) else { throw APIKeysError.invalidProvider }
        let provider = provider.trimmingCharacters(in: .whitespaces)
        let all = entries
        guard all.filter({ $0.name == name }).count <= 1 else { throw APIKeysError.duplicate(name) }
        guard let entry = all.first(where: { $0.name == name }) else { throw APIKeysError.notFound(name) }
        try refuseUnsafe(name)
        guard entry.provider != provider else { return }
        // A group that isn't in the file needs a label it would read
        // back; check before any line moves, so a refused move leaves
        // the file alone.
        // A provider counts as known when its group or even just its
        // heading is in the file: an empty group is a real destination.
        let providerKnown = all.contains { $0.provider == provider }
            || lines.contains { Self.heading($0) == provider }
        if !providerKnown {
            guard Self.isLabel(provider) else { throw APIKeysError.invalidProvider }
        }
        // Its group's only key leaving for a group that does not exist
        // is a provider rename: rewrite the heading where it sits so the
        // group keeps its place in the file.
        if !providerKnown, !entry.provider.isEmpty,
           !all.contains(where: { $0.provider == entry.provider && $0.name != name }) {
            try renameProvider(from: entry.provider, to: provider)
            return
        }
        let line = lines[entry.line]
        lines.remove(at: entry.line)
        if !entry.provider.isEmpty, !entries.contains(where: { $0.provider == entry.provider }),
           let heading = lines[..<min(entry.line, lines.count)].lastIndex(where: { Self.heading($0) == entry.provider }) {
            lines.remove(at: heading)
            // Leave one blank line between groups, not two.
            if heading > 0, heading < lines.count, lines[heading - 1].isEmpty, lines[heading].isEmpty {
                lines.remove(at: heading)
            } else if heading == 0, lines.first?.isEmpty == true {
                // The emptied group was the file's first: no stray leading blank.
                lines.removeFirst()
            }
        }
        if let last = entries.last(where: { $0.provider == provider }) {
            lines.insert(line, at: last.line + 1)
            return
        }
        // An empty group still counts: the key lands right after its heading.
        if let heading = lines.lastIndex(where: { Self.heading($0) == provider }) {
            lines.insert(line, at: heading + 1)
            return
        }
        // The provider isn't in the file, so the new group goes where
        // the key's old block ends: right after the old group's last key
        // when the group survived, or after the last ungrouped key, which
        // is where the key sat when it was the last of them.
        if let last = entries.last(where: { $0.provider == entry.provider }) {
            lines.insert(contentsOf: ["", "# \(provider)", line], at: last.line + 1)
            return
        }
        // The key was the ungrouped block's only line: the new group
        // takes that block's place, after the leading comments and
        // before the first heading (or at the end when there is none).
        if let first = lines.indices.first(where: { Self.heading(lines[$0]) != nil }) {
            lines.insert(contentsOf: ["# \(provider)", line, ""], at: first)
        } else {
            if !lines.isEmpty, lines.last?.isEmpty == false { lines.append("") }
            lines.append("# \(provider)")
            lines.append(line)
        }
    }

    /// Renames a provider: every heading reading exactly `from` becomes
    /// `# <to>`. The new label has to read back as a heading and not
    /// already name another group.
    public mutating func renameProvider(from: String, to newProvider: String) throws {
        let newProvider = newProvider.trimmingCharacters(in: .whitespaces)
        guard newProvider != from else { return }
        guard Self.isLabel(newProvider) else { throw APIKeysError.invalidProvider }
        let headings = lines.indices.filter { Self.heading(lines[$0]) == from }
        guard !headings.isEmpty else { throw APIKeysError.notFound(from) }
        guard !lines.contains(where: { Self.heading($0) == newProvider }) else {
            throw APIKeysError.providerTaken(newProvider)
        }
        for index in headings { lines[index] = "# \(newProvider)" }
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
    public static func isLabel(_ text: String) -> Bool {
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
    /// Nil too for a double-quoted `$` or backtick the shell expands
    /// (`"$OTHER"`): the text isn't the value, and saving it back escaped
    /// would turn the reference into a literal.
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
                } else if character == "$" || character == "`" {
                    return nil
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
    /// Vendor prefixes that mark a key whatever follows them.
    static let keyPrefixes = ["sk-", "sk_", "pk_", "rk_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_",
                              "glpat-", "xoxa-", "xoxb-", "xoxp-", "xoxs-", "xapp-", "AIza", "AKIA", "ASIA",
                              "hf_", "gsk_", "xai-", "pplx-", "r8_", "npm_"]

    /// A run of 16 or more characters like `sk-ant-api03-…`, `AKIA…` or
    /// `ghp_…`: a vendor prefix, mixed case, or digits among letters. An
    /// upper-case name with underscores such as `CLOUDFLARE_R2_ACCESS_KEY_ID`
    /// is a variable name, and an all-lower-case word is a package or path.
    static func looksLikeKey(_ text: Substring) -> Bool {
        guard text.count >= 16, text.contains(where: \.isLetter) else { return false }
        if keyPrefixes.contains(where: { text.hasPrefix($0) }) { return true }
        let lower = text.contains(where: \.isLowercase)
        let upper = text.contains(where: \.isUppercase)
        let digit = text.contains(where: \.isNumber)
        return (lower && upper) || (lower && digit) || (digit && !text.contains("_"))
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

/// Which logo stands for a provider heading, from `ModelBrands`. Headings
/// are free-form ("# ANTHROPIC", "# Moonshot / Kimi", "# x.ai"), so this
/// matches whole words, ignoring case, and gives nil when no brand with a
/// bundled logo is named.
public enum APIKeyProviderLogo {
    public static func logoID(for provider: String) -> String? {
        guard let icon = ModelBrands.inText(provider)?.icon, HarnessIcons.icon(for: icon) != nil else {
            return nil
        }
        return icon
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

    /// A folder that must never be versioned, such as ~/.swe/secrets.
    /// A save into it first gives it its own `.gitignore` of `*`, which git
    /// lets win over whatever the hub's root `.gitignore` says.
    public let sealedFolder: URL?

    public init(url: URL, backupDirectory: URL, sealedFolder: URL? = nil) {
        self.url = url
        self.backupDirectory = backupDirectory
        self.sealedFolder = sealedFolder
    }

    /// The same text `swe init` writes (`paths.SECRETS_GITIGNORE_BODY`).
    static let sealBody = "# Credential values. Never versioned, whatever ../.gitignore says.\n*\n"

    /// ~/.swe/secrets, where the default keys file lives.
    public static func secretsFolder(home: URL) -> URL {
        home.appending(path: ".swe/secrets")
    }

    /// ~/.swe/secrets/providers/.api_keys, or ~/.api_keys on a machine
    /// `swe init` has not migrated yet. After the move ~/.api_keys is a
    /// symlink to the new file, so either path edits the same keys.
    public static func defaultURL(home: URL) -> URL {
        let current = home.appending(path: ".swe/secrets/providers/.api_keys")
        let legacy = home.appending(path: ".api_keys")
        let manager = FileManager.default
        let currentIsThere = manager.fileExists(atPath: current.path)
            || (try? manager.destinationOfSymbolicLink(atPath: current.path)) != nil
        // A directory layout (~/.api_keys/<provider>) is legal to the CLI
        // but not to this file-format: a directory is not the store.
        var legacyIsDirectory = ObjCBool(false)
        let legacyIsFile = manager.fileExists(atPath: legacy.path,
                                              isDirectory: &legacyIsDirectory)
            && !legacyIsDirectory.boolValue
        return !currentIsThere && legacyIsFile ? legacy : current
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
            throw APIKeysError.unreadable("the file isn't valid UTF-8, so SWE won't rewrite it")
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
            try seal(around: target)
            // Private like the file: on a fresh machine this creates secrets/.
            try manager.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true,
                                        attributes: [.posixPermissions: 0o700])
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

    private func seal(around target: URL) throws {
        guard let folder = sealedFolder?.resolvingSymlinksInPath(),
              target.path.hasPrefix(folder.path + "/") else { return }
        let manager = FileManager.default
        try manager.createDirectory(at: folder, withIntermediateDirectories: true,
                                    attributes: [.posixPermissions: 0o700])
        let seal = folder.appending(path: ".gitignore")
        guard (try? String(contentsOf: seal, encoding: .utf8)) != Self.sealBody else { return }
        try Self.sealBody.write(to: seal, atomically: true, encoding: .utf8)
    }

    /// A temp file created 600 beside `destination`, so the secret is never
    /// readable by others even for a moment.
    private func preparePrivately(_ data: Data, beside destination: URL) throws -> URL {
        let temp = destination.deletingLastPathComponent()
            .appendingPathComponent(".\(destination.lastPathComponent).swe-\(UUID().uuidString)")
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
        /// The heading this key sits under; "" for keys before the first
        /// heading, which have no provider at all.
        public var provider: String
        /// What the group column shows; "Other" is only how the ungrouped
        /// read, never a value to write back.
        public var providerLabel: String { provider.isEmpty ? "Other" : provider }
        public var name: String
        public var value: String
        public var status: Status
        public var masked: String { APIKeyMask.mask(value) }
    }

    /// Whether the shell's startup file reads the keys file, so a new
    /// terminal picks changes up. `.unknown` until the first `load()`.
    public enum Sourcing: Equatable {
        case unknown
        case sourced
        /// The startup file that lacks the line.
        case notSourced(file: URL)
        /// The startup file lacks the line and can't be written: `file`
        /// is the link the shell reads, `resolved` the read-only target
        /// (a Nix store path, a read-only checkout).
        case notWritable(file: URL, resolved: URL)
        /// No startup file is known for this shell name.
        case unsupportedShell(String)
    }

    public var search = ""
    public private(set) var rows: [Row] = []
    public private(set) var sourcing: Sourcing = .unknown
    public private(set) var problems: [APIKeysFile.Problem] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    public private(set) var savedAt: Date?

    /// The latest check per key name, read back from the cache at init
    /// and kept until a newer check replaces it.
    public private(set) var checks: [String: APIKeyCheck] = [:]
    /// The names a check is running or queued for.
    public private(set) var checking: Set<String> = []
    /// Any check is still running.
    public var isChecking: Bool { !checking.isEmpty }

    public var path: String { store.url.path }

    private static let checkLimit = 4

    private let store: APIKeysStore
    private let shell: ShellEnvironmentReading
    /// The login shell name and environment (for $SHELL and ZDOTDIR) the
    /// sourcing check reads; injectable so tests point at a temp home.
    private let shellName: String
    private let home: URL
    private let processEnvironment: [String: String]
    private let checker: APIKeyChecker
    /// Where results persist between launches.
    private let checkCache: APIKeyCheckStore
    /// The machine's salt, so a stored fingerprint needs this machine.
    let checkSalt: Data
    /// The one limiter every check shares, so a click during a full
    /// run queues behind the same four slots instead of opening a lane.
    private let slots = CheckSlots(capacity: checkLimit)
    private var file = APIKeysFile(text: "")
    private var stamp = APIKeysStore.Stamp(content: nil)
    private var environment: [String: String]?
    private var shellRead = false

    public init(store: APIKeysStore, shell: ShellEnvironmentReading,
                checker: APIKeyChecker = APIKeyChecker(transport: URLSessionTransport()),
                checkCache: APIKeyCheckStore,
                defaults: UserDefaults = .standard,
                shellName: String = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh",
                home: URL = FileManager.default.homeDirectoryForCurrentUser,
                environment: [String: String] = ProcessInfo.processInfo.environment) {
        self.store = store
        self.shell = shell
        self.shellName = shellName
        self.home = home
        processEnvironment = environment
        self.checker = checker
        self.checkCache = checkCache
        checkSalt = APIKeyCheckStore.salt(from: defaults)
        checks = checkCache.load(for: store.url)
    }

    public var providers: [String] {
        var seen: [String] = []
        for row in rows where !seen.contains(row.provider) { seen.append(row.provider) }
        return seen
    }

    public var visible: [Row] {
        let needle = search.trimmingCharacters(in: .whitespaces).lowercased()
        guard !needle.isEmpty else { return rows }
        return rows.filter { $0.providerLabel.lowercased().contains(needle) || $0.name.lowercased().contains(needle) }
    }

    /// Reads the file, then what a new shell sees.
    public func load() async {
        isLoading = true
        defer { isLoading = false }
        readFile()
        // Show the file now; the shell can take seconds to start.
        shellRead = false
        rebuild()
        await readShell()
        refreshSourcing()
    }

    /// The login shell's environment when read (ZDOTDIR set in .zshenv
    /// only shows there, never in a Finder-launched process), the
    /// process's otherwise.
    private var shellEnvironment: [String: String] {
        environment ?? processEnvironment
    }

    public func set(name: String, value: String, provider: String,
                    tested: APIKeyCheckResult? = nil) async throws {
        var edited = file
        try edited.set(name: name, value: value, provider: provider)
        try write(edited)
        recordTested(tested, name: name, value: value)
    }

    /// Writes several variables under one heading in a single save — the
    /// custom-provider path writes BASE_URL, API_KEY and MODEL together,
    /// and a failed write drops none or all of them. `tested` is the
    /// verdict `testedName` earned, recorded like a `set` keeps it.
    public func setAll(_ entries: [(name: String, value: String)], provider: String,
                       tested: APIKeyCheckResult? = nil, testedName: String? = nil) async throws {
        var edited = file
        for entry in entries {
            try edited.set(name: entry.name, value: entry.value, provider: provider)
        }
        try write(edited)
        if let testedName, let tested,
           let entry = entries.first(where: { $0.name == testedName }) {
            recordTested(tested, name: testedName, value: entry.value)
        }
    }

    public func remove(name: String) async throws {
        var edited = file
        try edited.remove(name: name)
        try write(edited)
    }

    /// Renames a provider's heading; `from` is the group as it shows.
    public func renameProvider(from: String, to newProvider: String) async throws {
        var edited = file
        try edited.renameProvider(from: from, to: newProvider)
        try write(edited)
    }

    /// One save for every change the editor can make: rename, move and a
    /// new value together, so a changed-on-disk refusal loses none of them.
    /// A renamed key keeps its check result under the new name.
    public func update(name: String, newName: String, provider: String, value: String,
                       tested: APIKeyCheckResult? = nil) async throws {
        var edited = file
        try edited.rename(name: name, to: newName)
        try edited.move(name: newName, toProvider: provider)
        try edited.set(name: newName, value: value, provider: provider)
        try write(edited)
        if newName != name {
            // The old name may not keep a running check or its result:
            // nothing publishes under a name the file no longer has.
            checking.remove(name)
            let check = checks.removeValue(forKey: name)
            // The saved check follows the rename only when the new name
            // asks the same provider; a different probe would inherit a
            // verdict it never earned.
            if let check, Self.sameProbe(name, newName) {
                checks[newName] = check
            }
            saveChecks()
        }
        recordTested(tested, name: newName, value: value)
    }

    /// Whether a check made under one name still applies under another:
    /// the probes are the same, or neither name has one and the reason
    /// there is none is the same (both special-cased, or both unknown).
    static func sameProbe(_ name: String, _ newName: String) -> Bool {
        switch (APIKeyProbe.probe(forName: name), APIKeyProbe.probe(forName: newName)) {
        case let (old?, new?):
            return old == new
        case (nil, nil):
            return APIKeyProbe.noCheckReason(forName: name) == APIKeyProbe.noCheckReason(forName: newName)
        default:
            return false
        }
    }

    /// Check every key that has a value, publishing each result as it
    /// lands. Keys with no probe become "no check" right away, without
    /// the network; the network checks share `slots` with single checks.
    public func checkAll() async {
        guard checking.isEmpty else { return }
        var work: [(name: String, value: String, anchor: String, probe: APIKeyProbe)] = []
        for row in rows where !row.value.isEmpty {
            if let probe = probe(for: row.name) {
                work.append((row.name, row.value, checkAnchor(for: row), probe))
                checking.insert(row.name)
            } else {
                checks[row.name] = APIKeyCheck(
                    result: .noCheck(APIKeyProbe.noCheckReason(forName: row.name)),
                    checkedAt: Date(), value: checkAnchor(for: row), salt: checkSalt)
            }
        }
        let checker = self.checker
        let slots = self.slots
        let salt = checkSalt
        await withTaskGroup(of: (String, APIKeyCheck).self) { group in
            for target in work {
                group.addTask {
                    await slots.acquire()
                    let result = await checker.check(key: target.value, probe: target.probe)
                    await slots.release()
                    return (target.name, APIKeyCheck(result: result, checkedAt: Date(),
                                                     value: target.anchor, salt: salt))
                }
            }
            for await (name, check) in group {
                checking.remove(name)
                // A rename during the run left this name behind: its
                // result must not publish under a name the file lacks.
                guard rows.contains(where: { $0.name == name }) else { continue }
                checks[name] = check
                // The locked merge makes a save per landing result
                // safe, and a quit mid-run keeps what already finished.
                saveChecks()
            }
        }
        // A last save even when nothing went to the network, so a run
        // of only "no check" answers still lands on disk.
        saveChecks()
    }

    /// Re-check one key, the status label's click. Queued at the same
    /// limiter as a full run, and ignored while that row is checking.
    public func check(name: String) async {
        guard let row = rows.first(where: { $0.name == name }), !row.value.isEmpty,
              !checking.contains(name) else { return }
        guard let probe = probe(for: name) else {
            checks[name] = APIKeyCheck(result: .noCheck(APIKeyProbe.noCheckReason(forName: name)),
                                       checkedAt: Date(), value: checkAnchor(for: row), salt: checkSalt)
            saveChecks()
            return
        }
        // The anchor names the key and endpoint this check is sent to.
        // Taken after the await, it would read a base URL saved meanwhile
        // and stamp this verdict as current for the new endpoint.
        let anchor = checkAnchor(for: row)
        checking.insert(name)
        defer { checking.remove(name) }
        await slots.acquire()
        let result = await checker.check(key: row.value, probe: probe)
        await slots.release()
        // A rename during the check must not publish under the old name.
        guard rows.contains(where: { $0.name == name }) else { return }
        checks[name] = APIKeyCheck(result: result, checkedAt: Date(), value: anchor,
                                   salt: checkSalt)
        saveChecks()
    }

    /// Test a key that isn't saved yet, through the same checker and
    /// limiter as a row check. Persists nothing: a save that wants the
    /// result kept passes it back as `tested`.
    public func test(name: String, value: String) async -> APIKeyCheckResult {
        guard let probe = probe(for: name) else {
            return .noCheck(APIKeyProbe.noCheckReason(forName: name))
        }
        await slots.acquire()
        let result = await checker.check(key: value, probe: probe)
        await slots.release()
        return result
    }

    /// Test a custom-provider key before it is saved: `GET {base}/models`
    /// with the key as a bearer token, through the same limiter.
    public func test(key: String, baseURL: String) async -> APIKeyCheckResult {
        guard let probe = CustomProvider.probe(baseURL: baseURL) else {
            return .noCheck("the base URL isn't a valid http(s) URL")
        }
        await slots.acquire()
        let result = await checker.check(key: key, probe: probe)
        await slots.release()
        return result
    }

    /// The models an OpenAI-compatible endpoint serves, for the custom
    /// provider's picker: `GET {base}/models` with the key as a bearer
    /// token. Doesn't share the check limiter; a fetch is the user's own
    /// request, not a sweep.
    public func models(baseURL: String, key: String) async throws -> [CustomProvider.Model] {
        guard let url = CustomProvider.modelsURL(baseURL) else {
            throw CustomProvider.FetchError.badBaseURL
        }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.timeoutInterval = 15
        let (body, response): (Data, HTTPURLResponse)
        do {
            (body, response) = try await checker.transport.send(request)
        } catch {
            throw CustomProvider.FetchError.unreachable(error.localizedDescription)
        }
        guard (200..<300).contains(response.statusCode) else {
            throw CustomProvider.FetchError.http(
                response.statusCode, APIKeyProbe.errorMessage(in: body, key: key))
        }
        return CustomProvider.models(in: body)
    }

    /// The sibling `X_BASE_URL` of a `X_API_KEY` row, when set: the file
    /// points that key at a specific endpoint, so the endpoint steers
    /// both what a check asks and what a saved verdict describes.
    private func customBase(for name: String) -> String? {
        let suffix = "_API_KEY"
        guard name.hasSuffix(suffix), name.count > suffix.count,
              let base = rows.first(where: { $0.name == name.dropLast(suffix.count) + "_BASE_URL" })?.value,
              !base.isEmpty else { return nil }
        return base
    }

    /// The probe a check sends a key to. A sibling base URL reshapes the
    /// probe rather than replace it outright:
    /// - on one of the catalogue probe's own hosts (a region pick, like
    ///   Dashscope's China vs international endpoints), the catalogue
    ///   probe narrows to just that endpoint;
    /// - on any other host, the file points the key at a custom
    ///   endpoint, so it asks `{base}/models` as a bearer token, the
    ///   same request the model fetch and the pre-save test send —
    ///   the catalogue host would see a key it was never meant for.
    ///   OpenRouter's canonical `OPENROUTER_BASE_URL` narrows to the
    ///   same `/key` check rather than the public `/models`.
    /// - an unparseable base is no check at all, never a fall back.
    private func probe(for name: String) -> APIKeyProbe? {
        let catalogProbe = APIKeyProbe.probe(forName: name)
        guard let base = customBase(for: name) else { return catalogProbe }
        let baseHost = URLComponents(string: base)?.host?.lowercased()
        if let catalogProbe, let baseHost {
            let onHost = catalogProbe.hosts.filter { $0.host?.lowercased() == baseHost }
            if !onHost.isEmpty {
                return APIKeyProbe(hosts: onHost, method: catalogProbe.method,
                                   body: catalogProbe.body, auth: catalogProbe.auth,
                                   worksOnBadRequest: catalogProbe.worksOnBadRequest)
            }
            guard let url = CustomProvider.modelsURL(base) else { return nil }
            return APIKeyProbe(hosts: [url], method: "GET", body: nil,
                               auth: .bearer, worksOnBadRequest: false)
        }
        return CustomProvider.probe(baseURL: base)
    }

    /// The value a check's fingerprint covers for a row: the key itself,
    /// plus the custom base URL when one steers its probe. Repointing
    /// the endpoint then retires the verdict the old endpoint earned.
    private func checkAnchor(for row: Row) -> String {
        guard let base = customBase(for: row.name) else { return row.value }
        return row.value + "\u{1F}" + base
    }

    /// Store the verdict a pre-save test reached, so the row shows it
    /// right away instead of asking the provider a second time.
    private func recordTested(_ tested: APIKeyCheckResult?, name: String, value: String) {
        guard let tested else { return }
        let anchor = rows.first(where: { $0.name == name }).map { checkAnchor(for: $0) } ?? value
        checks[name] = APIKeyCheck(result: tested, checkedAt: Date(), value: anchor, salt: checkSalt)
        saveChecks()
    }

    /// The check to show for a row, or nothing when the row's value
    /// changed since it ran: a result only describes the key it tested.
    public func check(for row: Row) -> APIKeyCheck? {
        guard let check = checks[row.name],
              check.isCurrent(for: checkAnchor(for: row), salt: checkSalt) else { return nil }
        return check
    }

    /// Persist what the checks know under this keys file's own section,
    /// dropping names the file no longer has.
    private func saveChecks() {
        let names = Set(rows.map(\.name))
        checkCache.save(checks.filter { names.contains($0.key) },
                        for: store.url, currentNames: names)
    }

    private func write(_ edited: APIKeysFile) throws {
        try store.save(edited, over: stamp)
        savedAt = Date()
        readFile()
        rebuild()
        rereadShell()
    }

    /// The reread a save started, kept so a test can await the rows
    /// settling on the fresh environment.
    var shellReread: Task<Void, Never>?
    /// Serials shell reads so the newest always lands last: a read asked
    /// for before a save describes a file that no longer exists.
    private var shellReads = 0

    /// Reads the login shell's environment once and rebuilds on it.
    /// load() and the post-save reread both go through here; only the
    /// newest request's answer lands, so a save during a load can't be
    /// clobbered by the load's older answer. Sourcing recomputes on the
    /// landing read too: the startup file it checks follows ZDOTDIR,
    /// which only the shell's own environment knows.
    private func readShell() async {
        shellReads += 1
        let serial = shellReads
        let fresh = await shell.freshEnvironment()
        guard serial == shellReads else { return }
        environment = fresh
        shellRead = true
        refreshSourcing()
        rebuild()
    }

    /// A save changes the file a new terminal sources, so the last read
    /// is stale the moment the write lands — ask the shell again. The
    /// serial drops any older read still in flight, including a load's.
    private func rereadShell() {
        shellReread = Task { await readShell() }
    }

    private func readFile() {
        do {
            (file, stamp) = try store.load()
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        refreshSourcing()
    }

    /// Cheap text check, no shell spawn: does the startup file for
    /// `shellName` already source the keys file?
    private func refreshSourcing() {
        guard let rc = ShellStartup.startupFile(shell: shellName, home: home,
                                                environment: shellEnvironment)
        else {
            sourcing = .unsupportedShell(shellName)
            return
        }
        let real = rc.resolvingSymlinksInPath()
        let text = (try? String(contentsOf: real, encoding: .utf8)) ?? ""
        if ShellStartup.sources(text, keysFile: store.url, home: home) {
            sourcing = .sourced
        } else {
            sourcing = ShellStartup.writable(real)
                ? .notSourced(file: rc)
                : .notWritable(file: rc, resolved: real)
        }
    }

    /// The exact line `addSourceLine` would write, for the prompt.
    public var sourceLine: String {
        ShellStartup.sourceLine(for: store.url, home: home)
    }

    /// Appends the one line to the startup file (backing it up first),
    /// then reloads so the page shows the change. Returns the startup
    /// file and the backup made, nil when none existed to back up.
    @discardableResult
    public func addSourceLine() async throws -> (file: URL, backup: URL?) {
        let result = try ShellStartupWriter(home: home, backupDirectory: store.backupDirectory)
            .addSource(keysFile: store.url, shell: shellName,
                       environment: shellEnvironment)
        await load()
        return result
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
            return Row(provider: entry.provider, name: entry.name,
                       value: entry.value, status: status)
        }
    }
}
