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

    /// True when the reading can still be believed: not stale, and its window
    /// has not reset since it was taken.
    public func isCurrent(now: Date) -> Bool {
        !isStale && !(resetsAt.map { $0 <= now } ?? false)
    }

    /// Every account's windows, then every other harness swe reported a
    /// usable limit for.
    public static func all(cards: [AccountsModel.Card], others: [OtherLimitsModel.Row]) -> [LimitReading] {
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
        let harnesses = others.compactMap { row -> LimitReading? in
            guard row.limit.status == .ok else { return nil }
            return LimitReading(source: row.name, harness: row.name, title: row.name.capitalized,
                                window: row.limit.window ?? "", usedPercent: Double(row.limit.usedPercent),
                                resetsAt: row.limit.resetAt, fetchedAt: row.limit.fetchedAt, isStale: false)
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
