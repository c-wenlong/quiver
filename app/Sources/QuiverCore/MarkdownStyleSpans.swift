import Foundation

/// Where markdown styling goes in a run of text: line structure first
/// (fenced blocks, headings, quotes, lists), then inline marks inside what
/// the structure left. Ranges are UTF-16, ready for NSTextStorage.
///
/// Two families of ranges: blocking ranges (fences, code blocks, inline
/// code) that nothing else may touch, and style ranges (everything else)
/// that may overlap each other, so `# **Title**` is a heading AND bold.
/// Emphasis may wrap a blocking range but never overlaps a claim of the
/// same kind, and a `<!-- -->` comment is styled last so it mutes
/// whatever it covers. Claims are kept per line, so overlap checks stay
/// local: only fences and multi-line comments span lines, and both are
/// tracked separately.
///
/// Comments are found before inline code so `<!-- `code` -->` is still a
/// comment, while code that holds a comment marker stays code: the
/// container wins.
public enum MarkdownStyleSpans {
    public enum Kind: Equatable, Sendable {
        case heading(Int)
        case headingMarker
        case bold
        case italic
        case strike
        /// A muted structural mark: emphasis markers, list bullets, `>`.
        case marker
        case inlineCode
        case codeBlock
        case fence
        case link
        case linkURL
        case quote
        case comment
    }

    public struct Span: Equatable, Sendable {
        public var range: NSRange
        public var kind: Kind

        public init(range: NSRange, kind: Kind) {
            self.range = range
            self.kind = kind
        }
    }

