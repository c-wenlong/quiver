import Foundation

// Per-session unread dots on the Sessions list (#1439), the same rules
// the web app and the iPhone follow.
//
// The hub keeps a last-read cursor per device and session, and every
// `GET /v1/sessions` row carries `unread` for the calling device: this
// app reads as its own paired device ("Quiver for Mac"). `LocalHub`'s
// list read lays that flag over the Sessions rows the hub lists, local
// rows matched by `harness:id` and the Other machines group alike, and a
// row the hub doesn't list has no dot.
//
// A chat open on screen posts `POST /v1/sessions/{key}/read` when it
// opens, again (debounced) as messages arrive, and once more on close. `UnreadTracker` hides a dot at once rather than
// waiting for the next list read to bring the hub's answer.

/// What the hub answers a read: the cursor it stored.
public struct HubReadReply: Decodable, Sendable, Equatable {
    public var ok: Bool?
    /// Epoch seconds: the later of now and the row's `last_active_at`.
    public var at: Double?
}

/// Which dots to hide before the hub says so. Pure, so the rules test
/// without a hub.
public struct UnreadTracker: Equatable, Sendable {
    /// The chat on screen: its row never shows a dot.
    public private(set) var openKey: String?
    /// Reads posted here, by key: the activity (epoch seconds) the read
    /// covered. A row whose `last_active_at` is no later stays read until
    /// the hub's own flag catches up.
    public private(set) var marks: [String: Double] = [:]

    public init() {}

    /// Whether `key`'s row shows a dot: the hub says unread, it isn't the
    /// chat on screen, and no read posted here covers its activity.
    public func isUnread(_ key: String, serverUnread: Bool, lastActiveAt: Double?) -> Bool {
        guard serverUnread, key != openKey else { return false }
        if let mark = marks[key], mark >= (lastActiveAt ?? 0) { return false }
        return true
    }

    /// The chat for `key` opened: no dot while it shows, and none after
    /// for what it covered.
    public mutating func open(_ key: String, activity: Double?, now: Double) {
        openKey = key
        mark(key, activity: activity, now: now)
    }

    /// The chat for `key` closed, having seen up to `activity`. A close
    /// of a chat no longer on screen (another opened since) leaves that
    /// one alone.
    public mutating func close(_ key: String, activity: Double?, now: Double) {
        if openKey == key { openKey = nil }
        mark(key, activity: activity, now: now)
    }

    /// The hub caps a read at the later of now and the row's activity
    /// (a satellite clock running ahead), so the mark does too. A mark
    /// never moves back.
    mutating func mark(_ key: String, activity: Double?, now: Double) {
        let at = max(activity ?? 0, now)
        marks[key] = max(marks[key] ?? 0, at)
    }

    /// A fresh list read: a mark goes once the hub has caught up (its
    /// row reads as read), once the row has moved past it (new activity
    /// since, which a dot should show), or with the row itself.
    public mutating func reconcile(_ rows: [HubSessionRow]) {
        guard !marks.isEmpty else { return }
        let byKey = Dictionary(rows.map { ($0.key, $0) }, uniquingKeysWith: { _, newest in newest })
        marks = marks.filter { key, mark in
            guard let row = byKey[key], row.unread == true else { return false }
            return mark >= (row.lastActiveAt ?? 0)
        }
    }
}

extension HubClient {
    /// `POST /v1/sessions/{key}/read {}`: moves this device's cursor to
    /// now (the hub caps it), so the row reads as read on the next list.
    @discardableResult
    public func markRead(_ key: String) async throws -> HubReadReply {
        try await send(request("POST", Self.sessionPath(key, "read"), body: [:]))
    }
}

extension LocalHub {
    /// Whether `key`'s row shows the unread dot: the hub's flag from the
    /// last list read, less the chat on screen and the reads posted here
    /// since. False for a key the hub doesn't list.
    public func isUnread(_ key: String) -> Bool {
        guard let meta = overlay[key] else { return false }
        return unread.isUnread(key, serverUnread: meta.unread, lastActiveAt: meta.lastActiveAt)
    }

    /// The Sessions row's dot; never for a cloud row, which no hub lists.
    public func isUnread(_ row: SessionsModel.Row) -> Bool {
        HubSessionOverlay.key(row).map(isUnread) ?? false
    }

    /// The chat for `key` is on screen: its dot goes at once.
    public func chatOpened(_ key: String, now: Double = Date().timeIntervalSince1970) {
        unread.open(key, activity: overlay[key]?.lastActiveAt, now: now)
    }

    /// The chat for `key` closed, having shown activity up to `activity`.
    public func chatClosed(_ key: String, activity: Double?, now: Double = Date().timeIntervalSince1970) {
        let listed = overlay[key]?.lastActiveAt
        unread.close(key, activity: max(activity ?? 0, listed ?? 0), now: now)
    }

    /// Posts a read for `key`. A hub that is down, older than read
    /// state, or doesn't list the session changes nothing here: the
    /// tracker already hid the dot.
    public func postRead(_ key: String) async {
        _ = try? await withClient { try await $0.markRead(key) }
    }
}
