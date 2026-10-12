import Foundation

/// Where markdown styling goes in a run of text: line structure first
/// (fenced blocks, headings, quotes, lists), then inline marks inside what
/// the structure left. Ranges are UTF-16, ready for NSTextStorage.
///
/// Detection is deliberately simple: fenced code wins and nothing inside a
/// fence or a code span gets other styling; a `<!-- -->` comment is styled
/// last so it mutes whatever it covers.
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

    private static let fence = try! NSRegularExpression(pattern: #"^ {0,3}(```|~~~)"#)
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
        var occupied: [NSRange] = []
        /// Fenced blocks, where nothing else may style.
        var protected: [NSRange] = []

        func free(_ range: NSRange) -> Bool {
            range.length > 0 && !occupied.contains { NSIntersectionRange($0, range).length > 0 }
        }
        func claim(_ range: NSRange, _ kind: Kind) {
            guard range.length > 0 else { return }
            spans.append(Span(range: range, kind: kind))
            occupied.append(range)
        }

        var start = 0
        var inFence = false
        while start < ns.length {
            let lineRange = ns.lineRange(for: NSRange(location: start, length: 0))
            var content = lineRange
            while content.length > 0 {
                let last = ns.character(at: NSMaxRange(content) - 1)
                if last == 0x0A || last == 0x0D { content.length -= 1 } else { break }
            }
            let line = ns.substring(with: content)
            let lineLength = (line as NSString).length
            let fullLine = NSRange(location: 0, length: lineLength)
            func at(_ range: NSRange) -> NSRange {
                NSRange(location: range.location + content.location, length: range.length)
            }
            start = NSMaxRange(lineRange)

            if inFence {
                protected.append(content)
                if Self.fence.firstMatch(in: line, range: fullLine) != nil {
                    claim(content, .fence)
                    inFence = false
                } else {
                    claim(content, .codeBlock)
                }
                continue
            }
            if Self.fence.firstMatch(in: line, range: fullLine) != nil {
                protected.append(content)
                claim(content, .fence)
                inFence = true
                continue
            }

            if let m = Self.heading.firstMatch(in: line, range: fullLine) {
                claim(at(m.range), .headingMarker)
                let level = min(6, m.range(at: 1).length)
                claim(at(NSRange(location: NSMaxRange(m.range), length: lineLength - NSMaxRange(m.range))),
                      .heading(level))
            }
            if let m = Self.quoteMarker.firstMatch(in: line, range: fullLine) {
                claim(at(m.range), .marker)
                claim(at(NSRange(location: NSMaxRange(m.range), length: lineLength - NSMaxRange(m.range))),
                      .quote)
            }
            if let m = Self.listMarker.firstMatch(in: line, range: fullLine) {
                claim(at(m.range(at: 1)), .marker)
            }

            for m in Self.codeSpan.matches(in: line, range: fullLine) where free(at(m.range)) {
                claim(at(m.range), .inlineCode)
            }
            for m in Self.link.matches(in: line, range: fullLine) {
                let name = m.range(at: 1)
                if name.length > 0, free(at(name)) { claim(at(name), .link) }
                // The `(url)` tail, parentheses included.
                let urlStart = NSMaxRange(m.range(at: 1)) + 1
                let url = NSRange(location: urlStart, length: NSMaxRange(m.range) - urlStart)
                if free(at(url)) { claim(at(url), .linkURL) }
            }
            for m in Self.bold.matches(in: line, range: fullLine) {
                claimEmphasis(at: at, open: m.range(at: 1), inner: m.range(at: 2),
                              close: NSRange(location: NSMaxRange(m.range(at: 2)),
                                             length: m.range(at: 1).length),
                              kind: .bold, free: free, claim: claim)
            }
            for m in Self.strike.matches(in: line, range: fullLine) {
                claimEmphasis(at: at, open: m.range(at: 1), inner: m.range(at: 2),
                              close: m.range(at: 3), kind: .strike, free: free, claim: claim)
            }
            for m in Self.italic.matches(in: line, range: fullLine) {
                claimEmphasis(at: at, open: m.range(at: 1), inner: m.range(at: 2),
                              close: NSRange(location: NSMaxRange(m.range(at: 2)),
                                             length: m.range(at: 1).length),
                              kind: .italic, free: free, claim: claim)
            }
        }

        // Comments mute whatever they cover, so they go on last.
        for m in Self.comment.matches(in: text, range: NSRange(location: 0, length: ns.length))
        where !protected.contains(where: { NSIntersectionRange($0, m.range).length > 0 }) {
            spans.append(Span(range: m.range, kind: .comment))
        }
        return spans
    }

    /// The open and close markers claim `marker`, the inside claims `kind`;
    /// a candidate that touches something already claimed is dropped whole.
    private static func claimEmphasis(at: (NSRange) -> NSRange, open: NSRange, inner: NSRange,
                                      close: NSRange, kind: Kind, free: (NSRange) -> Bool,
                                      claim: (NSRange, Kind) -> Void) {
        guard free(at(open)), free(at(inner)), free(at(close)) else { return }
        claim(at(open), .marker)
        claim(at(close), .marker)
        claim(at(inner), kind)
    }
}
