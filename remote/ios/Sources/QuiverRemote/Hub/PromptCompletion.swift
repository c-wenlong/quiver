import Foundation
import Observation

// The prompt box's suggestion menus. Positions are Character offsets
// into the draft; `nil` as a cursor means the end of the text.
//
// iOS 18 reports the caret through `TextField(text:selection:)`; on
// iOS 17 the field can't say where the caret is, so the end of the
// text stands in for it there (see `PromptField`).

/// Converts between the draft's Character offsets and its indices.
enum Caret {
    /// `cursor` clamped into `text`; nil is the end.
    static func clamp(_ cursor: Int?, in text: String) -> Int {
        let count = text.count
        guard let cursor else { return count }
        return min(max(0, cursor), count)
    }

    static func index(_ offset: Int, in text: String) -> String.Index {
        text.index(text.startIndex, offsetBy: clamp(offset, in: text))
    }

    /// The offset of `index`, which may come from an older copy of the
    /// text: anything past the end counts as the end.
    static func offset(_ index: String.Index, in text: String) -> Int {
        guard index < text.endIndex else { return text.count }
        return text[..<index].count
    }

    /// The caret to keep after a pick put it at `offset`. Only a field
    /// that reports its caret (iOS 18) can hold a fixed offset: iOS 17's
    /// never updates it as you type, so there it stays nil and detection
    /// keeps following the end of the draft.
    static func afterPick(_ offset: Int, tracksCaret: Bool) -> Int? {
        tracksCaret ? offset : nil
    }

    /// Whether this iOS reports the prompt box's caret.
    static var tracked: Bool {
        if #available(iOS 18, *) { return true }
        return false
    }
}

/// An `@path` being typed: the `@` sits at the start of the text or after
/// whitespace, and runs to the caret with no whitespace in between. A
/// path with spaces is quoted, `@"My Documents/notes.txt"`, as Claude
/// Code writes it; while a folder is being browsed the quote stays open
/// (`@"My Documents/`).
struct MentionToken: Equatable {
    /// Offset of the `@`.
    let start: Int
    /// Offset just past the token's last character: the end of the word
    /// the caret is in, which a pick replaces whole.
    let end: Int
    /// What's typed between the `@` and the caret.
    let query: String
}

enum Mention {
    /// The `@token` the caret is in, if any. An `@` inside a word (an
    /// email such as a@b.com) never starts one.
    static func token(in text: String, cursor: Int?) -> MentionToken? {
        let chars = Array(text)
        let caret = Caret.clamp(cursor, in: text)
        if let quoted = quotedToken(chars, caret: caret) { return quoted }
        var start = caret
        while start > 0, !chars[start - 1].isWhitespace { start -= 1 }
        guard start < caret, chars[start] == "@" else { return nil }
        var end = caret
        while end < chars.count, !chars[end].isWhitespace { end += 1 }
        return MentionToken(start: start, end: end, query: String(chars[(start + 1)..<caret]))
    }

    /// `@"` at the start of the text or after whitespace, with no quote or
    /// newline between it and the caret. It runs to its closing quote, or,
    /// left open, to the end of the word the caret is in.
    private static func quotedToken(_ chars: [Character], caret: Int) -> MentionToken? {
        var q = caret - 1
        while q >= 0, chars[q] != "\"", !chars[q].isNewline { q -= 1 }
        guard q >= 1, chars[q] == "\"", chars[q - 1] == "@" else { return nil }
        let start = q - 1
        guard start == 0 || chars[start - 1].isWhitespace else { return nil }
        var end = caret
        while end < chars.count, chars[end] != "\"", !chars[end].isNewline { end += 1 }
        if end < chars.count, chars[end] == "\"" {
            end += 1
        } else {
            end = caret
            while end < chars.count, !chars[end].isWhitespace { end += 1 }
        }
        return MentionToken(start: start, end: end, query: String(chars[(q + 1)..<caret]))
    }

    /// The draft after picking `path`, and where the caret goes. A file
    /// becomes `@path ` with the caret after the space (an existing space
    /// is reused); a folder becomes `@path/` with the caret after the
    /// slash, so the menu stays open to pick inside it. A path with
    /// whitespace is quoted, `@"a b.txt" `, and a folder's quote is left
    /// open, `@"a b/`, until a file closes it.
    static func insert(_ path: String, isDir: Bool, replacing token: MentionToken, in text: String)
        -> (text: String, cursor: Int) {
        var chars = Array(text)
        let start = min(token.start, chars.count)
        let end = min(max(token.end, start), chars.count)
        let clean = trimmedSlash(path)
        let quoted = clean.contains(where: \.isWhitespace)
        var inserted = (quoted ? "@\"" : "@") + clean
        if isDir {
            inserted += "/"
        } else {
            if quoted { inserted += "\"" }
            if end >= chars.count || !chars[end].isWhitespace { inserted += " " }
        }
        chars.replaceSubrange(start..<end, with: Array(inserted))
        var cursor = start + inserted.count
        // A file before existing whitespace: step over it, as if the
        // space had been typed.
        if !isDir, !inserted.hasSuffix(" "), cursor < chars.count { cursor += 1 }
        return (String(chars), cursor)
    }

