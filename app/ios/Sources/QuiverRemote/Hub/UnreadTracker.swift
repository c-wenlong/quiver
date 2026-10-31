import Foundation

/// The unread dots on session rows (#1439). The hub flags a row `unread`
/// for this device when it moved after this phone's last-read cursor;
/// this lays what the phone already knows over that flag, so a dot goes
/// out the moment a chat opens instead of a poll later.
///
/// The Mac app and the web app keep the same rules; change all three
/// together.
struct UnreadTracker: Equatable {
    /// The chat on screen. It never shows a dot, whatever the list says.
    private(set) var openKey: String?
    /// Session key to the activity stamp (`last_active_at`) this phone
    /// cleared its dot at. A row at or before its mark shows no dot; one
    /// that moved past it has new activity and lights up again.
    private(set) var marks: [String: Double] = [:]

    func isUnread(_ row: SessionRow) -> Bool {
        guard row.unread == true, row.key != openKey else { return false }
        if let mark = marks[row.key], mark >= (row.lastActiveAt ?? 0) { return false }
        return true
    }

    /// A chat opened: no dot on it while it stays open, and none after
    /// for what it held at `stamp`.
    mutating func open(_ key: String, at stamp: Double?) {
        openKey = key
        mark(key, at: stamp)
    }

    /// A chat closed after showing everything up to `stamp`. Another
    /// chat opened since keeps its place as the open one.
    mutating func close(_ key: String, at stamp: Double?) {
        if openKey == key { openKey = nil }
        mark(key, at: stamp)
    }

    /// Clears the dot for activity up to `stamp`. A mark never moves back.
    mutating func mark(_ key: String, at stamp: Double?) {
        let at = stamp ?? 0
        marks[key] = max(marks[key] ?? at, at)
    }

    /// A fresh list arrived. A mark stays only while it is still needed:
    /// its row is listed, the hub still says unread (a list fetched before
    /// the hub saw the read), and the row hasn't moved past it. Once the
    /// hub says read, its flag is the truth from then on.
    mutating func reconcile(_ rows: [SessionRow]) {
        let byKey = Dictionary(rows.map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first })
        marks = marks.filter { key, mark in
            guard let row = byKey[key], row.unread == true else { return false }
            return mark >= (row.lastActiveAt ?? 0)
        }
    }
}

/// When the open chat's read cursor goes to the hub. Opening posts at
/// once (`ReadPoster`); after that each new message pushes a trailing
/// post `interval` past it, so a burst of replies costs one request, and
/// a post still waiting goes out when the chat closes. Pure: the caller
/// passes the time.
struct ReadDebounce: Equatable {
    static let interval: TimeInterval = 3

    /// The chat with a post waiting, and when it is due.
    private(set) var key: String?
    private(set) var due: Double?

    /// A message arrived in `key`'s chat at `now`. Returns another chat's
    /// waiting post, which must go out now rather than be lost.
    mutating func message(_ key: String, now: Double) -> String? {
        let other = self.key.flatMap { $0 == key ? nil : $0 }
        self.key = key
        due = now + Self.interval
        return other
    }

    /// The waiting post's key once it is due, which clears it.
    mutating func fire(now: Double) -> String? {
        guard let due, now >= due else { return nil }
        return take()
    }

    /// The waiting post, due or not, which clears it. With a `key`, only
    /// that chat's post.
    mutating func flush(_ key: String? = nil) -> String? {
        guard self.key != nil, key == nil || key == self.key else { return nil }
        return take()
    }

    private mutating func take() -> String? {
        defer { key = nil; due = nil }
        return key
    }
}

/// Runs `ReadDebounce` against a clock: posts on open, schedules the
/// trailing post, flushes on close. `post` fires a request and returns
/// at once; tests swap the clock and the sleep.
@MainActor
final class ReadPoster {
    private(set) var schedule = ReadDebounce()
    private let post: (String) -> Void
    private let now: () -> Double
    private let sleep: (TimeInterval) async -> Void
    private var timer: Task<Void, Never>?
    /// Bumped by `reset()`, so a cancelled timer can't clear a newer one.
    private var generation = 0

    init(now: @escaping () -> Double = { Date().timeIntervalSince1970 },
         sleep: @escaping (TimeInterval) async -> Void = { try? await Task.sleep(for: .seconds($0)) },
         post: @escaping (String) -> Void) {
        self.now = now
        self.sleep = sleep
        self.post = post
    }

    /// A chat opened: its cursor goes now. A post another chat left
    /// waiting goes first.
    func opened(_ key: String) {
        if let other = schedule.flush(), other != key { post(other) }
        post(key)
    }

    /// A message arrived in the open chat.
    func message(_ key: String) {
        if let other = schedule.message(key, now: now()) { post(other) }
        arm()
    }

    /// The chat closed: a post it left waiting goes now.
    func closed(_ key: String) {
        if let waiting = schedule.flush(key) { post(waiting) }
    }

    /// Another hub went on screen: nothing waiting may reach it.
    func reset() {
        generation += 1
        timer?.cancel()
        timer = nil
        schedule = ReadDebounce()
    }

    /// Waits out the timer, for tests.
    func settle() async { await timer?.value }

    private func arm() {
        guard timer == nil else { return }
        let gen = generation
        timer = Task { [weak self] in
            while let self, gen == self.generation, let due = self.schedule.due {
                let wait = due - self.now()
                if wait > 0 { await self.sleep(wait) }
                guard !Task.isCancelled, gen == self.generation else { return }
                if let key = self.schedule.fire(now: self.now()) { self.post(key) }
            }
            if let self, gen == self.generation { self.timer = nil }
        }
    }
}
