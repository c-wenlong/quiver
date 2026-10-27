import Foundation

/// Turns decoded messages into the rows the FTS table stores — Orca's
/// `session-search-message-rows.ts`:
///
/// - `tool` → ONE row, head-capped at `toolRowLimit`. Tool output is most of
///   a transcript's bytes, and a command plus the first lines of what it
///   printed are what a search looks for; the tail is the padding that makes
///   these messages large.
/// - `user`/`assistant` → rows of at most `chunkLimit`, cut at the last token
///   boundary in the window's second half so no token is torn in half (FTS5
///   rows can't match a phrase across a split). A window with no boundary in
///   its second half cuts at the limit — 4,000 units without a boundary is
///   not a word.
///
/// Sizes are UTF-16 code units (the unit Orca's caps count); every cut lands
/// on a `Character` boundary, so a surrogate pair or flag emoji can end a
/// row but is never torn. Empty and whitespace-only text yields no rows.
public enum SearchChunker {
    /// Orca's `TOOL_ROW_CHARS`.
    public static let toolRowLimit = 3072
    /// Orca's `CHUNK_TARGET_CHARS`.
    public static let chunkLimit = 8000

    public static func rows(from messages: [SearchMessage]) -> [SearchMessage] {
        var rows: [SearchMessage] = []
        for message in messages {
            if message.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            if message.role == .tool {
                rows.append(SearchMessage(
                    role: .tool,
                    text: SearchText.truncate(message.text, limit: toolRowLimit),
                    ts: message.ts))
                continue
            }
            for chunk in chunks(of: message.text) {
                rows.append(SearchMessage(role: message.role, text: chunk, ts: message.ts))
            }
        }
        return rows
    }

    /// `text` split into ≤`chunkLimit`-unit pieces on whole `Character`s.
    static func chunks(of text: String) -> [String] {
        guard text.utf16.count > chunkLimit else { return [text] }
        let characters = Array(text)
        // units[i] = the UTF-16 offset at which characters[i] starts.
        var units: [Int] = []
        units.reserveCapacity(characters.count)
        var offset = 0
        for character in characters {
            units.append(offset)
            offset += character.utf16Count
        }
        var rows: [String] = []
        var start = 0
        while start < characters.count {
            let windowEnd = units[start] + chunkLimit
            // `end` = first character that does not fit fully in the window.
            var end = start
            while end < characters.count && units[end] + characters[end].utf16Count <= windowEnd {
                end += 1
            }
            if end == start { end = start + 1 }  // one oversized cluster still progresses
            if end < characters.count {
                let floor = units[start] + chunkLimit / 2
                var index = end - 1
                while index > start && units[index] >= floor {
                    if isTokenBoundary(characters[index]) {
                        let split = index + 1
                        if split > start { end = split }
                        break
                    }
                    index -= 1
                }
            }
            rows.append(String(characters[start..<end]))
            start = end
        }
        return rows
    }

    /// A token boundary as FTS5's `unicode61` tokenizer (with
    /// `tokenchars '_.-/+'`) would see one: a `Character` holding no letter,
    /// number, private-use or `_.-/+` scalar is a boundary — except a cluster
    /// of pure combining marks, which the tokenizer folds onto its neighbour
    /// rather than breaking (Orca's `TOKEN_BOUNDARY` + `FOLDED_DIACRITIC`).
    static func isTokenBoundary(_ character: Character) -> Bool {
        var allMarks = true
        for scalar in character.unicodeScalars {
            if isTokenScalar(scalar) { return false }
            switch scalar.properties.generalCategory {
            case .nonspacingMark, .spacingMark, .enclosingMark:
                break
            default:
                allMarks = false
            }
        }
        return !allMarks
    }

    /// The scalar classes `unicode61` keeps inside a token when
    /// `tokenchars '_.-/+'` is set: letters, numbers, private use, `_.-/+`.
    static func isTokenScalar(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar {
        case "_", ".", "-", "/", "+": return true
        default: break
        }
        let properties = scalar.properties
        return properties.isAlphabetic || properties.numericType != nil
            || properties.generalCategory == .privateUse
    }
}