    static func trimmedSlash(_ path: String) -> String {
        var p = Substring(path)
        while p.hasSuffix("/"), p.count > 1 { p = p.dropLast() }
        return String(p)
    }

    /// "src/app/main.py" as its name and the folder it's in.
    static func split(_ path: String) -> (name: String, parent: String) {
        let clean = trimmedSlash(path)
        guard let slash = clean.lastIndex(of: "/") else { return (clean, "") }
        return (String(clean[clean.index(after: slash)...]), String(clean[..<slash]))
    }
}

/// The `@` menu's search: debounced, one request at a time, and the
/// last good results stay up while the next query loads.
@MainActor @Observable
final class MentionSearch {
    /// The token the caret is in, as the composer last reported it.
    private(set) var token: MentionToken?
    /// The newest results that came back, kept while a newer query loads.
    private(set) var entries: [FileEntry] = []
    /// The query `entries` answer; nil before any came back.
    private(set) var answered: String?
    private(set) var loading = false
    /// Why the last search failed, worded for the menu.
    private(set) var error: String?
    /// The token the menu was closed on (tapped outside); it stays closed
    /// until the token changes.
    private var dismissed: MentionToken?

    /// Where results come from; ChatModel points it at the hub.
    @ObservationIgnored var fetch: (String) async throws -> FilesResponse = { _ in FilesResponse(entries: []) }
    /// The session's machine, for the satellite error.
    @ObservationIgnored var machine: () -> String = { "that machine" }
    /// How the debounce waits; tests swap in a gate.
    @ObservationIgnored var pause: (Duration) async -> Void = { try? await Task.sleep(for: $0) }
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var generation = 0

    static let debounce: Duration = .milliseconds(120)
    static let limit = 50

    var isOpen: Bool { token != nil && token != dismissed }

    /// Called whenever the draft, caret or focus changes. Returns the
    /// search it started, if any, so tests can wait for it.
    @discardableResult
    func track(_ next: MentionToken?) -> Task<Void, Never>? {
        let before = isOpen ? token?.query : nil
        token = next
        if next == nil || dismissed != next { dismissed = nil }
        guard isOpen, let query = next?.query else {
            reset()
            return nil
        }
        guard query != before || (answered == nil && !loading && error == nil) else { return nil }
        return search(query)
    }

    /// Back from the background with the menu still open: `stop()` reset
    /// its search, and the token hasn't changed to start another.
    @discardableResult
    func resume() -> Task<Void, Never>? {
        guard isOpen, let query = token?.query, !loading, answered == nil, error == nil else { return nil }
        return search(query)
    }

    /// Closes the menu on the current token, until it changes.
    func dismiss() {
        guard token != nil else { return }
        dismissed = token
        reset()
    }

    /// Drops any search and its results.
    func reset() {
        generation += 1
        task?.cancel()
        task = nil
        entries = []
        answered = nil
        loading = false
        error = nil
    }

    private func search(_ query: String) -> Task<Void, Never> {
        generation += 1
        let gen = generation
        task?.cancel()
        loading = true
        let t = Task { [weak self] in
            guard let self else { return }
            await self.pause(Self.debounce)
            guard !Task.isCancelled, gen == self.generation else { return }
            do {
                let body = try await self.fetch(query)
                guard gen == self.generation else { return }
                self.entries = body.entries
                self.answered = query
                self.error = nil
            } catch {
                guard gen == self.generation else { return }
                self.entries = []
                self.answered = query
                self.error = Self.message(for: error, machine: self.machine())
            }
            self.loading = false
        }
        task = t
        return t
    }

    /// The one muted line the menu shows instead of results.
    static func message(for error: Error, machine: String) -> String {
        guard let e = error as? HubError else { return "Couldn't search files" }
        switch e.code {
        case "files_unavailable": return "File search isn't available for sessions on \(machine)"
        case "cwd_missing": return "This session's folder is gone"
        case "bad_cwd": return "This session has no working folder"
        case "not_readable": return "The hub can't read this session's folder"
        case "unknown_session": return "The hub doesn't know this session any more"
        case "network": return "Can't reach the hub"
        default:
            // A hub from before the route answers any unknown path 404.
            return e.status == 404 ? "Update the hub to search files from the phone" : "Couldn't search files"
        }
    }
}
