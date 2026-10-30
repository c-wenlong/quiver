import Foundation

/// The editable fields of a `.claude-plugin/plugin.json` manifest as one
/// flat form. `apply(to:)` writes them back through `JSONTextEdit`, so
/// every other key, the key order and the file's layout are left alone:
/// only the fields the form showed change.
public struct PluginManifestFields: Sendable, Equatable {
    public var description = ""
    public var version = ""
    public var authorName = ""
    public var authorEmail = ""
    public var authorURL = ""
    public var homepage = ""
    public var repository = ""
    public var license = ""
    /// Keywords separated by ", ".
    public var keywords = ""

    public init() {}

    /// Read the fields out of manifest text; absent fields stay empty.
    public init(parsing text: String) {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return }
        description = object["description"] as? String ?? ""
        version = object["version"] as? String ?? ""
        homepage = object["homepage"] as? String ?? ""
        repository = object["repository"] as? String ?? ""
        license = object["license"] as? String ?? ""
        keywords = (object["keywords"] as? [String] ?? []).joined(separator: ", ")
        if let author = object["author"] as? [String: Any] {
            authorName = author["name"] as? String ?? ""
            authorEmail = author["email"] as? String ?? ""
            authorURL = author["url"] as? String ?? ""
        } else if let author = object["author"] as? String {
            authorName = author
        }
    }

    /// "not a version", "not a URL": what stops a save.
    public var problems: [String] {
        var problems: [String] = []
        if !version.isEmpty, !PluginManifestFields.isVersion(version) {
            problems.append("Version should look like 1.2.3.")
        }
        for (label, url) in [("Homepage", homepage), ("Repository", repository), ("Author URL", authorURL)] {
            if !url.isEmpty, !PluginManifestFields.isURL(url) {
                problems.append("\(label) is not a URL.")
            }
        }
        return problems
    }

    /// `1.2.3` with an optional suffix.
    public static func isVersion(_ version: String) -> Bool {
        version.range(of: #"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$"#, options: .regularExpression) != nil
    }

    public static func isURL(_ string: String) -> Bool {
        guard let url = URL(string: string) else { return false }
        return url.scheme != nil && url.host != nil
    }

    /// The manifest text with the fields this form changed applied. A
    /// field still reading as it did in `text` is not written, so a key the
    /// file lacks isn't added empty and a value the form can't show (a
    /// `repository` object, a string `author`) stays as it is. A field
    /// cleared on purpose is written empty, matching how `swe` writes them.
    public func apply(to text: String) throws -> String {
        let before = PluginManifestFields(parsing: text)
        var text = text
        for (value, old, key) in [(description, before.description, "description"),
                                  (version, before.version, "version"),
                                  (homepage, before.homepage, "homepage"),
                                  (repository, before.repository, "repository"),
                                  (license, before.license, "license")] where value != old {
            try set(value, at: [key], in: &text)
        }
        let words = Self.keywordList(keywords)
        if words != Self.keywordList(before.keywords) {
            try set(words, at: ["keywords"], in: &text)
        }
        let nameChanged = authorName != before.authorName
        let detailChanged = authorEmail != before.authorEmail || authorURL != before.authorURL
        if Self.authorIsString(text), !detailChanged {
            // `"author": "Name"` stays a string when only the name moves.
            if nameChanged { try set(authorName, at: ["author"], in: &text) }
        } else {
            // A string author becoming an object takes its name along.
            if nameChanged || (detailChanged && Self.authorIsString(text)) {
                try set(authorName, at: ["author", "name"], in: &text)
            }
            if authorEmail != before.authorEmail { try set(authorEmail, at: ["author", "email"], in: &text) }
            if authorURL != before.authorURL { try set(authorURL, at: ["author", "url"], in: &text) }
        }
        return text
    }

    private static func keywordList(_ keywords: String) -> [String] {
        keywords.components(separatedBy: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    private static func authorIsString(_ text: String) -> Bool {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return false }
        return object["author"] is String
    }

    private func set(_ value: Any, at path: [String], in text: inout String) throws {
        text = try JSONTextEdit.set(value, at: path, in: text)
    }

    /// Write `interface.displayName` into a `.codex-plugin/plugin.json`,
    /// keeping every other key. Codex documents the field; Claude's files
    /// do not get keys they do not know.
    public static func setCodexDisplayName(_ name: String, in text: String) throws -> String {
        try JSONTextEdit.set(name, at: ["interface", "displayName"], in: text)
    }
}

/// The `---`-fenced frontmatter of a markdown file, kept as raw lines.
/// `key: value` lines are editable fields; anything else (comments,
/// lists, nested values) stays byte-for-byte so nothing the harness
/// relies on is rewritten.
public struct MarkdownFrontmatter: Sendable, Equatable {
    /// A `key: value` line that is safe to edit: the value sits on the
    /// same line and no continuation or nested lines follow.
    public struct Field: Sendable, Equatable {
        public var key: String
        public var value: String
    }

    public var fields: [Field]
    /// Lines inside the fences that are not simple fields.
    public var other: [String]

    /// The frontmatter of `text`, or nil when the file has none.
    public init?(text: String) {
        guard let range = MarkdownFrontmatter.range(in: text) else { return nil }
        let inside = text[range].split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        fields = []
        other = []
        var i = 0
        while i < inside.count {
            let line = inside[i]
            if let parsed = MarkdownFrontmatter.parseField(line) {
                let key = parsed.key
                // Simple only when nothing indented hangs off it.
                let next = i + 1 < inside.count ? inside[i + 1] : ""
                let simple = next.isEmpty || !(next.first == " " || next.first == "\t")
                if simple {
                    fields.append(Field(key: key, value: parsed.value))
                } else {
                    other.append(line)
                }
            } else {
                other.append(line)
            }
            i += 1
        }
        if other.last == "" { other.removeLast() }
    }

    /// `(key, value)` for a `key: value` line, else nil.
    private static func parseField(_ line: String) -> (key: String, value: String)? {
        guard let colon = line.firstIndex(of: ":") else { return nil }
        let key = line[line.startIndex..<colon]
        guard !key.isEmpty, !key.contains(" "), !key.contains("\t"),
              key.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" })
        else { return nil }
        var value = line[line.index(after: colon)...]
        if value.first == " " { value = value.dropFirst() }
        return (String(key), String(value).trimmingCharacters(in: .whitespaces))
    }

    /// The frontmatter's line range inside `text` (between the fences).
    private static func range(in text: String) -> Range<String.Index>? {
        guard text.hasPrefix("---") else { return nil }
        var index = text.startIndex
        // The first line must be exactly "---" (or "---" then end).
        guard let firstEnd = text[index...].firstIndex(of: "\n"),
              text[index..<firstEnd].trimmingCharacters(in: .whitespaces) == "---"
        else { return nil }
        index = text.index(after: firstEnd)
        var lineStart = index
        while lineStart < text.endIndex {
            let lineEnd = text[lineStart...].firstIndex(of: "\n") ?? text.endIndex
            let line = text[lineStart..<lineEnd].trimmingCharacters(in: .whitespaces)
            if line == "---" { return index..<lineStart }
            lineStart = lineEnd < text.endIndex ? text.index(after: lineEnd) : text.endIndex
        }
        return nil
    }

    /// `text` with `values`' keys set, in the order given: a simple field
    /// keeps its line (the value is swapped), a key the file lacks is added
    /// before the closing fence, and every other byte stays. With no
    /// frontmatter one is created at the top.
    public static func update(_ text: String, set values: [(key: String, value: String)]) -> String {
        guard let range = range(in: text) else {
            let block = values.map { "\($0.key): \($0.value)" }.joined(separator: "\n")
            return "---\n\(block)\n---\n\n\(text)"
        }
        var lines = text[range].split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
        var left = values
        for i in lines.indices {
            guard let parsed = parseField(lines[i]),
                  let at = left.firstIndex(where: { $0.key == parsed.key }) else { continue }
            // Consume the key either way so a complex field is never
            // duplicated: a key the file already has in a form the form
            // can't show stays exactly as it is.
            let value = left.remove(at: at).value
            let next = i + 1 < lines.count ? lines[i + 1] : ""
            guard next.isEmpty || !(next.first == " " || next.first == "\t") else { continue }
            lines[i] = value.isEmpty ? parsed.key + ":" : parsed.key + ": " + value
        }
        // Insert before the closing fence: a trailing "" is the newline
        // that already ends the last line, so new keys go ahead of it.
        let at = lines.last == "" ? lines.count - 1 : lines.count
        lines.insert(contentsOf: left.map { "\($0.key): \($0.value)" }, at: at)
        return String(text[..<range.lowerBound])
            + lines.joined(separator: "\n")
            + String(text[range.upperBound...])
    }
}
