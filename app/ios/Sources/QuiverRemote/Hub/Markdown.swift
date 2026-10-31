import Foundation

/// Block-level Markdown for agent messages: the subset agents actually
/// write (headings, lists, tables, quotes, rules, fenced code). Inline
/// spans (bold, italics, code, links) are left in the text for
/// `AttributedString(markdown:)` to style.
enum Markdown {
    enum Block: Hashable {
        case heading(level: Int, text: String)
        case paragraph(String)
        /// One list item; `indent` counts nesting levels, `marker` is "•"
        /// or the item's own number ("1.", "2)").
        case listItem(indent: Int, marker: String, text: String)
        case quote(String)
        case code(language: String?, text: String)
        case table(Table)
        case rule
        /// An image on its own line. The phone can't open the agent's
        /// local file, so it shows as a labelled chip.
        case image(alt: String, source: String)
        /// A view a harness drew (Droid's `<json-render>`): no way to draw
        /// it here, so it folds into a note.
        case rendered(String)
    }

    /// A task item's box, read off the start of a list item's text.
    enum TaskBox: Hashable { case open, done }

    static func taskBox(_ text: String) -> (TaskBox, String)? {
        let lower = text.lowercased()
        if lower.hasPrefix("[ ] ") { return (.open, String(text.dropFirst(4))) }
        if lower.hasPrefix("[x] ") { return (.done, String(text.dropFirst(4))) }
        return nil
    }

    struct Table: Hashable {
        enum Align: Hashable { case leading, center, trailing }
        var header: [String]
        var align: [Align]
        var rows: [[String]]

        var columnCount: Int { max(header.count, rows.map(\.count).max() ?? 0) }
    }

