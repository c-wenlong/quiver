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

/// A markdown block — the unit a body renders as. Lists keep their
/// items (depth, bullet/number/task) so the view can draw real
/// structure instead of marker characters.
public enum MarkdownBlock: Equatable, Sendable {
    case heading(Int, String)
    case paragraph(String)
    case code(String, String?)
    case quote([MarkdownBlock])
    case list([MarkdownListItem])
    case rule
}

public struct MarkdownListItem: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case bullet
        case ordered(Int)
        case task(Bool)
    }
    public var depth: Int
    public var kind: Kind
    public var text: String

    public init(depth: Int, kind: Kind, text: String) {
        self.depth = depth
        self.kind = kind
        self.text = text
    }
}

public enum GitHubMarkdown {

    /// How many `<details>` levels fold. A body is untrusted (anyone can
    /// open an issue), and each level re-scans its inside, so deeper
    /// blocks render as prose instead of folding further.
    static let maxDetailsNesting = 16

    /// Split `body` into renderable segments, top-level `<details>`
    /// blocks first. Unclosed or malformed blocks stay as text.
    public static func segments(_ body: String) -> [MarkdownSegment] {
        segments(body, level: 0)
    }

    private static func segments(_ body: String, level: Int) -> [MarkdownSegment] {
        var out: [MarkdownSegment] = []
        var prose = ""
        var rest = Substring(body)
        while level < maxDetailsNesting, let range = firstTag(in: rest, named: "details") {
            prose += rest[..<range.lowerBound]
            if let end = matchingClose(in: rest, named: "details", from: range.upperBound) {
                let block = rest[range.upperBound..<end.inner]
                let (title, inner) = splitSummary(block)
                flush(&prose, into: &out)
                out.append(.details(title: inlineMarkdown(title),
                                    body: segments(String(inner), level: level + 1)))
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

    /// Flatten the bracket forms Apple's inline parser mangles before
    /// it runs: `[![alt](badge)](href)` — and the `!`-less twin
    /// `[[alt](badge)](href)` greptile writes — collapse to
    /// `[alt](href)`, and a bare `![alt](src)` image becomes a link to
    /// its source. An image badge is a label anyway; a link-in-link
    /// keeps the outer href since that is where a click would go.
    /// Backtick code spans pass through untouched — a documented
    /// example is text, not a badge.
    public static func flatLinks(_ md: String) -> String {
        var out = ""
        var rest = md[...]
        while let tick = rest.firstIndex(of: "`") {
            out += flattenLinks(String(rest[..<tick]))
            rest = rest[tick...]
            let run = rest.prefix { $0 == "`" }
            guard let close = rest.dropFirst(run.count)
                    .range(of: String(run)) else {
                return out + String(rest)   // unclosed — the rest is literal
            }
            // `rest` starts at the run, so this slice is the whole
            // code span, fences included — copied verbatim.
            out += rest[..<close.upperBound]
            rest = rest[close.upperBound...]
        }
        return out + flattenLinks(String(rest))
    }

    /// The badge rewrites for one span of non-code text.
    private static func flattenLinks(_ md: String) -> String {
        var md = md
        md = md.replacing(#/\[!?\[([^\]]*)\]\([^)]*\)\]\(([^)\s]+)\)/#) {
            "[\($0.1)](\($0.2))"
        }
        md = md.replacing(#/!\[([^\]]*)\]\(([^)\s]+)\)/#) {
            "[\($0.1)](\($0.2))"
        }
        return md
    }

    // MARK: - block parse

    /// Markdown → display blocks for the sheet. Line-scanned: fences,
    /// ATX and setext headings, `>` quotes (their contents re-parse,
    /// so nested blocks survive), `-`/`+`/`*` bullets, `N.` items,
    /// `[ ]`/`[x]` tasks and `---` rules; anything else accumulates
    /// into a paragraph whose soft breaks stay newlines — GitHub
    /// wraps issue and PR bodies hard, so a source line is a
    /// rendered line.
    public static func blocks(_ md: String) -> [MarkdownBlock] {
        var out: [MarkdownBlock] = []
        var para: [String] = []
        var items: [MarkdownListItem]?
        var quote: [String]?
        var code: [String]?
        var fence = ""
        var lang: String?

        func flushPara() {
            guard !para.isEmpty else { return }
            out.append(.paragraph(para.joined(separator: "\n")))
            para = []
        }
        func flushList() {
            guard let l = items, !l.isEmpty else { return }
            out.append(.list(l))
            items = nil
        }
        func flushQuote() {
            guard let q = quote, !q.isEmpty else { return }
            out.append(.quote(blocks(q.joined(separator: "\n"))))
            quote = nil
        }

        for raw in md.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = String(raw)
            if code != nil {
                if fenceClose(line, marker: fence) {
                    out.append(.code(code!.joined(separator: "\n"), lang))
                    code = nil; fence = ""; lang = nil
                } else { code!.append(line) }
                continue
            }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty {
                flushPara(); flushList(); flushQuote()
                continue
            }
            if let f = fenceOpen(line) {
                flushPara(); flushList(); flushQuote()
                code = []; fence = f.marker; lang = f.lang
                continue
            }
            if let (level, text) = heading(line) {
                flushPara(); flushList(); flushQuote()
                out.append(.heading(level, text))
                continue
            }
            if trimmed.allSatisfy({ $0 == "=" }), !para.isEmpty {
                // Setext h1: `===` directly under a paragraph.
                out.append(.heading(1, para.joined(separator: " ")))
                para = []
                continue
            }
            if isRule(trimmed) {
                // A dash-only line under paragraph text is a setext
                // h2, not a rule.
                if !para.isEmpty, trimmed.contains("-"),
                   !trimmed.contains("*"), !trimmed.contains("_") {
                    out.append(.heading(2, para.joined(separator: " ")))
                    para = []
                } else {
                    flushPara(); flushList(); flushQuote()
                    out.append(.rule)
                }
                continue
            }
            if let inner = quoteLine(line) {
                flushPara(); flushList()
                if quote == nil { quote = [] }
                quote!.append(inner)
                continue
            }
            if let item = listItem(line) {
                flushPara(); flushQuote()
                if items == nil { items = [] }
                items!.append(item)
                continue
            }
            flushList(); flushQuote()
            para.append(line)
        }
        if let c = code {   // unclosed fence: keep what was typed
            out.append(.code(c.joined(separator: "\n"), lang))
        }
        flushPara(); flushList(); flushQuote()
        return out
    }

    /// ```` ```swift ````-style fence open: marker (` ```` ` ``` or `~~~`)
    /// plus the optional info string. At most three leading spaces.
    private static func fenceOpen(_ line: String) -> (marker: String, lang: String?)? {
        guard line.prefix(while: { $0 == " " }).count <= 3 else { return nil }
        let t2 = line.trimmingCharacters(in: .whitespaces)
        if t2.hasPrefix("```") || t2.hasPrefix("~~~") {
            let marker = String(t2.prefix(3))
            let rest = t2.dropFirst(3).trimmingCharacters(in: .whitespaces)
            return (marker, rest.isEmpty ? nil : String(rest))
        }
        return nil
    }

    /// A line that closes the open fence: same marker, nothing after.
    private static func fenceClose(_ line: String, marker: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix(marker)
            && t.allSatisfy { String($0) == String(marker.first!) }
    }

    /// `#`–`###### ` ATX heading → (level, text); trailing `#`s strip.
    private static func heading(_ line: String) -> (Int, String)? {
        let t = line.trimmingCharacters(in: .whitespaces)
        var level = 0
        while level < t.count, t[t.index(t.startIndex, offsetBy: level)] == "#" {
            level += 1
        }
        guard level >= 1, level <= 6 else { return nil }
        let rest = t.dropFirst(level)
        guard rest.isEmpty || rest.hasPrefix(" ") || rest.hasPrefix("\t")
        else { return nil }
        var text = String(rest).trimmingCharacters(in: .whitespaces)
        if let cut = text.range(of: " #+$", options: .regularExpression) {
            text = String(text[..<cut.lowerBound])
        } else if text.allSatisfy({ $0 == "#" }) { text = "" }
        return (level, text)
    }

    /// `- - -`-style rule: at least three of one of `-`, `*`, `_`.
    private static func isRule(_ trimmed: String) -> Bool {
        let chars = trimmed.filter { $0 != " " }
        guard chars.count >= 3, let first = chars.first,
              "-*_".contains(first) else { return false }
        return chars.allSatisfy { $0 == first }
    }

    /// A `>`-prefixed line → its contents minus the marker (and one
    /// optional space); a bare `>` yields "".
    private static func quoteLine(_ line: String) -> String? {
        var s = Substring(line)
        var lead = 0
        while lead < 3, s.first == " " { s.removeFirst(); lead += 1 }
        guard s.first == ">" else { return nil }
        s.removeFirst()
        if s.first == " " { s.removeFirst() }
        return String(s)
    }

    /// `- `/`+ `/`* `/`1. ` list row → its item; a leading `[ ]`/`[x]`
    /// makes it a task. Two-space steps nest.
    private static func listItem(_ line: String) -> MarkdownListItem? {
        var s = Substring(line)
        var spaces = 0
        while s.first == " " { s.removeFirst(); spaces += 1 }
        var kind: MarkdownListItem.Kind?
        if let m = s.range(of: #"^([-*+])\s+"#, options: .regularExpression) {
            kind = .bullet
            s = s[m.upperBound...]
        } else if let m = s.range(of: #"^(\d+)[.)]\s+"#,
                                  options: .regularExpression) {
            let digits = s[m.lowerBound...].prefix(while: { $0.isNumber })
            kind = .ordered(Int(digits) ?? 1)
            s = s[m.upperBound...]
        }
        guard kind != nil else { return nil }
        var text = String(s)
        if let m = text.range(of: #"^\[( |x|X)\]\s+"#,
                              options: .regularExpression) {
            let done = text[text.index(after: m.lowerBound)] != " "
            kind = .task(done)
            text = String(text[m.upperBound...])
        }
        return MarkdownListItem(depth: spaces / 2, kind: kind!, text: text)
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
    ///
    /// One forward pass over the bytes: searching ahead for both the next
    /// open and the next close at every step made nested blocks cost the
    /// square of their depth per call, and a hostile body hung the page.
    static func matchingClose(in s: Substring, named name: String,
                              from: String.Index)
        -> (inner: String.Index, outer: String.Index)? {
        let bytes = s.utf8
        let tag = Array(name.lowercased().utf8)
        let lt = UInt8(ascii: "<"), gt = UInt8(ascii: ">"), slash = UInt8(ascii: "/")
        var depth = 1
        var at = from
        while let open = bytes[at...].firstIndex(of: lt) {
            var p = bytes.index(after: open)
            let closing = p < bytes.endIndex && bytes[p] == slash
            if closing { p = bytes.index(after: p) }
            guard let afterName = tagName(tag, in: bytes, at: p) else {
                at = bytes.index(after: open)
                continue
            }
            guard closing else {
                depth += 1
                at = afterName
                continue
            }
            depth -= 1
            guard let end = bytes[afterName...].firstIndex(of: gt) else { return nil }
            if depth == 0 { return (inner: open, outer: bytes.index(after: end)) }
            at = bytes.index(after: end)
        }
        return nil
    }

    /// The index just past `tag` (lowercase ASCII) when the bytes at `at`
    /// start with it, compared case-insensitively; nil otherwise.
    private static func tagName(_ tag: [UInt8], in bytes: Substring.UTF8View,
                                at: String.Index) -> String.Index? {
        var q = at
        for expected in tag {
            guard q < bytes.endIndex else { return nil }
            var byte = bytes[q]
            if byte >= UInt8(ascii: "A") && byte <= UInt8(ascii: "Z") { byte += 32 }
            guard byte == expected else { return nil }
            q = bytes.index(after: q)
        }
        return q
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
                    // A `<` that opens no tag ("a < b") is text; search past
                    // it, or the loop would find it again forever.
                    let from = s[i] == "<" ? s.index(after: i) : i
                    let next = s[from...].firstIndex(of: "<") ?? s.endIndex
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
                    _ = prefixes.popLast(); newline(2)
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

        /// `attr("href")`-style pull from raw tag attributes. Only a whole
        /// attribute name matches: `href` is not `data-href`, and text
        /// inside another attribute's quoted value is skipped.
        func attr(_ attrs: String, _ key: String) -> String? {
            var j = attrs.startIndex
            func skipBlanks() {
                while j < attrs.endIndex, attrs[j].isWhitespace {
                    j = attrs.index(after: j)
                }
            }
            while true {
                skipBlanks()
                guard j < attrs.endIndex else { return nil }
                let nameStart = j
                while j < attrs.endIndex, !attrs[j].isWhitespace,
                      attrs[j] != "=", attrs[j] != "/" {
                    j = attrs.index(after: j)
                }
                let name = attrs[nameStart..<j]
                if name.isEmpty { j = attrs.index(after: j); continue }  // a stray "/"
                skipBlanks()
                guard j < attrs.endIndex, attrs[j] == "=" else { continue }  // valueless
                j = attrs.index(after: j)
                skipBlanks()
                var value = ""
                if j < attrs.endIndex, attrs[j] == "\"" || attrs[j] == "'" {
                    let quote = attrs[j]
                    let from = attrs.index(after: j)
                    let end = attrs[from...].firstIndex(of: quote) ?? attrs.endIndex
                    value = String(attrs[from..<end])
                    j = end < attrs.endIndex ? attrs.index(after: end) : end
                } else {
                    let from = j
                    while j < attrs.endIndex, !attrs[j].isWhitespace {
                        j = attrs.index(after: j)
                    }
                    value = String(attrs[from..<j])
                }
                if name.caseInsensitiveCompare(key) == .orderedSame {
                    return value.isEmpty ? nil : decodeEntities(value)
                }
            }
        }

        /// HTML entities read in one left-to-right pass, so `&amp;lt;`
        /// stays `&lt;`. One a reader doesn't know (or a number past
        /// Unicode) stays as written and the rest still decode.
        func decodeEntities(_ t: String) -> String {
            guard t.contains("&") else { return t }
            let named: [Substring: String] = [
                "amp": "&", "lt": "<", "gt": ">", "quot": "\"", "apos": "'",
                "nbsp": " ", "hellip": "…", "mdash": "—", "ndash": "–",
                "middot": "·",
            ]
            var out = ""
            var rest = t[...]
            while let amp = rest.firstIndex(of: "&") {
                out += rest[..<amp]
                rest = rest[amp...]
                // `&#` takes every digit that follows (leading zeros
                // included); a name is letters and digits. Either needs
                // its `;`, else the `&` is text.
                let after = rest.dropFirst()
                let numeric = after.first == "#"
                let hex = numeric && (after.dropFirst().first == "x" || after.dropFirst().first == "X")
                let bodyStart = numeric ? after.dropFirst(hex ? 2 : 1) : after
                let body = bodyStart.prefix { ch in
                    numeric ? (hex ? ch.isHexDigit : ch.isASCII && ch.isNumber)
                            : ch.isASCII && (ch.isLetter || ch.isNumber)
                }
                var decoded: String?
                if !body.isEmpty, body.endIndex < rest.endIndex, rest[body.endIndex] == ";" {
                    if numeric {
                        if let n = UInt32(body, radix: hex ? 16 : 10), let u = Unicode.Scalar(n) {
                            decoded = n == 0x200B ? "" : String(Character(u))
                        }
                    } else {
                        decoded = named[body]
                    }
                }
                if let decoded {
                    out += decoded
                    rest = rest[rest.index(after: body.endIndex)...]
                } else {
                    out.append("&")
                    rest = rest.dropFirst()
                }
            }
            return out + rest
        }
    }
}
