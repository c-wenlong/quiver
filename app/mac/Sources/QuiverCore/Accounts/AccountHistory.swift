import Foundation

/// One week or month of tokens Quiver logged for an account. The
/// running period is `inProgress`, so its bar renders hatched.
public struct UsagePeriod: Sendable, Equatable {
    public var start: Date
    public var tokens: Int
    public var inProgress: Bool

    public init(start: Date, tokens: Int, inProgress: Bool) {
        self.start = start
        self.tokens = tokens
        self.inProgress = inProgress
    }
}

/// Every account card's usage history on the Accounts page: six weeks
/// and six months of the tokens Quiver logged for one harness and its
/// account homes, so every card shows the same chart whatever vendor it
/// is. Codex adds its own account-wide totals on top
/// (`CodexAccountUsage`); no other vendor shares one.
///
/// Only the keys the page shows are built, all in one ledger read, and
/// rebuilt after each ledger scan or write until the page hides them.
@MainActor
@Observable
public final class AccountHistory {
    /// Which ledger rows a card sums: one harness, over these account
    /// homes, or every home but `excluding` when `homes` is nil.
    public struct Key: Hashable, Sendable {
        public var harness: String
        public var homes: Set<String>?
        public var excluding: Set<String>

        public init(harness: String, homes: Set<String>?, excluding: Set<String> = []) {
            self.harness = harness
            self.homes = homes
            self.excluding = excluding
        }

        /// Whether a ledger row for `harness` in `home` belongs to this card.
        public func matches(harness: String, home: String) -> Bool {
            guard harness == self.harness else { return false }
            if let homes { return homes.contains(home) }
            return !excluding.contains(home)
        }
    }

    public struct History: Sendable, Equatable {
        public var weeks: [UsagePeriod]
        public var months: [UsagePeriod]
    }

    /// The last figures built per key; kept while hidden so showing
    /// history again paints at once, then rebuilt.
    public private(set) var histories: [Key: History] = [:]
    /// The keys on screen now: the only ones a scan rebuilds.
    @ObservationIgnored private var shown: Set<Key> = []
    /// Which build last started each key, so an older build finishing
    /// late cannot overwrite newer figures.
    @ObservationIgnored private var started: [Key: Int] = [:]
    @ObservationIgnored private var builds = 0

    /// Every key's tokens per local day, from one ledger read.
    private let days: ([Key], DateInterval) async -> [Key: [Date: Int]]
    private let now: () -> Date
    private let calendar: Calendar
    /// Six months back from the current one, plus a little slack.
    private static let lookback: TimeInterval = 200 * 86_400

    public init(days: @escaping ([Key], DateInterval) async -> [Key: [Date: Int]],
                now: @escaping () -> Date = Date.init,
                calendar: Calendar = .current) {
        self.days = days
        self.now = now
        self.calendar = calendar
    }

    /// The page shows these keys: build them now (cached figures stay up
    /// until the new ones land) and keep them fresh after each scan.
    public func show(_ keys: [Key]) async {
        shown = Set(keys)
        await build(shown)
    }

    /// History is hidden or the page went away: scans stop rebuilding.
    public func hide() {
        shown = []
    }

    /// The ledger moved on: rebuild every shown key, including those
    /// whose first build is still running.
    public func recompute() async {
        guard !shown.isEmpty else { return }
        await build(shown)
    }

    private func build(_ keys: Set<Key>) async {
        builds += 1
        let mine = builds
        for key in keys { started[key] = mine }
        let now = now()
        let range = DateInterval(start: now.addingTimeInterval(-Self.lookback), end: now)
        let read = await days(Array(keys), range)
        for key in keys where started[key] == mine {
            histories[key] = Self.history(days: read[key] ?? [:], now: now, calendar: calendar)
        }
    }

    /// A day map into six Monday-start weeks and six months, each ending
    /// at the current one.
    public static func history(days: [Date: Int], now: Date, calendar: Calendar) -> History {
        func periods(_ component: Calendar.Component) -> [UsagePeriod] {
            periodBounds(now: now, calendar: calendar, component: component, count: 6).map { bounds in
                let tokens = days.reduce(0) { sum, day in
                    bounds.contains(day.key) ? sum + day.value : sum
                }
                return UsagePeriod(start: bounds.start, tokens: tokens, inProgress: bounds.inProgress)
            }
        }
        return History(weeks: periods(.weekOfYear), months: periods(.month))
    }

    /// One bucket of `periodBounds`: `start` inclusive, `end` exclusive.
    struct PeriodBounds {
        var start: Date
        var end: Date
        var inProgress: Bool
        func contains(_ date: Date) -> Bool { date >= start && date < end }
    }

    /// `count` calendar periods ending at the current one, oldest first;
    /// the current one is `inProgress`. Weeks start on Monday whatever the
    /// calendar's own first weekday. Shared with the Codex account compare.
    nonisolated static func periodBounds(now: Date, calendar: Calendar, component: Calendar.Component,
                             count: Int) -> [PeriodBounds] {
        var calendar = calendar
        if component == .weekOfYear { calendar.firstWeekday = 2 }
        guard let current = calendar.dateInterval(of: component, for: now) else { return [] }
        return (0..<count).reversed().map { back in
            let start = calendar.date(byAdding: component, value: -back, to: current.start)!
            let end = calendar.date(byAdding: component, value: 1, to: start)!
            return PeriodBounds(start: start, end: end, inProgress: back == 0)
        }
    }
}