    static func parse(_ text: String) -> [Block] {
        var lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var blocks: [Block] = []
        var paragraph: [String] = []
        var i = 0

        func flushParagraph() {
            let joined = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.paragraph(joined)) }
            paragraph = []
        }

        while i < lines.count {
            let line = lines[i]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            // Fenced code: everything up to the closing fence, verbatim.
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flushParagraph()
                let fence = String(trimmed.prefix(3))
                let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                // A fence indented under a list item: its lines lose that indent.
                let indent = line.prefix { $0 == " " }.count
                var body: [String] = []
                i += 1
                while i < lines.count, !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(fence) {
                    let raw = lines[i]
                    let lead = min(indent, raw.prefix { $0 == " " }.count)
                    body.append(String(raw.dropFirst(lead)))
                    i += 1
                }
                i += 1 // the closing fence (or past the end)
                blocks.append(.code(language: lang.isEmpty ? nil : lang, text: body.joined(separator: "\n")))
                continue
            }

            if trimmed.isEmpty {
                flushParagraph()
                i += 1
                continue
            }

            if trimmed.hasPrefix("<json-render>") {
                flushParagraph()
                var body = trimmed
                while !body.contains("</json-render>"), i + 1 < lines.count {
                    i += 1
                    body += "\n" + lines[i]
                }
                // Text after the closing tag on its line parses as usual.
                if let close = body.range(of: "</json-render>") {
                    let rest = body[close.upperBound...].trimmingCharacters(in: .whitespaces)
                    body = String(body[..<close.upperBound])
                    if !rest.isEmpty {
                        lines[i] = rest
                        blocks.append(.rendered(body))
                        continue
                    }
                }
                i += 1
                blocks.append(.rendered(body))
                continue
            }

            if let (alt, source) = imageLine(trimmed) {
                flushParagraph()
                blocks.append(.image(alt: alt, source: source))
                i += 1
                continue
            }

            // A table: a row of cells, then a |---|:--:| separator row.
            if trimmed.contains("|"), i + 1 < lines.count, let align = separatorAlignments(lines[i + 1]) {
                flushParagraph()
                let header = cells(trimmed)
                var rows: [[String]] = []
                i += 2
                while i < lines.count {
                    let row = lines[i].trimmingCharacters(in: .whitespaces)
                    guard !row.isEmpty, row.contains("|") else { break }
                    rows.append(cells(row))
                    i += 1
                }
                blocks.append(.table(Table(header: header, align: align, rows: rows)))
                continue
            }

            if let (level, text) = heading(trimmed) {
                flushParagraph()
                blocks.append(.heading(level: level, text: text))
                i += 1
                continue
            }

            if isRule(trimmed) {
                flushParagraph()
                blocks.append(.rule)
                i += 1
                continue
            }

            if trimmed.hasPrefix(">") {
                flushParagraph()
                var quoted: [String] = []
                while i < lines.count {
                    let q = lines[i].trimmingCharacters(in: .whitespaces)
                    guard q.hasPrefix(">") else { break }
                    var rest = q.dropFirst()
                    if rest.first == " " { rest = rest.dropFirst() }
                    quoted.append(String(rest))
                    i += 1
                }
                blocks.append(.quote(quoted.joined(separator: "\n")))
                continue
            }

            if let item = listItem(line) {
                flushParagraph()
                var text = item.text
                i += 1
                // Indented lines that aren't a new item continue this one,
                // unless they open a block of their own (a fence, a table,
                // a quote, a heading), which then parses on its own.
                while i < lines.count {
                    let next = lines[i]
                    let nt = next.trimmingCharacters(in: .whitespaces)
                    guard !nt.isEmpty, listItem(next) == nil, next.hasPrefix("  ") || next.hasPrefix("\t") else { break }
                    if startsBlock(lines, at: i) { break }
                    text += "\n" + nt
                    i += 1
                }
                blocks.append(.listItem(indent: item.indent, marker: item.marker, text: text))
                continue
            }

            paragraph.append(line)
            i += 1
        }
        flushParagraph()
        return blocks
    }

    // MARK: Pieces

    /// `![alt](source)` alone on a line.
    static func imageLine(_ line: String) -> (String, String)? {
        guard line.hasPrefix("!["), line.hasSuffix(")"),
              let close = line.range(of: "]("), line.firstIndex(of: "]") == close.lowerBound
        else { return nil }
        let alt = String(line[line.index(line.startIndex, offsetBy: 2)..<close.lowerBound])
        let source = String(line[close.upperBound..<line.index(before: line.endIndex)])
        // Exactly one image: `![a](x)![b](y)` stays for inline rendering.
        guard !source.contains(" "), !source.contains(")"), !source.contains("![") else { return nil }
        return (alt, source)
    }

    /// Inline `![alt](source)` inside a sentence, as text the phone can show.
    /// Code spans and an escaped `\![` keep what the agent wrote.
    static func inlineImagesAsText(_ text: String) -> String {
        guard text.contains("!["), let re = inlineImageRE else { return text }
        let ns = text as NSString
        var out = ""
        var at = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            guard m.range(at: 2).location != NSNotFound else { continue } // a code span
            out += ns.substring(with: NSRange(location: at, length: m.range.location - at))
            out += "*Image: \(ns.substring(with: m.range(at: 2)))*"
            at = m.range.location + m.range.length
        }
        return out + ns.substring(from: at)
    }

    /// A code span (left as is), or an unescaped image (group 2 its alt).
    private static let inlineImageRE = try? NSRegularExpression(
        pattern: #"(`+)[\s\S]*?\1|(?<!\\)!\[([^\]]*)\]\([^)\s]+\)"#)

    /// Whether `lines[i]` opens a block that isn't list-item text.
    static func startsBlock(_ lines: [String], at i: Int) -> Bool {
        let t = lines[i].trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("```") || t.hasPrefix("~~~") || t.hasPrefix(">") || heading(t) != nil { return true }
        if t.hasPrefix("<json-render>") || imageLine(t) != nil { return true }
        if t.contains("|"), i + 1 < lines.count, separatorAlignments(lines[i + 1]) != nil { return true }
        return false
    }

    static func heading(_ line: String) -> (Int, String)? {
        let hashes = line.prefix { $0 == "#" }
        guard (1...6).contains(hashes.count) else { return nil }
        let rest = line.dropFirst(hashes.count)
        guard rest.first == " " else { return nil }
        var text = rest.trimmingCharacters(in: .whitespaces)
        // An optional closing run ("## Title ##") goes only when a space
        // sets it apart, so "C#" and "#123" keep their hashes.
        if let spaceRun = text.range(of: #"\s+#+$"#, options: .regularExpression) {
            text.removeSubrange(spaceRun)
        } else if text.allSatisfy({ $0 == "#" }) {
            text = ""
        }
        return (hashes.count, text)
    }

    static func isRule(_ line: String) -> Bool {
        let compact = line.replacingOccurrences(of: " ", with: "")
        guard compact.count >= 3, let first = compact.first, "-*_".contains(first) else { return false }
        return compact.allSatisfy { $0 == first }
    }

    static func listItem(_ line: String) -> (indent: Int, marker: String, text: String)? {
        let leading = line.prefix { $0 == " " || $0 == "\t" }
        let width = leading.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
        let rest = line.dropFirst(leading.count)
        if let first = rest.first, "-*+".contains(first), rest.dropFirst().first == " " {
            // "- [ ] task" and "- [x] task" keep their box as text.
            return (width / 2, "•", String(rest.dropFirst(2)))
        }
        let digits = rest.prefix { $0.isNumber }
        guard !digits.isEmpty, digits.count <= 3 else { return nil }
        let after = rest.dropFirst(digits.count)
        guard let punct = after.first, punct == "." || punct == ")", after.dropFirst().first == " " else { return nil }
        return (width / 2, "\(digits)\(punct)", String(after.dropFirst(2)))
    }

    /// Cells of a `| a | b |` row. Only `\|` is an escape here: it stays
    /// in its cell as `|`. Every other backslash (`C:\Users`, `\d+`) is
    /// left for the inline parser.
    static func cells(_ row: String) -> [String] {
        var s = row.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") && !s.hasSuffix("\\|") { s.removeLast() }
        var out: [String] = []
        var current = ""
        var chars = Array(s)[...]
        while let ch = chars.popFirst() {
            if ch == "\\", chars.first == "|" {
                current.append("|")
                chars.removeFirst()
            } else if ch == "|" {
                out.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            } else {
                current.append(ch)
            }
        }
        out.append(current.trimmingCharacters(in: .whitespaces))
        return out
    }

    /// The alignments a `|---|:--:|--:|` row sets, or nil when the line
    /// isn't a separator row.
    static func separatorAlignments(_ line: String) -> [Table.Align]? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.contains("-") else { return nil }
        let parts = cells(trimmed)
        guard !parts.isEmpty else { return nil }
        var out: [Table.Align] = []
        for p in parts {
            let core = p.trimmingCharacters(in: CharacterSet(charactersIn: ":"))
            guard !core.isEmpty, core.allSatisfy({ $0 == "-" }) else { return nil }
            let left = p.hasPrefix(":"), right = p.hasSuffix(":")
            out.append(left && right ? .center : right ? .trailing : .leading)
        }
        // A single dashed "cell" without pipes is a rule, not a table.
        if out.count == 1 && !trimmed.contains("|") { return nil }
        return out
    }
}

