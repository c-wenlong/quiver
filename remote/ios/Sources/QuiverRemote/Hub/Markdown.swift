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
    }

    struct Table: Hashable {
        enum Align: Hashable { case leading, center, trailing }
        var header: [String]
        var align: [Align]
        var rows: [[String]]

        var columnCount: Int { max(header.count, rows.map(\.count).max() ?? 0) }
    }

    static func parse(_ text: String) -> [Block] {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
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
                var body: [String] = []
                i += 1
                while i < lines.count, !lines[i].trimmingCharacters(in: .whitespaces).hasPrefix(fence) {
                    body.append(lines[i])
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
                // Indented lines that aren't a new item continue this one.
                while i < lines.count {
                    let next = lines[i]
                    let nt = next.trimmingCharacters(in: .whitespaces)
                    guard !nt.isEmpty, listItem(next) == nil, next.hasPrefix("  ") || next.hasPrefix("\t") else { break }
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

    static func heading(_ line: String) -> (Int, String)? {
        let hashes = line.prefix { $0 == "#" }
        guard (1...6).contains(hashes.count) else { return nil }
        let rest = line.dropFirst(hashes.count)
        guard rest.first == " " else { return nil }
        let text = rest.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "#")).trimmingCharacters(in: .whitespaces)
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

    /// Cells of a `| a | b |` row. A `\|` stays in its cell as `|`.
    static func cells(_ row: String) -> [String] {
        var s = row.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") && !s.hasSuffix("\\|") { s.removeLast() }
        var out: [String] = []
        var current = ""
        var escaped = false
        for ch in s {
            if escaped { current.append(ch); escaped = false; continue }
            if ch == "\\" { escaped = true; continue }
            if ch == "|" { out.append(current.trimmingCharacters(in: .whitespaces)); current = ""; continue }
            current.append(ch)
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
