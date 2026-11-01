import Foundation
import Observation

// The prompt box's `/` menu. Positions are Character offsets into the
// draft; `nil` as a cursor means the end of the text. The iPhone app's
// `PromptCompletion.swift` (app/ios) holds the same logic; the `@` menu
// is in `MentionCompletion.swift`.

/// Converts between the draft's Character offsets and its indices.
public enum Caret {
    /// `cursor` clamped into `text`; nil is the end.
    public static func clamp(_ cursor: Int?, in text: String) -> Int {
        let count = text.count
        guard let cursor else { return count }
        return min(max(0, cursor), count)
    }

    public static func index(_ offset: Int, in text: String) -> String.Index {
        text.index(text.startIndex, offsetBy: clamp(offset, in: text))
    }

    /// The offset of `index`, which may come from an older copy of the
    /// text: anything past the end counts as the end.
    public static func offset(_ index: String.Index, in text: String) -> Int {
        guard index < text.endIndex else { return text.count }
        return text[..<index].count
    }
}

/// A `/command` being typed: the box starts with `/` and the caret is
/// still in that first word.
public struct SlashToken: Equatable, Sendable {
    /// Offset just past the first word, which a pick replaces whole.
    public let end: Int
    /// What's typed between the `/` and the caret.
    public let query: String

    public init(end: Int, query: String) {
        self.end = end
        self.query = query
    }
}

public enum Slash {
    public static func token(in text: String, cursor: Int?) -> SlashToken? {
        let chars = Array(text)
        let caret = Caret.clamp(cursor, in: text)
        guard caret >= 1, chars.first == "/" else { return nil }
        guard !chars[0..<caret].contains(where: \.isWhitespace) else { return nil }
        var end = caret
        while end < chars.count, !chars[end].isWhitespace { end += 1 }
        return SlashToken(end: end, query: String(chars[1..<caret]))
    }

    /// Commands whose name starts with `query`, then those that hold it
    /// anywhere, case-insensitively; the hub's order within each.
    public static func filter(_ commands: [SlashCommand], query: String) -> [SlashCommand] {
        let q = query.lowercased()
        guard !q.isEmpty else { return commands }
        let prefix = commands.filter { $0.name.lowercased().hasPrefix(q) }
        let contains = commands.filter { !$0.name.lowercased().hasPrefix(q) && $0.name.lowercased().contains(q) }
        return prefix + contains
    }

    /// The box after picking `command`, with the caret after what went in
    /// (after its space, ready for an argument). An existing space after
    /// the word is reused.
    public static func insert(_ command: SlashCommand, replacing token: SlashToken, in text: String)
        -> (text: String, cursor: Int) {
        var chars = Array(text)
        let end = min(max(token.end, 0), chars.count)
        var inserted = command.insertText
        let reuseSpace = end < chars.count && chars[end].isWhitespace
        if reuseSpace { inserted = String(inserted.dropLast()) }
        chars.replaceSubrange(0..<end, with: Array(inserted))
        return (String(chars), inserted.count + (reuseSpace ? 1 : 0))
    }

    /// What a plain Return does while the `/` menu is open: a lit row
    /// picks it; once the list has answered with nothing pickable — no
    /// match, a failed fetch, an unsupported harness — the draft sends
    /// as typed; while the fetch is still out Return waits.
    public enum ReturnAction: Sendable, Equatable {
        case pick, send, wait

        /// `row` is whether a row is lit right now.
        public static func action(row: Bool, loading: Bool) -> ReturnAction {
            if row { return .pick }
            return loading ? .wait : .send
        }
    }

    /// What the menu says besides its rows: the line in place of an empty
    /// list, and muted footnotes under it.
    public static func notes(_ r: CommandsResponse, harness: String, machine: String, remote: Bool)
        -> (empty: String?, footnotes: [String]) {
        let supported = r.supported != false
        let empty = !supported || r.commands.isEmpty ? "No commands for \(harness)" : nil
        var footnotes: [String] = []
        let customMissing = supported && r.customAvailable == false
        if customMissing {
            footnotes.append(remote ? "Custom commands from \(machine) aren't available yet"
                                    : "Custom commands don't work in this session's mode")
        }
        if let note = r.note?.trimmingCharacters(in: .whitespacesAndNewlines), !note.isEmpty,
           !(customMissing && note.lowercased().contains("custom commands")) {
            footnotes.append(note.prefix(1).uppercased() + note.dropFirst())
        }
        return (empty, footnotes)
    }
}

/// The `/` menu's list: warmed when the chat opens so the first `/`
/// opens it already filled, filtered locally as you type, refetched by
/// `refresh()`. A port of the iPhone app's `CommandMenu`.
@MainActor
@Observable
public final class SlashCommandMenu {
    /// The token the caret is in, as the composer last reported it.
    public private(set) var token: SlashToken?
    public private(set) var response: CommandsResponse?
    public private(set) var loading = false
    /// Why the last fetch failed, worded for the menu.
    public private(set) var error: String?
    /// The token the menu was closed on (Escape); it stays closed until
    /// the token changes.
    private var dismissed: SlashToken?

