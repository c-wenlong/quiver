import Foundation

/// A run of snippet text; `matched` marks the terms FTS5 highlighted.
public struct SearchSnippetPart: Equatable, Sendable {
    public var text: String
    public var matched: Bool

    public init(text: String, matched: Bool) {
        self.text = text
        self.matched = matched
    }
}

/// Rendering for one `snippet()` pair — the plain window and the one with
/// private-use marks — into the `snippet`, `markedSnippet` and
/// `snippetParts` a hit carries. Ported from Orca's
/// `session-search-snippet.ts`.
///
/// The marks are U+E000/U+E001, private-use code points, and not `[[`,
/// because two jobs here have to tell a mark from content: refusing to cut
/// a snippet between an open mark and its close, and resolving the marks
/// into display runs. Transcripts contain `[[` — a bash `[[ -f x ]]`,
/// numpy's `[[1, 2]]` — so a mark the content can forge makes both of
/// those decisions wrong on real text.
enum SearchSnippet {
    static let markOpen: Unicode.Scalar = "\u{E000}"
    static let markClose: Unicode.Scalar = "\u{E001}"
    /// A ceiling on top of the token count: a transcript chunk can be
    /// 8,000 characters with no separator in it, which FTS5 reports as one
    /// token, so "twelve tokens" is not by itself a bound on what a hit
    /// carries.
    static let maxChars = 512

    /// One run of the snippet's own text, or one mark FTS5 put between runs.
    private enum Piece: Equatable {
        case text(String)
        case mark(Unicode.Scalar)
    }

    /// The marked rendering as its text and the marks FTS5 inserted into it.
    ///
    /// A mark is a private-use scalar the marked rendering has where the
    /// plain one has something else, so a Nerd Font glyph the transcript
    /// itself wrote stays text — replacing every private-use character
    /// would hand the renderer a highlight the content forged. Nil when the
    /// two renderings differ for any other reason, which is not a
    /// difference this can attribute. Compared per scalar, the same walk
    /// Orca does per code point.
    private static func split(marked: String, plain: String) -> [Piece]? {
        var pieces: [Piece] = []
        let rest = Array(plain.unicodeScalars)
        var at = 0
        var run = String.UnicodeScalarView()
        for point in marked.unicodeScalars {
            if at < rest.count, point == rest[at] {
                run.append(point)
                at += 1
                continue
            }
            guard point == markOpen || point == markClose else { return nil }
            pieces.append(.text(String(run)))
            pieces.append(.mark(point))
            run = String.UnicodeScalarView()
        }
        guard at == rest.count else { return nil }
        pieces.append(.text(String(run)))
        return pieces
    }

    /// The pair reconciled into `(text, marked, parts)`.
    ///
    /// `text` is the plain window capped at `maxChars` scalars. `marked` is
    /// the same text with the marks on, capped so the cut never lands
    /// between an open mark and its close (an open mark with no close hands
    /// the renderer something it can never close) — and empty when the
    /// pair can't be reconciled at all. `parts` is `marked` as display
    /// runs with whitespace collapsed: a transcript's own private-use
    /// scalars stay text because the parts are built from the validated
    /// walk, not by re-scanning the marked string.
    static func render(plain: String, marked: String)
        -> (text: String, marked: String, parts: [SearchSnippetPart]) {
        guard let pieces = split(marked: marked, plain: plain) else {
            let text = String(String.UnicodeScalarView(plain.unicodeScalars.prefix(maxChars)))
            return (text, "", [.init(text: normalized(text), matched: false)]
                .filter { !$0.text.isEmpty })
        }
        var text = String.UnicodeScalarView()
        var markedOut = String.UnicodeScalarView()
        var parts: [SearchSnippetPart] = []
        var run = String.UnicodeScalarView()
        var runMatched = false
        var shown = 0
        /// The output lengths where the still-open mark sits — where a cut
        /// inside it rolls back to.
        var open: (textLen: Int, markedLen: Int, partsCount: Int)?

        func flush() {
            let part = SearchSnippetPart(text: normalized(String(run)), matched: runMatched)
            run = String.UnicodeScalarView()
            guard !part.text.isEmpty else { return }
            if let last = parts.last, last.matched == part.matched {
                parts[parts.count - 1] = .init(text: last.text + part.text, matched: last.matched)
            } else {
                parts.append(part)
            }
        }

        for piece in pieces {
            switch piece {
            case .mark(let scalar):
                flush()
                runMatched = scalar == markOpen
                open = runMatched ? (text.count, markedOut.count, parts.count) : nil
                markedOut.append(scalar)
            case .text(let pieceText):
                let scalars = Array(pieceText.unicodeScalars)
                if shown + scalars.count <= maxChars {
                    shown += scalars.count
                    text.append(contentsOf: scalars)
                    markedOut.append(contentsOf: scalars)
                    run.append(contentsOf: scalars)
                } else {
                    // Over the cap: take what fits, then roll back to the
                    // open mark when the cut lands inside one.
                    let take = scalars.prefix(maxChars - shown)
                    text.append(contentsOf: take)
                    markedOut.append(contentsOf: take)
                    run.append(contentsOf: take)
                    flush()
                    if let open {
                        text = .init(text.prefix(open.textLen))
                        markedOut = .init(markedOut.prefix(open.markedLen))
                        parts = Array(parts.prefix(open.partsCount))
                    }
                    return (String(text), String(markedOut), parts)
                }
            }
        }
        flush()
        // A still-open mark at the end rolls back the same way — `split`
        // can't attest marks pair up, it only reads the difference.
        if let open {
            text = .init(text.prefix(open.textLen))
            markedOut = .init(markedOut.prefix(open.markedLen))
            parts = Array(parts.prefix(open.partsCount))
        }
        return (String(text), String(markedOut), parts)
    }

    /// Display text: whitespace runs collapse to one space, so a snippet
    /// stays a single line in the row.
    private static func normalized(_ text: String) -> String {
        text.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
    }
}
