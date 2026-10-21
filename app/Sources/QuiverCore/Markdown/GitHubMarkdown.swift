import Foundation

/// A GitHub body is markdown with inline and block HTML (links,
/// `<details>` folds, `<table>`s, `<blockquote>`s — dependabot and
/// release bodies are mostly HTML). Apple's markdown parser leaves
/// the tags as literal text, so bodies are pre-processed into
/// segments here: `<details>` blocks become foldable sections, and
/// the prose between them is converted to real markdown.
public indirect enum MarkdownSegment: Equatable, Sendable {
    case markdown(String)
    case details(title: String, body: [MarkdownSegment])
}

public enum GitHubMarkdown {

    /// Split `body` into renderable segments, top-level `<details>`
    /// blocks first. Unclosed or malformed blocks stay as text.
    public static func segments(_ body: String) -> [MarkdownSegment] {
        var out: [MarkdownSegment] = []
        var prose = ""
        var rest = Substring(body)
        while let range = firstTag(in: rest, named: "details") {
            prose += rest[..<range.lowerBound]
            if let end = matchingClose(in: rest, named: "details", from: range.upperBound) {
                let block = rest[range.upperBound..<end.inner]
                let (title, inner) = splitSummary(block)
                flush(&prose, into: &out)
                out.append(.details(title: inlineMarkdown(title),
                                    body: segments(String(inner))))
                rest = rest[end.outer...]
            } else {
                prose += rest[range.lowerBound...]
                rest = rest[rest.endIndex...]
            }
        }
        prose += rest
        flush(&prose, into: &out)
        return out
    }

    /// The HTML→markdown pass for prose between `<details>` blocks:
    /// links, emphasis, lists, blockquotes, headings and tables come
    /// out as markdown; comments, scripts and boilerplate tags drop.
    static func htmlToMarkdown(_ html: String) -> String {
        var c = Converter(html)
        c.run()
        return c.finish()
    }

