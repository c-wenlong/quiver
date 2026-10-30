import Foundation

/// A change to a JSON object made by editing its text: one value replaced
/// in place or inserted before the object's closing brace, so unknown
/// keys, their order and the file's layout survive untouched. This is how
/// the plugin and marketplace forms write back: manifests are read by
/// harnesses that may reject a reordered or trimmed file.
public enum JSONTextEdit {
    public enum Failure: Error, Equatable {
        /// The text is not a single JSON object.
        case notJSONObject
        /// The new value is not a JSON type.
        case notJSONValue
    }

    /// Set `value` at `path`, each element a key one level down, and return
    /// the new text. A parent object the path needs but the file lacks is
    /// created; one that exists as something else is replaced with an
    /// object. `value` may be a String, a number, a Bool, an array or a
    /// dictionary of those.
    public static func set(_ value: Any, at path: [String], in text: String) throws -> String {
        guard let top = try topLevelObject(in: text) else { throw Failure.notJSONObject }
        return try setIn(text, object: top, path: path[...], value: value)
    }

    // MARK: - Editing

    private static func setIn(_ text: String, object: Range<String.Index>,
                              path: ArraySlice<String>, value: Any) throws -> String {
        guard let key = path.first else { throw Failure.notJSONValue }
        let entries = try objectEntries(text, span: object)
        if let entry = entries.first(where: { $0.key == key }) {
            if path.count == 1 {
                let rendered = try render(value, indent: indentOf(text, at: entry.valueRange.lowerBound))
                return text.replacingCharacters(in: entry.valueRange, with: rendered)
            }
            if let inner = try objectSpanAt(text, range: entry.valueRange) {
                return try setIn(text, object: inner, path: path.dropFirst(), value: value)
            }
            // The key exists as a scalar or array: make it an object first.
            let replaced = text.replacingCharacters(in: entry.valueRange, with: "{}")
            let start = replaced.index(replaced.startIndex,
                                       offsetBy: text.distance(from: text.startIndex,
                                                               to: entry.valueRange.lowerBound))
            let empty = start ..< replaced.index(start, offsetBy: 2)
            return try setIn(replaced, object: empty, path: path.dropFirst(), value: value)
        }
        let inserted = try insert(text, into: object, entries: entries,
                                  key: key, rendered: path.count == 1 ? render(value, indent: "") : "{}")
        if path.count == 1 { return inserted }
        // The new entry landed inside THIS object, which grew by the
        // inserted text: shift its end by the delta and search it again,
        // not the top level, so a nested insert finds its own object.
        let delta = inserted.count - text.count
        let lower = inserted.index(inserted.startIndex,
                                   offsetBy: text.distance(from: text.startIndex, to: object.lowerBound))
        let upper = inserted.index(inserted.startIndex,
                                   offsetBy: text.distance(from: text.startIndex, to: object.upperBound) + delta)
        guard let created = try objectEntries(inserted, span: lower..<upper).first(where: { $0.key == key }),
              let inner = try objectSpanAt(inserted, range: created.valueRange)
        else { throw Failure.notJSONObject }
        return try setIn(inserted, object: inner, path: path.dropFirst(), value: value)
    }

    /// Insert `"key": rendered` as the object's last entry.
    private static func insert(_ text: String, into object: Range<String.Index>,
                               entries: [Entry], key: String, rendered: String) throws -> String {
        let renderedKey = try render(key)
        let closingBrace = text.index(before: object.upperBound)
        guard let last = entries.last else {
            // An empty object is written compactly.
            let inner = text[object].trimmingCharacters(in: .whitespacesAndNewlines) == "{}" ? "" : " "
            return text.replacingCharacters(in: object, with: "{\(inner)\(renderedKey): \(rendered)\(inner)}")
        }
        // Swap the gap between the last value and `}` for the comma, the
        // new entry on its own line, and the closing line's indent.
        let indent = lineIndent(text, at: last.keyRange.lowerBound)
        let braceIndent = lineIndent(text, at: closingBrace)
        let replacement = ",\n\(indent)\(renderedKey): \(rendered)\n\(braceIndent)"
        return text.replacingCharacters(in: last.valueRange.upperBound..<closingBrace,
                                        with: replacement)
    }

    // MARK: - Scanning

    private struct Entry {
        var key: String
        var keyRange: Range<String.Index>
        var valueRange: Range<String.Index>
    }

    /// The top-level object's span, nil when the text is not one object.
    private static func topLevelObject(in text: String) throws -> Range<String.Index>? {
        guard let start = text.firstIndex(where: { !$0.isWhitespace }) else { return nil }
        guard text[start] == "{" else { return nil }
        guard let end = try scanValue(text, at: start) else { throw Failure.notJSONObject }
        return start..<end
    }

    /// The span of the object at `range`'s start, when that value is one.
    private static func objectSpanAt(_ text: String, range: Range<String.Index>) throws -> Range<String.Index>? {
        guard let start = text[range].firstIndex(where: { !$0.isWhitespace }), text[start] == "{" else { return nil }
        guard let end = try scanValue(text, at: start) else { throw Failure.notJSONObject }
        return start..<end
    }