    /// Where the list comes from; SessionChatModel points it at the hub.
    @ObservationIgnored public var fetch: () async throws -> CommandsResponse = {
        CommandsResponse(commands: [])
    }
    /// The session the list is for; set beside `fetch`. Answered lists
    /// are kept per key, so a reopened chat's menu is filled at once.
    @ObservationIgnored public var cacheKey: String?
    /// The hub the session lives on, read when a list is seeded or
    /// kept: sessions with no id share a path digest, so a same-keyed
    /// session on another hub must not inherit this one's commands.
    @ObservationIgnored public var scope: () -> String = { "" }
    /// Answered lists by scope + session key, kept for the app's run.
    private static var lists: [String: CommandsResponse] = [:]
    /// The newest request's serial per list key: an older answer landing
    /// late can still be kept for a chat that was left, but it never
    /// replaces a newer request's answer.
    private static var seqs: [String: Int] = [:]
    private static var nextSeq = 0
    @ObservationIgnored private var generation = 0

    /// `lists`' key for this menu: the hub scope plus the session key.
    private var listKey: String? { cacheKey.map { "\(scope())|\($0)" } }

    public init() {}

    public var isOpen: Bool { token != nil && token != dismissed }

    /// The commands that match what's typed, best first.
    public var matches: [SlashCommand] {
        Slash.filter(response?.commands ?? [], query: token?.query ?? "")
    }

    /// Fetches the list ahead of the first `/` — called when the chat
    /// opens. A kept list from an earlier open fills `response` at once
    /// and is still refreshed here.
    @discardableResult
    public func warm() -> Task<Void, Never>? {
        if response == nil, let key = listKey, let kept = Self.lists[key] {
            response = kept
        }
        guard !loading else { return nil }
        return Task { await load() }
    }

    /// Called whenever the draft, caret or focus changes. Returns the
    /// fetch it started, if any, so tests can wait for it.
    @discardableResult
    public func track(_ next: SlashToken?) -> Task<Void, Never>? {
        let wasOpen = isOpen
        token = next
        if next == nil || dismissed != next { dismissed = nil }
        if response == nil, let key = listKey { response = Self.lists[key] }
        guard Self.shouldLoad(open: isOpen, opening: !wasOpen, loaded: response != nil,
                              loading: loading, failed: error != nil) else { return nil }
        return Task { await load() }
    }

    /// An open menu fetches when it has no list and no failure to show
    /// (also mid-typing, after `reset()` dropped the list under it), and
    /// retries on opening after a failure, even with a list kept from an
    /// earlier load.
    public static func shouldLoad(open: Bool, opening: Bool, loaded: Bool, loading: Bool, failed: Bool) -> Bool {
        guard open, !loading else { return false }
        if failed { return opening }
        return !loaded
    }

    /// Fetches the list again.
    public func refresh() async { await load() }

    /// The menu is open but its list is gone (a `stop()`/`reset()` while
    /// it was on screen) and the token may not change to fetch again.
    @discardableResult
    public func resume() -> Task<Void, Never>? {
        guard isOpen, response == nil, !loading else { return nil }
        return Task { await load() }
    }

    /// Closes the menu on the current token, until it changes.
    public func dismiss() {
        guard token != nil else { return }
        dismissed = token
    }

    /// Forgets the list, so the next open fetches it again.
    public func reset() {
        generation += 1
        response = nil
        loading = false
        error = nil
    }

    private func load() async {
        generation += 1
        let gen = generation
        Self.nextSeq += 1
        let seq = Self.nextSeq
        if let key = listKey { Self.seqs[key] = seq }
        loading = true
        do {
            let body = try await fetch()
            // The scope can settle mid-fetch: a nil `current` client
            // then becomes the hub that answered. The kept list is
            // written under the key as it resolves now, and only while
            // this request is still the newest one the key saw.
            let key = listKey
            if let key, seq >= (Self.seqs[key] ?? 0) {
                Self.seqs[key] = seq
                Self.lists[key] = body
            }
            guard gen == generation else { return }
            response = body
            error = nil
        } catch {
            guard gen == generation else { return }
            self.error = Self.message(for: error)
        }
        loading = false
    }

    /// The one muted line the menu shows instead of the list.
    public static func message(for error: Error) -> String {
        guard let e = error as? HubError else { return "Couldn't load commands" }
        switch e.code {
        case "network": return "Can't reach the hub"
        case "unknown_session": return "The hub doesn't know this session any more"
        default:
            // A hub from before the route answers any unknown path 404.
            return e.status == 404 ? "Update the hub to list commands" : "Couldn't load commands"
        }
    }
}
