import Foundation

/// Identifier shadow text for one message body — Orca's
/// `identifierShadowTerms` joined back into a string for the FTS
/// `identifiers` column. Tokens that are "interesting" (they contain a
/// `_./-` separator, a camelCase boundary, or are SCREAMING) are split on
/// separators and camel humps, deduplicated case-insensitively and
/// space-joined, so `jsonlFiles`, `Ledger.swift`, `MAX_RETRY` and
/// `~/.claude/projects/foo` still hit on a piece of the identifier.
///
/// Tokens are ASCII by definition, so the scan runs over UTF-8 bytes; a
/// multibyte character is never inside one.
public func identifierShadowText(_ text: String, limit: Int = 4000) -> String {
    SearchIdentifierSplit.terms(in: text, limit: limit).joined(separator: " ")
}

private enum SearchIdentifierSplit {
    /// Orca's token gate: `[A-Za-z0-9_./-]{3,120}` plus INTERESTING.
    static let minToken = 3
    static let maxToken = 120
    /// Pieces shorter than this are dropped (Orca's `MIN_PIECE`).
    static let minPiece = 2

    static func terms(in text: String, limit: Int) -> [String] {
        let bytes = Array(text.utf8)
        var out: [String] = []
        var seen = Set<String>()
        var index = 0
        while index < bytes.count, out.count < limit {
            guard isTokenByte(bytes[index]) else {
                index += 1
                continue
            }
            var end = index + 1
            while end < bytes.count && isTokenByte(bytes[end]) { end += 1 }
            defer { index = end }
            let token = bytes[index..<end]
            guard token.count >= minToken, token.count <= maxToken, isInteresting(token)
            else { continue }
            for piece in pieces(of: token) where out.count < limit {
                append(piece, into: &out, seen: &seen)
                if hasMixedCase(piece) {
                    for sub in camelPieces(piece) where out.count < limit {
                        append(sub, into: &out, seen: &seen)
                    }
                }
            }
        }
        return out
    }

    static func append(_ piece: ArraySlice<UInt8>, into out: inout [String], seen: inout Set<String>) {
        let lowered = String(decoding: piece, as: UTF8.self).lowercased()
        guard lowered.count >= minPiece, seen.insert(lowered).inserted else { return }
        out.append(lowered)
    }

    /// The non-separator runs inside a token (`[_./-]+` is the separator).
    static func pieces(of token: ArraySlice<UInt8>) -> [ArraySlice<UInt8>] {
        var pieces: [ArraySlice<UInt8>] = []
        var index = token.startIndex
        while index < token.endIndex {
            guard !isSeparator(token[index]) else {
                index += 1
                continue
            }
            var end = index + 1
            while end < token.endIndex && !isSeparator(token[end]) { end += 1 }
            pieces.append(token[index..<end])
            index = end
        }
        return pieces
    }

    /// Orca's INTERESTING: the token holds a `_./-` separator, a
    /// lower-or-digit → upper camel boundary, or is SCREAMING
    /// (`^[A-Z]{2,}[0-9_]*$` — an all-uppercase head then only digits and
    /// underscores, so `MAX_RETRY` qualifies but `MAX2RETRY` does not).
    static func isInteresting(_ token: ArraySlice<UInt8>) -> Bool {
        var previousLowOrDigit = false
        for byte in token {
            if isSeparator(byte) { return true }
            if isUpper(byte), previousLowOrDigit { return true }
            previousLowOrDigit = isLower(byte) || isDigit(byte)
        }
        var index = token.startIndex
        var upperHead = 0
        while index < token.endIndex && isUpper(token[index]) {
            upperHead += 1
            index += 1
        }
        guard upperHead >= 2 else { return false }
        return token[index...].allSatisfy { isDigit($0) || $0 == UInt8(ascii: "_") }
    }

    /// Orca's `CAMEL_PIECE` (`[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+`,
    /// alternation order significant): an uppercase run gives up its last
    /// letter to a following lowercase (`JSONParser` → `JSON` + `Parser`), a
    /// lone upper opens a lower/digit run (`Ab2`), and digits sit with
    /// lowercase (`a1`).
    static func camelPieces(_ piece: ArraySlice<UInt8>) -> [ArraySlice<UInt8>] {
        var out: [ArraySlice<UInt8>] = []
        var index = piece.startIndex
        while index < piece.endIndex {
            if isUpper(piece[index]) {
                var end = index + 1
                while end < piece.endIndex && isUpper(piece[end]) { end += 1 }
                if end < piece.endIndex && isLower(piece[end]) {
                    if end - index == 1 {
                        var tail = end
                        while tail < piece.endIndex && (isLower(piece[tail]) || isDigit(piece[tail])) { tail += 1 }
                        out.append(piece[index..<tail])
                        index = tail
                    } else {
                        out.append(piece[index..<end - 1])
                        index = end - 1
                    }
                } else {
                    out.append(piece[index..<end])
                    index = end
                }
            } else {
                var end = index + 1
                while end < piece.endIndex && (isLower(piece[end]) || isDigit(piece[end])) { end += 1 }
                out.append(piece[index..<end])
                index = end
            }
        }
        return out
    }

    static func hasMixedCase(_ piece: ArraySlice<UInt8>) -> Bool {
        piece.contains(where: isLower) && piece.contains(where: isUpper)
    }

    static func isTokenByte(_ byte: UInt8) -> Bool {
        isUpper(byte) || isLower(byte) || isDigit(byte) || isSeparator(byte)
    }

    static func isSeparator(_ byte: UInt8) -> Bool {
        byte == UInt8(ascii: "_") || byte == UInt8(ascii: ".")
            || byte == UInt8(ascii: "/") || byte == UInt8(ascii: "-")
    }

    static func isUpper(_ byte: UInt8) -> Bool { byte >= 65 && byte <= 90 }
    static func isLower(_ byte: UInt8) -> Bool { byte >= 97 && byte <= 122 }
    static func isDigit(_ byte: UInt8) -> Bool { byte >= 48 && byte <= 57 }
}
