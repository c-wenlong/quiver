import Foundation

/// One normalized transcript message headed for the full-text search index.
/// Per-harness record shapes are flattened to `(role, text, ts)` by the
/// decoders in `SearchLineDecoders.swift`; the store consumes index rows built
/// from these via `SearchChunker`.
public enum SearchRole: String, Sendable, Equatable {
    case user, assistant, tool
}

public struct SearchMessage: Sendable, Equatable {
    public var role: SearchRole
    /// The message body, head-capped at `SearchText.messageLimit`. Raw
    /// transcript text — harness envelope markup is left in, since the point
    /// of the index is that anything the session said is findable.
    public var text: String
    /// The record's own timestamp string (usually ISO-8601), kept verbatim;
    /// nil when the line carried none.
    public var ts: String?

    public init(role: SearchRole, text: String, ts: String? = nil) {
        self.role = role
        self.text = text
        self.ts = ts
    }
}

/// Per-line session metadata a decoder surfaces so the indexer can fill its
/// `sessions` rows. Every field arrives repeatedly across a file, so merge by
/// keeping the latest non-nil. That rule resolves `title` too: the first user
/// prompt yields a fallback candidate, and an explicit harness title record
/// (a rename, an auto-title, codex's `session_meta` name) yields the real one
/// later — or earlier for codex, in which case the decoder suppresses the
/// prompt candidate so the order still lands on the explicit title.
public struct SearchHints: Sendable, Equatable {
    public var sessionId: String?
    public var cwd: String?
    public var branch: String?
    public var title: String?
    /// Codex only: the thread was spawned by Codex itself — a subagent or an
    /// internal run, not one the user started. Orca drops these outright;
    /// surfacing the flag lets the indexer decide.
    public var nonUserOrigin = false
    /// This record's user text is harness-injected machinery — Claude's
    /// `isMeta`, CodeBuddy's `providerData.skipRun`, a known injected-turn
    /// tag. The turn still indexes as a user message, but it never seeds the
    /// first-prompt title (Orca's `metaTitle`/`seedFirstUserPrompt` gate).
    public var injectedUserTurn = false

    public init(sessionId: String? = nil, cwd: String? = nil, branch: String? = nil, title: String? = nil) {
        self.sessionId = sessionId
        self.cwd = cwd
        self.branch = branch
        self.title = title
    }
}

/// Truncation and text helpers shared by the search decoders. Every cap is in
/// UTF-16 code units — the unit Orca's TypeScript limits count — and every
/// cut lands on a `Character` boundary, so output never holds a torn
/// surrogate pair or half a grapheme cluster.
enum SearchText {
    /// Orca's `TRANSCRIPT_MESSAGE_TEXT_LIMIT`: a safety bound only, so one
    /// pathological paste cannot dominate a scan. Consumers apply their own
    /// caps on top (`SearchContent.toolArgumentLimit`,
    /// `SearchChunker.toolRowLimit`).
    static let messageLimit = 256 * 1024

    /// `text` cut to `limit` UTF-16 units and trimmed, or nil when nothing
    /// non-whitespace remains (Orca's `boundedText`).
    static func bounded(_ text: String, limit: Int = messageLimit) -> String? {
        let trimmed = truncate(text, limit: limit)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// `text` cut to `limit` UTF-16 units, never inside a `Character`.
    static func truncate(_ text: String, limit: Int) -> String {
        guard text.utf16.count > limit else { return text }
        var used = 0
        var end = text.endIndex
        var index = text.startIndex
        while index < text.endIndex {
            used += text[index].utf16Count
            if used > limit {
                end = index
                break
            }
            index = text.index(after: index)
        }
        return String(text[..<end])
    }

    /// `value` when it holds anything but whitespace, else nil. The string is
    /// returned untrimmed — callers that need the clean form take it.
    static func nonBlank(_ value: String?) -> String? {
        guard let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        return value
    }

    /// A first-user-prompt title candidate: whitespace runs folded to single
    /// spaces and capped small, or nil when nothing is left.
    static func titleCandidate(_ text: String, limit: Int = 256) -> String? {
        let collapsed = text.split { $0.isWhitespace }.joined(separator: " ")
        return bounded(collapsed, limit: limit)
    }
}

extension Character {
    /// UTF-16 code units this `Character` occupies (a supplementary-plane
    /// scalar is two).
    var utf16Count: Int {
        unicodeScalars.reduce(0) { $0 + ($1.value > 0xFFFF ? 2 : 1) }
    }
}