    /// Single-line conversion for `<summary>` titles.
    static func inlineMarkdown(_ html: String) -> String {
        htmlToMarkdown(html)
            .replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespaces)
    }

    private static func flush(_ prose: inout String,
                              into out: inout [MarkdownSegment]) {
        let md = htmlToMarkdown(prose)
        prose = ""
        if !md.isEmpty { out.append(.markdown(md)) }
    }

    // MARK: - tag scanning

    /// The range of `<name …>` (not `</name`) starting a tag, or nil.
    static func firstTag(in s: Substring,
                         named name: String) -> Range<String.Index>? {
        var from = s.startIndex
        while let r = s.range(of: "<" + name, options: .caseInsensitive,
                              range: from..<s.endIndex) {
            let after = r.upperBound
            guard after < s.endIndex else { return nil }
            let tail = s[after]
            guard tail == " " || tail == ">" || tail == "/" || tail == "\n"
                    || tail == "\t" || tail == "\r" else {
                from = r.upperBound
                continue
            }
            guard let gt = s[after...].firstIndex(of: ">") else { return nil }
            return r.lowerBound..<s.index(after: gt)
        }
        return nil
    }

    /// `<name>` at `from` … its matching `</name>`: `inner` is the end
    /// of the block's content, `outer` the end of the close tag.
    /// Nesting of the same tag counts. Returns nil when unclosed.
    static func matchingClose(in s: Substring, named name: String,
                              from: String.Index)
        -> (inner: String.Index, outer: String.Index)? {
        var depth = 1
        var at = from
        let open = "<" + name, close = "</" + name
        while at < s.endIndex {
            if let r = s.range(of: open, options: .caseInsensitive,
                               range: at..<s.endIndex),
               r.lowerBound < (s.range(of: close, options: .caseInsensitive,
                                       range: at..<s.endIndex)?.lowerBound
                               ?? s.endIndex) {
                depth += 1
                at = r.upperBound
                continue
            }
            guard let r = s.range(of: close, options: .caseInsensitive,
                                  range: at..<s.endIndex) else { return nil }
            depth -= 1
            guard let gt = s[r.upperBound...].firstIndex(of: ">") else {
                return nil
            }
            if depth == 0 {
                return (inner: r.lowerBound, outer: s.index(after: gt))
            }
            at = s.index(after: gt)
        }
        return nil
    }

    /// `<summary>title</summary>` out of a `<details>` block's inside;
    /// the rest is the body. Missing summary → "Details".
    static func splitSummary(_ inside: Substring) -> (title: String, body: Substring) {
        guard let open = firstTag(in: inside, named: "summary"),
              let close = matchingClose(in: inside, named: "summary",
                                        from: open.upperBound) else {
            return ("Details", inside)
        }
        let title = String(inside[open.upperBound..<close.inner])
            .trimmingCharacters(in: .whitespacesAndNewlines)
        var body = inside
        body.removeSubrange(open.lowerBound..<close.outer)
        return (title.isEmpty ? "Details" : title, body)
    }

    // MARK: - converter

    struct Converter {
        let s: String
        var i: String.Index
        var out = ""
        var prefixes: [String] = []      // "> " while inside blockquotes
        var linkUrls: [String] = []      // href stack for <a> nesting
        var listDepth = 0

        init(_ html: String) {
            s = html
            i = html.startIndex
        }

        var prefix: String { prefixes.joined() }

        mutating func run() {
            while i < s.endIndex {
                if s[i] == "<", let (tag, end) = readTag() {
                    i = end   // past the tag — handle() may scan ahead
                    handle(tag)
                } else {
                    let next = s[i...].firstIndex(of: "<") ?? s.endIndex
                    text(s[i..<next])
                    i = next
                }
            }
        }

        /// Tag text from `<` to `>`, lowercased name + raw inside.
        /// Returns nil when `<` isn't a tag start.
        mutating func readTag() -> (tag: String, end: String.Index)? {
            var j = s.index(after: i)
            guard j < s.endIndex else { return nil }
            let closing = s[j] == "/"
            if closing { j = s.index(after: j) }
            guard j < s.endIndex, s[j].isLetter || s[j] == "!" else {
                return nil
            }
            var name = ""
            while j < s.endIndex, s[j].isLetter || s[j].isNumber {
                name.append(Character(s[j].lowercased()))
                j = s.index(after: j)
            }
            if name.isEmpty, s[j] == "!" {
                // <!-- comment --> or <!doctype>
                if let r = s.range(of: "-->", range: j..<s.endIndex) {
                    return ("comment", r.upperBound)
                }
                guard let gt = s[j...].firstIndex(of: ">") else { return nil }
                return ("doctype", s.index(after: gt))
            }
            guard let gt = s[j...].firstIndex(of: ">") else { return nil }
            let attrs = String(s[j..<gt])
            let nameOut = (closing ? "/" : "") + name
            return (nameOut + "\n" + attrs, s.index(after: gt))
        }

        mutating func handle(_ tag: String) {
            let parts = tag.split(separator: "\n", maxSplits: 1)
            var name = String(parts.first ?? "")
            let attrs = parts.count > 1 ? String(parts[1]) : ""
            let closing = name.hasPrefix("/")
            if closing { name.removeFirst() }
            switch name {
            case "comment", "doctype": break
            case "script", "style", "textarea", "template":
                if !closing, let end = matchingClose(
                    in: s[i...], named: name, from: i) {
                    i = end.outer
                }
            case "a":
                if closing {
                    out += "](" + (linkUrls.popLast() ?? "") + ")"
                } else {
                    linkUrls.append(attr(attrs, "href") ?? "")
                    out += "["
                }
            case "code", "tt":
                out += "`"
            case "strong", "b":
                out += "**"
            case "em", "i":
                out += "*"
            case "s", "del", "strike":
                out += "~~"
            case "ul", "ol":
                if closing {
                    listDepth = max(0, listDepth - 1); newline()
                } else {
                    newline()
                    listDepth += 1
                }
            case "li":
                if !closing {
                    newline()
                    out += String(repeating: "  ", count: max(0, listDepth - 1)) + "- "
                }
            case "p", "div", "section", "article", "figure", "figcaption",
                 "header", "footer", "aside", "main", "center", "form":
                newline(2)
            case "br", "wbr":
                newline()
            case "hr":
                newline(2); out += "---"; newline(2)
            case "blockquote":
                if closing {
                    prefixes.popLast(); newline(2)
                } else {
                    newline(2); prefixes.append("> ")
                }
            case "h1", "h2", "h3", "h4", "h5", "h6":
                if closing { newline(2) } else {
                    newline(2)
                    out += String(repeating: "#",
                                  count: Int(String(name.last!)) ?? 2) + " "
                }
            case "pre":
                if closing { newline(2) } else {
                    newline(2); out += "```\n"
                    if let end = matchingClose(in: s[i...], named: "pre",
                                               from: i) {
                        let inner = decodeEntities(
                            String(s[i..<end.inner]))
                        out += inner.trimmingCharacters(
                            in: .newlines) + "\n```"
                        i = end.outer
                    }
                    newline(2)
                }
            case "table":
                if !closing, let end = matchingClose(
                    in: s[i...], named: "table", from: i) {
                    newline(2)
                    table(String(s[i..<end.inner]))
                    newline(2)
                    i = end.outer
                }
            case "img":
                if !closing {
                    if let src = attr(attrs, "src") {
                        let alt = attr(attrs, "alt") ?? "image"
                        out += "[\(alt)](\(src))"
                    }
                }
            case "details", "summary", "thead", "tbody", "tfoot",
                 "tr", "td", "th", "caption", "colgroup", "col":
                break  // handled at their own level
            default:
                break  // span, kbd, sub, sup, mark, … keep content
            }
        }

        /// A `<table>` region → one bullet line per body row:
        /// `- **first cell** → next → next`. Header (`th`) rows skip.
        mutating func table(_ inside: String) {
            var rows: [[String]] = []
            var at = inside.startIndex
            while let open = firstTag(in: inside[at...], named: "tr"),
                  let close = matchingClose(in: inside[at...], named: "tr",
                                            from: open.upperBound) {
                let inner = inside[open.upperBound..<close.inner]
                var cells: [String] = []
                var isHeader = false
                var ct = inner.startIndex
                while let cell = [firstTag(in: inner[ct...], named: "td"),
                                  firstTag(in: inner[ct...], named: "th")]
                        .compactMap({ $0 })
                        .min(by: { $0.lowerBound < $1.lowerBound }) {
                    let tagName = inner[cell.lowerBound...]
                        .hasPrefix("<th") ? "th" : "td"
                    if tagName == "th" { isHeader = true }
                    guard let cend = matchingClose(in: inner[ct...],
                                                 named: tagName,
                                                 from: cell.upperBound)
                    else { break }
                    let text = inlineMarkdown(
                        String(inner[cell.upperBound..<cend.inner]))
                    cells.append(text)
                    ct = cend.outer
                }
                if !cells.isEmpty && !isHeader { rows.append(cells) }
                at = close.outer
            }
            for cells in rows {
                newline()
                out += "- **" + cells[0] + "**"
                for cell in cells.dropFirst() where !cell.isEmpty {
                    out += " → " + cell
                }
            }
            if !rows.isEmpty { newline() }
        }

        /// Text run: decode entities, then newlines carry the active
        /// blockquote prefix so `> ` continues on every source line.
        mutating func text(_ t: Substring) {
            var piece = decodeEntities(String(t))
            if !prefix.isEmpty {
                piece = piece.replacingOccurrences(of: "\n",
                                                   with: "\n" + prefix)
            }
            out += piece
        }

        mutating func newline(_ n: Int = 1) {
            var have = 0
            for ch in out.reversed() {
                if ch == "\n" { have += 1 } else { break }
            }
            for _ in have..<max(have, n) { out += "\n" }
            if !prefix.isEmpty, !out.hasSuffix(prefix) {
                out += prefix
            }
        }

        /// Collapse blank runs, trim line tails, and turn GFM pipe
        /// tables into the same bullets as `<table>` — Apple's
        /// markdown renderer would otherwise fuse the cells into one
        /// unreadable paragraph.
        func finish() -> String {
            var lines = out.components(separatedBy: "\n")
            lines = lines.map {
                $0.replacingOccurrences(of: "[ \\t]+$", with: "",
                                        options: .regularExpression)
            }
            var result: [String] = []
            for line in lines {
                if line.isEmpty, result.last == "" { continue }
                result.append(line)
            }
            while result.first == "" { result.removeFirst() }
            while result.last == "" { result.removeLast() }
            return Self.pipeTables(result).joined(separator: "\n")
        }

        /// `| h | h |` + `| --- |` + data rows → `- **c0** → c1 → c2`
        /// per row. The header drops, same as `<th>`.
        static func pipeTables(_ lines: [String]) -> [String] {
            func isRow(_ l: String) -> Bool {
                l.trimmingCharacters(in: .whitespaces).hasPrefix("|")
            }
            func isSep(_ l: String) -> Bool {
                l.contains("-") && l.allSatisfy {
                    $0 == "|" || $0 == "-" || $0 == ":" || $0 == " "
                }
            }
            var out: [String] = []
            var i = 0
            while i < lines.count {
                guard isRow(lines[i]), i + 1 < lines.count,
                      isSep(lines[i + 1]) else {
                    out.append(lines[i]); i += 1; continue
                }
                i += 2   // header + separator
                while i < lines.count, isRow(lines[i]) {
                    var cells = lines[i].split(separator: "|",
                                               omittingEmptySubsequences: false)
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                    cells = Array(cells.drop(while: { $0.isEmpty }))
                    while cells.last == "" { cells.removeLast() }
                    if let first = cells.first {
                        out.append("- **" + first + "**"
                            + cells.dropFirst()
                                .filter { !$0.isEmpty }
                                .map { " → " + $0 }.joined())
                    }
                    i += 1
                }
            }
            return out
        }

        /// `attr("href")`-style pull from raw tag attributes.
        func attr(_ attrs: String, _ key: String) -> String? {
            guard let k = attrs.range(of: key, options: .caseInsensitive)
            else { return nil }
            var j = k.upperBound
            while j < attrs.endIndex, attrs[j].isWhitespace {
                j = attrs.index(after: j)
            }
            guard j < attrs.endIndex, attrs[j] == "=" else { return nil }
            j = attrs.index(after: j)
            while j < attrs.endIndex, attrs[j].isWhitespace {
                j = attrs.index(after: j)
            }
            guard j < attrs.endIndex else { return nil }
            let quote = attrs[j]
            var value = ""
            if quote == "\"" || quote == "'" {
                let end = attrs[attrs.index(after: j)...]
                    .firstIndex(of: quote) ?? attrs.endIndex
                value = String(attrs[attrs.index(after: j)..<end])
            } else {
                var e = j
                while e < attrs.endIndex,
                      !attrs[e].isWhitespace { e = attrs.index(after: e) }
                value = String(attrs[j..<e])
            }
            return value.isEmpty ? nil : decodeEntities(value)
        }

        func decodeEntities(_ t: String) -> String {
            var r = t
            let named: [(String, String)] = [
                ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                ("&quot;", "\""), ("&apos;", "'"), ("&#39;", "'"),
                ("&nbsp;", " "), ("&#8203;", ""), ("&#x200b;", ""),
                ("&#x200B;", ""), ("&hellip;", "…"), ("&mdash;", "—"),
                ("&ndash;", "–"), ("&middot;", "·"),
            ]
            for (k, v) in named {
                r = r.replacingOccurrences(of: k, with: v)
            }
            // &#NNN; / &#xHH;
            while let m = r.range(of: "&#(\\d+|x[0-9a-fA-F]+);",
                                  options: .regularExpression) {
                let digits = r[m].dropFirst(2).dropLast()
                let scalar: UInt32? = digits.hasPrefix("x")
                    ? UInt32(digits.dropFirst(), radix: 16)
                    : UInt32(digits)
                guard let n = scalar, let u = Unicode.Scalar(n) else { break }
                r.replaceSubrange(m, with: String(Character(u)))
            }
            return r
        }
    }
}