    private static let comment = try! NSRegularExpression(pattern: #"<!--.*?-->"#,
                                                          options: [.dotMatchesLineSeparators])
    private static let heading = try! NSRegularExpression(pattern: #"^(#{1,6})(?:[ \t]+|$)"#)
    private static let quoteMarker = try! NSRegularExpression(pattern: #"^((?:[ \t]*>[ \t]?)+)"#)
    private static let listMarker = try! NSRegularExpression(pattern: #"^[ \t]*([-*+]|\d{1,9}[.)])[ \t]+"#)
    private static let codeSpan = try! NSRegularExpression(pattern: #"`[^`\n]+`"#)
    private static let link = try! NSRegularExpression(pattern: #"\[([^\]\n]*)\]\(([^)\n]*)\)"#)
    private static let bold = try! NSRegularExpression(pattern: #"(\*\*|__)([^\n]+?)\1"#)
    private static let strike = try! NSRegularExpression(pattern: #"(~~)([^\n]+?)(~~)"#)
    private static let italic = try! NSRegularExpression(pattern: #"(\*|_)([^\s*_](?:[^\n]*?[^\s*_])??)\1"#)

    public static func spans(in text: String) -> [Span] {
        let ns = text as NSString
        guard ns.length > 0 else { return [] }
        var spans: [Span] = []

        func emit(_ range: NSRange, _ kind: Kind) {
            guard range.length > 0 else { return }
            spans.append(Span(range: range, kind: kind))
        }

        // One split into lines, shared by both passes.
        var contents: [NSRange] = []
        var lines: [NSString] = []
        var start = 0
        while start < ns.length {
            let lineRange = ns.lineRange(for: NSRange(location: start, length: 0))
            var content = lineRange
            while content.length > 0 {
                let last = ns.character(at: NSMaxRange(content) - 1)
                if last == 0x0A || last == 0x0D { content.length -= 1 } else { break }
            }
            contents.append(content)
            lines.append(ns.substring(with: content) as NSString)
            start = NSMaxRange(lineRange)
        }

        // Pass 1: fenced blocks claim whole lines; nothing else may
        // touch what a fence holds.
        var fenced: Set<Int> = []
        var inFence = false
        var fenceMark: unichar = 0
        var fenceLength = 0
        for (index, line) in lines.enumerated() {
            if inFence {
                fenced.insert(index)
                // A closing fence uses the same character, is at least as
                // long as the opener, and has nothing after it but space.
                if let run = Self.fenceRun(in: line), run.mark == fenceMark,
                   run.length >= fenceLength, Self.blankTail(in: line, from: run.end) {
                    emit(contents[index], .fence)
                    inFence = false
                } else {
                    emit(contents[index], .codeBlock)
                }
                continue
            }
            if let run = Self.fenceRun(in: line) {
                fenced.insert(index)
                emit(contents[index], .fence)
                fenceMark = run.mark
                fenceLength = run.length
                inFence = true
            }
        }

        // Comments are found before inline code: `<!-- `code` -->` is
        // still a comment, so a code span inside a comment is never
        // code. Code that holds a comment marker stays code and drops
        // the comment. Comments emitted last mute what they cover.
        var comments = Self.comment
            .matches(in: text, range: NSRange(location: 0, length: ns.length))
            .map(\.range)
            .filter { match in
                !fenced.contains {
                    NSIntersectionRange(contents[$0], match).length > 0
                }
            }

        // Pass 2: line structure and inline styles on the lines the
        // fences left.
        for (index, line) in lines.enumerated() where !fenced.contains(index) {
            let content = contents[index]
            let lineLength = line.length
            let fullLine = NSRange(location: 0, length: lineLength)
            func at(_ range: NSRange) -> NSRange {
                NSRange(location: range.location + content.location, length: range.length)
            }

            // Headings, quotes and list bullets emit but never claim:
            // inline marks inside them still get styled.
            if let m = Self.heading.firstMatch(in: line as String, range: fullLine) {
                emit(at(m.range), .headingMarker)
                let level = min(6, m.range(at: 1).length)
                emit(at(NSRange(location: NSMaxRange(m.range),
                                length: lineLength - NSMaxRange(m.range))), .heading(level))
            }
            if let m = Self.quoteMarker.firstMatch(in: line as String, range: fullLine) {
                emit(at(m.range), .marker)
                emit(at(NSRange(location: NSMaxRange(m.range),
                                length: lineLength - NSMaxRange(m.range))), .quote)
            }
            if let m = Self.listMarker.firstMatch(in: line as String, range: fullLine) {
                emit(at(m.range(at: 1)), .marker)
            }

            // This line's claims: `blocked` is what nothing else may
            // touch (inline code), `markers` keeps one emphasis's
            // markers out of another's, and the per-kind arrays stop
            // same-kind overlap. All reset per line, so every check
            // scans only this line.
            var blocked: [NSRange] = []
            var markers: [NSRange] = []
            var boldClaims: [NSRange] = []
            var italicClaims: [NSRange] = []
            var strikeClaims: [NSRange] = []

            for m in Self.codeSpan.matches(in: line as String, range: fullLine) {
                let range = at(m.range)
                guard range.length > 0,
                      !blocked.contains(where: { NSIntersectionRange($0, range).length > 0 })
                else { continue }
                if comments.contains(where: { NSIntersectionRange($0, range).length > 0 }) {
                    // Inside a comment the backticks are just text; a
                    // comment inside the code is the code's problem.
                    if comments.contains(where: {
                        range.location >= $0.location && NSMaxRange(range) <= NSMaxRange($0)
                    }) { continue }
                    comments.removeAll { NSIntersectionRange($0, range).length > 0 }
                }
                emit(range, .inlineCode)
                blocked.append(range)
            }
            for m in Self.link.matches(in: line as String, range: fullLine) {
                let name = m.range(at: 1)
                if name.length > 0,
                   !blocked.contains(where: { NSIntersectionRange($0, at(name)).length > 0 }) {
                    emit(at(name), .link)
                }
                // The `(url)` tail, parentheses included.
                let urlStart = NSMaxRange(m.range(at: 1)) + 1
                let url = at(NSRange(location: urlStart,
                                     length: NSMaxRange(m.range) - urlStart))
                if url.length > 0,
                   !blocked.contains(where: { NSIntersectionRange($0, url).length > 0 }) {
                    emit(url, .linkURL)
                }
            }
            for m in Self.bold.matches(in: line as String, range: fullLine) {
                Self.claimEmphasis(at: at, open: m.range(at: 1), inner: m.range(at: 2),
                                   close: NSRange(location: NSMaxRange(m.range(at: 2)),
                                                  length: m.range(at: 1).length),
                                   kind: .bold, blocked: blocked,
                                   markers: &markers, mine: &boldClaims, emit: emit)
            }
            for m in Self.strike.matches(in: line as String, range: fullLine) {
                Self.claimEmphasis(at: at, open: m.range(at: 1), inner: m.range(at: 2),
                                   close: m.range(at: 3), kind: .strike, blocked: blocked,
                                   markers: &markers, mine: &strikeClaims, emit: emit)
            }
            for m in Self.italic.matches(in: line as String, range: fullLine) {
                Self.claimEmphasis(at: at, open: m.range(at: 1), inner: m.range(at: 2),
                                   close: NSRange(location: NSMaxRange(m.range(at: 2)),
                                                  length: m.range(at: 1).length),
                                   kind: .italic, blocked: blocked,
                                   markers: &markers, mine: &italicClaims, emit: emit)
            }
        }

        for range in comments { emit(range, .comment) }
        return spans
    }

    /// A fence marker at the start of a line: up to three spaces, then a
    /// run of three or more backticks or tildes. `end` is where the run
    /// stops, so callers can check what trails it.
    private static func fenceRun(in line: NSString) -> (mark: unichar, length: Int, end: Int)? {
        var i = 0
        while i < line.length, i < 3, line.character(at: i) == 0x20 { i += 1 }
        guard i < line.length else { return nil }
        let mark = line.character(at: i)
        guard mark == 0x60 || mark == 0x7E else { return nil }
        var end = i
        while end < line.length, line.character(at: end) == mark { end += 1 }
        guard end - i >= 3 else { return nil }
        return (mark, end - i, end)
    }

    /// True when everything after `location` is spaces or tabs.
    private static func blankTail(in line: NSString, from location: Int) -> Bool {
        var i = location
        while i < line.length {
            let c = line.character(at: i)
            if c != 0x20 && c != 0x09 { return false }
            i += 1
        }
        return true
    }

    /// The markers claim `marker` and the inside claims `kind` in as many
    /// segments as blocking ranges leave. A marker touching a blocking
    /// range, a claimed marker, or a same-kind claim drops the candidate;
    /// the inside may still wrap blocking ranges (code stays code-only).
    private static func claimEmphasis(at: (NSRange) -> NSRange, open: NSRange, inner: NSRange,
                                      close: NSRange, kind: Kind, blocked: [NSRange],
                                      markers: inout [NSRange], mine: inout [NSRange],
                                      emit: (NSRange, Kind) -> Void) {
        let open = at(open), inner = at(inner), close = at(close)
        func hits(_ range: NSRange, _ list: [NSRange]) -> Bool {
            list.contains { NSIntersectionRange($0, range).length > 0 }
        }
        guard !hits(open, blocked), !hits(close, blocked),
              !hits(open, markers), !hits(close, markers),
              !hits(open, mine), !hits(close, mine), !hits(inner, mine) else { return }
        emit(open, .marker)
        emit(close, .marker)
        markers.append(open)
        markers.append(close)
        mine.append(open)
        mine.append(close)
        var cursor = inner.location
        for cut in blocked where NSIntersectionRange(cut, inner).length > 0 {
            let gapEnd = min(cut.location, NSMaxRange(inner))
            if gapEnd > cursor {
                let gap = NSRange(location: cursor, length: gapEnd - cursor)
                emit(gap, kind)
                mine.append(gap)
            }
            cursor = max(cursor, NSMaxRange(cut))
        }
        if NSMaxRange(inner) > cursor {
            let tail = NSRange(location: cursor, length: NSMaxRange(inner) - cursor)
            emit(tail, kind)
            mine.append(tail)
        }
    }
}