    /// The entries of the object `span` covers, in file order.
    private static func objectEntries(_ text: String, span: Range<String.Index>) throws -> [Entry] {
        var entries: [Entry] = []
        var index = text.index(after: span.lowerBound)
        while true {
            index = skipSpace(text, from: index, before: span.upperBound)
            guard index < span.upperBound, text[index] != "}" else { return entries }
            guard text[index] == "\"" else { throw Failure.notJSONObject }
            // The opening quote's index: whitespace may sit between the
            // key and its colon, so `index` has moved on by entry time.
            let keyStart = index
            let keyEnd = try scanStringEnd(text, at: index)
            let key = try unquote(String(text[index..<keyEnd]))
            index = skipSpace(text, from: keyEnd, before: span.upperBound)
            guard index < span.upperBound, text[index] == ":" else { throw Failure.notJSONObject }
            let valueStart = skipSpace(text, from: text.index(after: index), before: span.upperBound)
            guard let valueEnd = try scanValue(text, at: valueStart) else { throw Failure.notJSONObject }
            entries.append(Entry(key: key, keyRange: keyStart..<keyEnd, valueRange: valueStart..<valueEnd))
            index = skipSpace(text, from: valueEnd, before: span.upperBound)
            if index < span.upperBound, text[index] == "," {
                index = text.index(after: index)
            }
        }
    }

    /// The end of a JSON value starting at `index` (after whitespace).
    private static func scanValue(_ text: String, at index: String.Index) throws -> String.Index? {
        guard index < text.endIndex else { return nil }
        switch text[index] {
        case "\"": return try scanStringEnd(text, at: index)
        case "{", "[":
            let close: Character = text[index] == "{" ? "}" : "]"
            var depth = 0
            var i = index
            while i < text.endIndex {
                let c = text[i]
                if c == "\"" {
                    i = try scanStringEnd(text, at: i)
                    continue
                }
                if c == text[index] { depth += 1 }
                if c == close {
                    depth -= 1
                    if depth == 0 { return text.index(after: i) }
                }
                i = text.index(after: i)
            }
            return nil
        default:
            var i = index
            while i < text.endIndex, !",}] \t\n\r".contains(text[i]) {
                i = text.index(after: i)
            }
            return i > index ? i : nil
        }
    }

    /// The index just past a string literal that starts at `index` (`"`).
    private static func scanStringEnd(_ text: String, at index: String.Index) throws -> String.Index {
        var i = text.index(after: index)
        while i < text.endIndex {
            switch text[i] {
            case "\\": i = text.index(i, offsetBy: 2, limitedBy: text.endIndex) ?? text.endIndex
            case "\"": return text.index(after: i)
            default: i = text.index(after: i)
            }
        }
        throw Failure.notJSONObject
    }

    private static func skipSpace(_ text: String, from index: String.Index,
                                  before end: String.Index) -> String.Index {
        var i = index
        while i < end, text[i].isWhitespace { i = text.index(after: i) }
        return i
    }

    /// The spaces or tabs the line at `index` opens with.
    private static func lineIndent(_ text: String, at index: String.Index) -> String {
        var i = index
        while i > text.startIndex, text[text.index(before: i)] != "\n" { i = text.index(before: i) }
        var end = i
        while end < text.endIndex, text[end] == " " || text[end] == "\t" { end = text.index(after: end) }
        return String(text[i..<end])
    }

    private static func indentOf(_ text: String, at index: String.Index) -> String {
        lineIndent(text, at: index)
    }

    // MARK: - Rendering

    /// `value` as JSON, objects and arrays pretty-printed and re-indented
    /// so an entry lines up under its key.
    private static func render(_ value: Any, indent: String) throws -> String {
        switch value {
        case let string as String: return try render(string)
        case let number as NSNumber where CFGetTypeID(number) == CFBooleanGetTypeID():
            return number.boolValue ? "true" : "false"
        case let number as NSNumber:
            return "\(number)"
        case is [Any], is [String: Any]:
            guard JSONSerialization.isValidJSONObject(value),
                  let data = try? JSONSerialization.data(withJSONObject: value,
                                                         options: [.prettyPrinted, .withoutEscapingSlashes])
            else { throw Failure.notJSONValue }
            let pretty = String(decoding: data, as: UTF8.self)
            guard !indent.isEmpty else { return pretty }
            return pretty.components(separatedBy: "\n").joined(separator: "\n" + indent)
        default: throw Failure.notJSONValue
        }
    }

    /// A string as a JSON string literal. Slashes stay bare, so a URL
    /// reads `https://…` as people and other tools write it, not `https:\/\/…`.
    private static func render(_ string: String) throws -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [string],
                                                     options: [.withoutEscapingSlashes]) else {
            throw Failure.notJSONValue
        }
        var encoded = String(decoding: data, as: UTF8.self)
        encoded.removeFirst()
        encoded.removeLast()
        return encoded
    }

    /// A `"..."` token back to the string it encodes.
    private static func unquote(_ token: String) throws -> String {
        guard let data = ("[" + token + "]").data(using: .utf8),
              let decoded = try? JSONSerialization.jsonObject(with: data) as? [String],
              let string = decoded.first else { throw Failure.notJSONObject }
        return string
    }
}
