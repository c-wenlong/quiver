import Foundation

/// One rate-limit window of one account or harness, in the shape the menu
/// bar, notifications and widgets share. Never carries a credential.
public struct LimitReading: Sendable, Equatable, Identifiable, Codable {
    /// Stable across fetches: the source plus the window, so one window's
    /// crossing is remembered as one crossing.
    public var id: String { "\(source)|\(window)" }
    /// The account's profile id, or the harness name for limits from swe.
    public var source: String
    /// Which harness, for its colour.
    public var harness: String
    /// "Claude: Work", "Devin".
    public var title: String
    /// "5h", "7d", "7d Opus"; empty when the provider names none.
    public var window: String
    /// 0 to 100.
    public var usedPercent: Double
    public var resetsAt: Date?
    public var fetchedAt: Date?
    /// An older reading shown because the last fetch failed or was skipped.
    public var isStale: Bool

    public init(source: String, harness: String, title: String, window: String, usedPercent: Double,
                resetsAt: Date?, fetchedAt: Date?, isStale: Bool) {
        self.source = source
        self.harness = harness
        self.title = title
        self.window = window
        self.usedPercent = usedPercent
        self.resetsAt = resetsAt
        self.fetchedAt = fetchedAt
        self.isStale = isStale
    }

    /// The percentage as shown everywhere: a whole number.
    public var percent: Int { Int(usedPercent.rounded()) }

    /// True when the window's reset time has passed since the reading.
    public func hasReset(now: Date) -> Bool {
        resetsAt.map { $0 <= now } ?? false
    }

    /// True when the reading can still be believed: not stale, and its window
    /// has not reset since it was taken.
    public func isCurrent(now: Date) -> Bool {
        !isStale && !hasReset(now: now)
    }

    /// How old a reading may be before the widgets dim it. The app refetches
    /// every five minutes while it runs; older means it stopped.
    public static let widgetMaxAge: TimeInterval = 30 * 60

    /// Stale, or not refreshed for `widgetMaxAge`. `fallback` stands in for a
    /// missing fetch time, such as when the snapshot was written.
    public func isOld(now: Date, fallback: Date? = nil) -> Bool {
        guard !isStale else { return true }
        guard let taken = fetchedAt ?? fallback else { return false }
        return now.timeIntervalSince(taken) > Self.widgetMaxAge
    }

    /// How old a reading from swe may be before it counts as stale. swe
    /// refreshes every five minutes and the app asks as often, so anything
    /// older is swe's outage fallback (up to a day old), not a fresh fetch.
    public static let sweStaleAfter: TimeInterval = 20 * 60

    /// The cutoff for "stale": the floor above, or the refresh window plus
    /// five minutes when the user widened it. With a 120-minute window swe
    /// legitimately serves hour-old healthy readings; that is not outage.
    public static func sweStaleLimit(freshFor: TimeInterval = 0) -> TimeInterval {
        max(sweStaleAfter, freshFor + 300)
    }

    /// Every account's windows, then every other harness swe reported a
    /// usable limit for.
    public static func all(cards: [AccountsModel.Card], others: [OtherLimitsModel.Row], now: Date,
                           sweFreshFor: TimeInterval = 0) -> [LimitReading] {
        let accounts = cards.flatMap { card -> [LimitReading] in
            guard let usage = card.usage else { return [] }
            let profile = card.profile
            return usage.windows.map { window in
                LimitReading(source: profile.id, harness: profile.vendor.rawValue,
                             title: "\(profile.vendor.title): \(profile.name)", window: window.label,
                             usedPercent: window.usedPercent, resetsAt: window.resetsAt,
                             fetchedAt: usage.fetchedAt, isStale: usage.isStale)
            }
        }
        let harnesses = others.flatMap { row -> [LimitReading] in
            guard row.limit.status == .ok else { return [] }
            let isStale = row.limit.fetchedAt.map {
                now.timeIntervalSince($0) > sweStaleLimit(freshFor: sweFreshFor) } ?? true
            return row.limit.allWindows.map { window in
                // An unavailable window holds last-known values: as stale
                // as the outage reading swe falls back to.
                LimitReading(source: row.id, harness: row.name, title: row.accountTitle,
                             window: window.window, usedPercent: Double(window.usedPercent),
                             resetsAt: window.resetAt, fetchedAt: row.limit.fetchedAt,
                             isStale: isStale || window.unavailable)
            }
        }
        return accounts + harnesses
    }

    /// The limit closest to stopping you: the most used current window. On
    /// a tie, the one that resets last, since you are stuck with it longer.
    public static func tightest(_ readings: [LimitReading], now: Date) -> LimitReading? {
        readings.filter { $0.isCurrent(now: now) }.max { a, b in
            if a.percent != b.percent { return a.percent < b.percent }
            return (a.resetsAt ?? .distantPast) < (b.resetsAt ?? .distantPast)
        }
    }

    /// Current readings most used first, then the ones that can't be trusted.
    public static func byPressure(_ readings: [LimitReading], now: Date) -> [LimitReading] {
        let current = readings.filter { $0.isCurrent(now: now) }.sorted { $0.usedPercent > $1.usedPercent }
        return current + readings.filter { !$0.isCurrent(now: now) }
    }
}
