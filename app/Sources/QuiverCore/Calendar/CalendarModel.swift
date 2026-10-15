import Foundation

/// The Calendar page's model: which days are on show, the activity blocks
/// on them, and which harnesses or accounts the legend has turned off.
@MainActor @Observable
public final class CalendarModel {
    public enum Mode: String, CaseIterable, Sendable {
        case day, week
        public var title: String { self == .day ? "Day" : "Week" }
    }

    /// A session's responses read as one block until this much silence
    /// passes between two of them.
    public static let gap: TimeInterval = 15 * 60

    public var mode: Mode = .week
    /// The day the visible range is built around.
    public private(set) var anchor: Date
    /// Every block the providers reported for the visible range, before
    /// the legend's hidden toggles apply. `visibleBlocks` is the grid's.
    public private(set) var blocks: [ActivityBlock] = []
    /// Harnesses hidden in the legend: every account of one goes with it.
    public private(set) var hiddenHarnesses: Set<String>
    /// "harness|accountHome" pairs hidden on their own.
    public private(set) var hiddenAccounts: Set<String>

    private let providers: [any ActivityProviding]
    private let accountLabel: (String) -> String
    private let defaults: UserDefaults
    private let now: () -> Date
    public let calendar: Calendar
    private var generation = 0

    static let hiddenHarnessesKey = "calendar.hiddenHarnesses"
    static let hiddenAccountsKey = "calendar.hiddenAccounts"

    public init(providers: [any ActivityProviding],
                accountLabel: @escaping (String) -> String = { $0 },
                defaults: UserDefaults = .standard,
                now: @escaping () -> Date = Date.init,
                calendar: Calendar = .current) {
        self.providers = providers
        self.accountLabel = accountLabel
        self.defaults = defaults
        self.now = now
        self.calendar = calendar
        anchor = now()
        hiddenHarnesses = Set(defaults.stringArray(forKey: Self.hiddenHarnessesKey) ?? [])
        hiddenAccounts = Set(defaults.stringArray(forKey: Self.hiddenAccountsKey) ?? [])
    }

    /// The days on show. `.week` starts on the user's first weekday, so a
    /// Monday-first calendar never shows a Sunday-first grid.
    public var visibleRange: DateInterval {
        switch mode {
        case .day:
            let start = calendar.startOfDay(for: anchor)
            return DateInterval(start: start,
                                end: calendar.date(byAdding: .day, value: 1, to: start) ?? start)
        case .week:
            let week = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: anchor)
            let start = calendar.date(from: week) ?? calendar.startOfDay(for: anchor)
            return DateInterval(start: start,
                                end: calendar.date(byAdding: .day, value: 7, to: start) ?? start)
        }
    }

    /// One interval per day column, clipped to the range.
    public var days: [DateInterval] {
        let range = visibleRange
        var days: [DateInterval] = []
        var day = calendar.startOfDay(for: range.start)
        while day < range.end {
            let next = calendar.date(byAdding: .day, value: 1, to: day) ?? day
            days.append(DateInterval(start: max(day, range.start), end: min(next, range.end)))
            day = next
        }
        return days
    }

    public func today() { anchor = now() }
    public func next() { shift(1) }
    public func previous() { shift(-1) }
    public func go(to date: Date) { anchor = date }
    private func shift(_ count: Int) {
        let unit: Calendar.Component = mode == .day ? .day : .weekOfYear
        anchor = calendar.date(byAdding: unit, value: count, to: anchor) ?? anchor
    }

    /// Fetch every provider's blocks for the visible range concurrently. A
    /// load that lands while a newer one is running is dropped.
    public func load() async {
        generation += 1
        let mine = generation
        let range = visibleRange
        let all = await withTaskGroup(of: [ActivityBlock].self) { group -> [ActivityBlock] in
            for provider in providers {
                group.addTask { await provider.activityBlocks(in: range, gap: Self.gap) }
            }
            var blocks: [ActivityBlock] = []
            for await batch in group { blocks += batch }
            return blocks
        }
        guard mine == generation else { return }
        blocks = all.sorted { $0.start != $1.start ? $0.start < $1.start : $0.end < $1.end }
    }

    /// The grid's input: blocks whose harness and account are both shown.
    public var visibleBlocks: [ActivityBlock] {
        blocks.filter {
            !hiddenHarnesses.contains($0.harness)
                && !hiddenAccounts.contains(Self.key(harness: $0.harness, home: $0.accountHome))
        }
    }

    static func key(harness: String, home: String) -> String { "\(harness)|\(home)" }

    /// Harnesses with any block in the range, hidden or not, sorted.
    public var harnesses: [String] {
        Array(Set(blocks.map(\.harness))).sorted()
    }

    /// An account id only appears when it has a block in the range.
    public func accounts(for harness: String) -> [String] {
        Array(Set(blocks.filter { $0.harness == harness }.map(\.accountHome))).sorted()
    }

    /// The legend's label for an account home: the profile's name when
    /// the accounts store knows one, else the home path itself.
    public func accountName(for accountHome: String) -> String {
        accountLabel(accountHome)
    }

    /// Seconds of visible blocks in the range per harness, each block
    /// counted only for its part inside the range.
    public var totals: [String: TimeInterval] {
        totalTime { $0.harness }
    }

    /// Same split by account home, for one harness's expanded rows.
    public func accountTotals(for harness: String) -> [String: TimeInterval] {
        totalTime { $0.harness == harness ? $0.accountHome : nil }
    }

    private func totalTime(key: (ActivityBlock) -> String?) -> [String: TimeInterval] {
        let range = visibleRange
        var totals: [String: TimeInterval] = [:]
        for block in visibleBlocks {
            guard let name = key(block) else { continue }
            let clipped = min(block.end, range.end).timeIntervalSince(max(block.start, range.start))
            totals[name, default: 0] += max(0, clipped)
        }
        return totals
    }

    public func isHidden(_ harness: String) -> Bool { hiddenHarnesses.contains(harness) }
    public func isHidden(harness: String, accountHome: String) -> Bool {
        hiddenHarnesses.contains(harness)
            || hiddenAccounts.contains(Self.key(harness: harness, home: accountHome))
    }

    public func toggle(_ harness: String) {
        if hiddenHarnesses.remove(harness) == nil { hiddenHarnesses.insert(harness) }
        persist()
    }

    public func toggle(harness: String, accountHome: String) {
        let key = Self.key(harness: harness, home: accountHome)
        if hiddenAccounts.remove(key) == nil { hiddenAccounts.insert(key) }
        persist()
    }

    private func persist() {
        defaults.set(Array(hiddenHarnesses), forKey: Self.hiddenHarnessesKey)
        defaults.set(Array(hiddenAccounts), forKey: Self.hiddenAccountsKey)
    }
}