extension Markdown {
    /// A hub or app message with its backtick spans as inline code
    /// ("run `gh auth login`"). Nothing else is parsed: these are plain
    /// sentences, so a `*` or `_` in a path or error stays as written.
    /// A run of backticks closes only on a run of the same length, as in
    /// CommonMark; one with no partner shows as typed.
    static func inlineCode(_ text: String) -> AttributedString {
        guard text.contains("`") else { return AttributedString(text) }
        let chars = Array(text)
        var out = AttributedString()
        var plain = ""
        var i = 0
        func runLength(at j: Int) -> Int {
            var k = j
            while k < chars.count && chars[k] == "`" { k += 1 }
            return k - j
        }
        while i < chars.count {
            guard chars[i] == "`" else { plain.append(chars[i]); i += 1; continue }
            let open = runLength(at: i)
            var j = i + open
            var close: Int?
            while j < chars.count {
                if chars[j] == "`" {
                    let n = runLength(at: j)
                    if n == open { close = j; break }
                    j += n
                } else {
                    j += 1
                }
            }
            guard let end = close else {
                plain.append(String(chars[i..<(i + open)]))
                i += open
                continue
            }
            var code = String(chars[(i + open)..<end])
            // CommonMark trims one space each side, so "`` `x` ``" reads `x`.
            if code.count >= 2, code.first == " ", code.last == " ", code.contains(where: { $0 != " " }) {
                code = String(code.dropFirst().dropLast())
            }
            out += AttributedString(plain)
            plain = ""
            var span = AttributedString(code)
            span.inlinePresentationIntent = .code
            out += span
            i = end + open
        }
        out += AttributedString(plain)
        return out
    }
}
