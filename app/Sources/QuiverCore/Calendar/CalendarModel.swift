import Foundation
import os

/// The Calendar page's model: which days are on show, the activity blocks
/// on them, and which harnesses or accounts the legend has turned off.
@MainActor @Observable
public final class CalendarModel {
    /// How much the grid shows. `.week` starts on the user's first
    /// weekday; `.days(n)` starts on the anchor day itself, so `.days(7)`
    /// is a rolling seven days, not the week. `.days(1)` is `.day`.
    public enum Mode: Sendable, Hashable {
        case day, week, month
        case days(Int)

        public var title: String {
            switch self {
            case .day: "Day"
            case .week: "Week"
            case .month: "Month"
            case .days(let n): "\(n) days"
            }
        }

        /// `.days(1)` reads as `.day`; the menu never shows an N-day
        /// option under 2 or over 9.
        public var normalized: Mode {
            guard case .days(let n) = self else { return self }
            return n <= 1 ? .day : .days(min(n, 9))
        }

        /// Its plain-key shortcut, shown in the mode menu's labels.
        public var keyHint: String {
            switch self {
            case .day: "D"
            case .week: "W"
            case .month: "M"
            case .days(let n): "\(n)"
            }
        }

        /// The menu's rows: the three named modes plus the current N-day
        /// selection when one is active.
        public static func menu(for current: Mode) -> [Mode] {
            var modes: [Mode] = [.day, .week, .month]
            if case .days = current { modes.append(current) }
            return modes
        }
    }

    /// What a plain key on the Calendar page does: pick a mode, step
    /// sideways, or jump to today. Pure so it can be tested without a
    /// view; arrows are handled by the view since they are not
    /// characters.
    public enum KeyCommand: Sendable, Equatable {
        case mode(Mode)
        case previous, next, today

        /// Arrow keys step the anchor, like the header chevrons.
        public enum Arrow: Sendable {
            case left, right
        }

        public init(arrow: Arrow) {
            self = arrow == .left ? .previous : .next
        }

        /// `d` Day, `w` Week, `m` Month, `t` Today, `1`...`9` N days.
        public init?(character: Character) {
            switch character.lowercased().first {
            case "d": self = .mode(.day)
            case "w": self = .mode(.week)
            case "m": self = .mode(.month)
            case "t": self = .today
            case let digit? where digit.isNumber:
                self = .mode(.days(Int(String(digit)) ?? 1))
            default: return nil
            }
        }
    }

    /// A session's responses read as one block until this much silence
    /// passes between two of them.
    public nonisolated static let gap: TimeInterval = 15 * 60

    public var mode: Mode = .week {
        didSet {
            if mode != mode.normalized { mode = mode.normalized }
            applyCached()
        }
    }
    /// The day the visible range is built around.
    public private(set) var anchor: Date
    /// Every block the providers reported for the visible range, before
    /// the legend's hidden toggles apply. `visibleBlocks` is the grid's.
    public private(set) var blocks: [ActivityBlock] = []
    /// The last provider read that failed; nil once every provider read
    /// cleanly. Failed providers keep their last good blocks meanwhile.
    public private(set) var loadError: String?
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
    /// Each provider's last successful read, so one bad fetch does not
    /// blank its blocks for the range.
    private var lastGood: [Int: [ActivityBlock]] = [:]
    /// Per-provider blocks by range: the stale-while-revalidate cache.
    /// A range's blocks paint the moment the user lands on it, then the
    /// load replaces them.
    private var cache: [DateInterval: [Int: [ActivityBlock]]] = [:]
    /// Cache ranges a completed ledger scan invalidated: kept on screen
    /// until their refetch lands.
    private var stale: Set<DateInterval> = []
    /// Most recently used ranges, for the persisted snapshot's cap.
    private var recent: [DateInterval] = []
    private let snapshots: (any SnapshotStoring<CalendarSnapshot>)?
    /// How many ranges the snapshot keeps.
    static let snapshotRangeCount = 8

    static let hiddenHarnessesKey = "calendar.hiddenHarnesses"
    static let hiddenAccountsKey = "calendar.hiddenAccounts"

    public init(providers: [any ActivityProviding],
                accountLabel: @escaping (String) -> String = { $0 },
                defaults: UserDefaults = .standard,
                now: @escaping () -> Date = Date.init,
                calendar: Calendar = .current,
                snapshots: (any SnapshotStoring<CalendarSnapshot>)? = nil,
                restored: CalendarSnapshot? = nil) {
        self.providers = providers
        self.accountLabel = accountLabel
        self.defaults = defaults
        self.now = now
        self.calendar = calendar
        anchor = now()
        hiddenHarnesses = Set(defaults.stringArray(forKey: Self.hiddenHarnessesKey) ?? [])
        hiddenAccounts = Set(defaults.stringArray(forKey: Self.hiddenAccountsKey) ?? [])
        self.snapshots = snapshots
        // Hydrate the block cache from the last run's snapshot so the
        // first paint does not wait on a provider read.
        for entry in restored?.ranges ?? [] {
            let range = DateInterval(start: entry.start, end: entry.end)
            cache[range] = entry.providers
            recent.append(range)
        }
        // First paint off the snapshot: the cached blocks for the range
        // on screen show before the session list or any provider answers.
        if let cached = cache[visibleRange] {
            blocks = Self.flatten(cached)
        }
    }

    /// The days on show. `.week` starts on the user's first weekday, so a
    /// Monday-first calendar never shows a Sunday-first grid.
    public var visibleRange: DateInterval { range(for: anchor, mode: mode) }

    /// One mode step of the calendar around an anchor: how `visibleRange`
    /// and the ranges next()/previous() and the prefetch land on are
    /// computed. Calendar arithmetic throughout, so a 25-hour DST day or
    /// a month never drifts off a day boundary.
    public func range(for anchor: Date, mode: Mode) -> DateInterval {
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
        case .month:
            // Always six week rows: the first weekday on or before the
            // 1st, so adjacent-month days fill the grid like Notion's.
            if let weeks = MonthLayout.weekRange(for: anchor, calendar: calendar) {
                return weeks
            }
            // A pathological calendar falls back to the anchor's day.
            let start = calendar.startOfDay(for: anchor)
            return DateInterval(start: start,
                                end: calendar.date(byAdding: .day, value: 1, to: start) ?? start)
        case .days(let n):
            let start = calendar.startOfDay(for: anchor)
            return DateInterval(start: start,
                                end: calendar.date(byAdding: .day, value: max(1, n), to: start) ?? start)
        }
    }

    /// The anchor shifted by this many mode steps (a day, a week, a
    /// month, or an N-day span).
    public func shiftedAnchor(_ count: Int, mode: Mode) -> Date {
        switch mode {
        case .day:
            return calendar.date(byAdding: .day, value: count, to: anchor) ?? anchor
        case .week:
            return calendar.date(byAdding: .weekOfYear, value: count, to: anchor) ?? anchor
        case .month:
            return calendar.date(byAdding: .month, value: count, to: anchor) ?? anchor
        case .days(let n):
            return calendar.date(byAdding: .day, value: count * max(1, n), to: anchor) ?? anchor
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

    /// One key command as the page maps it: a mode pick, a step, or
    /// Today.
    public func apply(_ command: KeyCommand) {
        switch command {
        case .mode(let mode): self.mode = mode
        case .previous: previous()
        case .next: next()
        case .today: today()
        }
    }

    public func today() { anchor = now(); applyCached() }
    public func next() { shift(1) }
    public func previous() { shift(-1) }
    public func go(to date: Date) { anchor = date; applyCached() }
    private func shift(_ count: Int) {
        anchor = shiftedAnchor(count, mode: mode)
        applyCached()
    }

    /// Whatever the cache holds for the new visible range, right away;
    /// the load the page then starts replaces it.
    private func applyCached() {
        if let cached = cache[visibleRange] {
            blocks = Self.flatten(cached)
        }
    }

    /// A finished ledger scan: every cached range is stale but stays on
    /// screen; the page follows with `load(force:)` for the visible one.
    public func noteScanCompleted() {
        stale.formUnion(cache.keys)
    }

    /// Fetch every provider's blocks for the visible range concurrently.
    /// A cached range paints synchronously before the fetch starts. A
    /// provider that fails keeps its cached or last good blocks (the ones
    /// overlapping this range) and sets `loadError`; a fully clean pass
    /// clears it. A load that lands while a newer one is running is
    /// dropped. `force` asks cached sources to skip their throttle.
    public func load(force: Bool = false) async {
        await load(range: visibleRange, force: force)
    }

    /// The range-explicit form, so tests (and a future Month mode) can
    /// load a range the mode does not produce.
    func load(range: DateInterval, force: Bool) async {
        generation += 1
        let mine = generation
        let range = range
        if let cached = cache[range] {
            blocks = Self.flatten(cached)
        }
        var all: [ActivityBlock] = []
        var failed: String?
        // Saved only once this load is known to be the latest: an overtaken
        // load must not overwrite the newer one's last good blocks.
        var good: [Int: [ActivityBlock]] = [:]
        await withTaskGroup(of: (Int, Result<[ActivityBlock], Error>, TimeInterval).self) { group in
            for (i, provider) in providers.enumerated() {
                group.addTask {
                    let start = Date()
                    do {
                        let batch = try await provider.activityBlocks(in: range, gap: Self.gap,
                                                                      force: force)
                        return (i, .success(batch), Date().timeIntervalSince(start))
                    } catch {
                        return (i, .failure(error), 0)
                    }
                }
            }
            for await (i, result, seconds) in group {
                switch result {
                case .success(let batch):
                    good[i] = batch
                    all += batch
                    Logger.calendar.debug("\(i) \(String(describing: type(of: self.providers[i])), privacy: .public) loaded \(batch.count) blocks in \(seconds, format: .fixed(precision: 1), privacy: .public)s")
                case .failure(let error):
                    all += (cache[range]?[i] ?? (lastGood[i] ?? []).filter {
                        $0.end >= range.start && $0.start < range.end
                    })
                    failed = error.localizedDescription
                }
            }
        }
        guard mine == generation else { return }
        lastGood.merge(good) { _, new in new }
        cache[range] = cache[range]?.merging(good) { _, new in new } ?? good
        stale.remove(range)
        blocks = all.sorted { $0.start != $1.start ? $0.start < $1.start : $0.end < $1.end }
        loadError = failed
        persistSnapshot(of: range)
        prefetchNeighbours()
    }

    /// A range's per-provider blocks as one sorted list.
    private static func flatten(_ providers: [Int: [ActivityBlock]]) -> [ActivityBlock] {
        providers.values.flatMap { $0 }
            .sorted { $0.start != $1.start ? $0.start < $1.start : $0.end < $1.end }
    }

    /// The ranges one mode step either side of the anchor, fetched
    /// quietly so stepping sideways paints from cache. Results land in
    /// the cache only; navigating there publishes them. A result that
    /// arrives after a load already wrote fresher blocks for its range
    /// is dropped rather than overwritten with an older read.
    private func prefetchNeighbours() {
        for neighbour in [range(for: shiftedAnchor(-1, mode: mode), mode: mode),
                          range(for: shiftedAnchor(1, mode: mode), mode: mode)] {
            guard cache[neighbour] == nil || stale.contains(neighbour) else { continue }
            Task(priority: .utility) { [providers, weak self] in
                var fetched: [Int: [ActivityBlock]] = [:]
                await withTaskGroup(of: (Int, [ActivityBlock]?).self) { group in
                    for (i, provider) in providers.enumerated() {
                        group.addTask {
                            (i, try? await provider.activityBlocks(in: neighbour,
                                                                   gap: Self.gap, force: false))
                        }
                    }
                    for await (i, batch) in group {
                        if let batch { fetched[i] = batch }
                    }
                }
                guard let self else { return }
                if cache[neighbour] == nil || stale.contains(neighbour) {
                    // Merge into what is cached: a provider that failed
                    // this prefetch keeps its stale blocks, like load().
                    cache[neighbour] = cache[neighbour]?.merging(fetched) { _, new in new }
                        ?? fetched
                    stale.remove(neighbour)
                }
            }
        }
    }

    /// The recent ranges' blocks, capped and written; the next launch
    /// paints from them.
    private func persistSnapshot(of range: DateInterval) {
        guard let snapshots else { return }
        recent.removeAll { $0 == range }
        recent.insert(range, at: 0)
        if recent.count > Self.snapshotRangeCount {
            recent = Array(recent.prefix(Self.snapshotRangeCount))
        }
        let entries = recent.compactMap { key -> CalendarSnapshot.Entry? in
            cache[key].map { CalendarSnapshot.Entry(start: key.start, end: key.end,
                                                    providers: $0) }
        }
        Task { await snapshots.save(CalendarSnapshot(ranges: entries)) }
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

    /// Seconds in the range per harness, each block counted only for its
    /// part inside the range. Legend totals deliberately read all blocks,
    /// not `visibleBlocks`: a hidden row keeps its time, shown dimmed.
    public var totals: [String: TimeInterval] {
        totalTime { $0.harness }
    }

    /// Same split by account home, for one harness's expanded rows.
    public func accountTotals(for harness: String) -> [String: TimeInterval] {
        totalTime { $0.harness == harness ? $0.accountHome : nil }
    }

    /// Legend totals clip to this range: in month mode the anchor's own
    /// month, so adjacent-month grid days do not count.
    private var totalsRange: DateInterval {
        guard case .month = mode,
              let first = calendar.date(from: calendar.dateComponents([.year, .month], from: anchor)),
              let next = calendar.date(byAdding: .month, value: 1, to: first)
        else { return visibleRange }
        return DateInterval(start: first, end: next)
    }

    private func totalTime(key: (ActivityBlock) -> String?) -> [String: TimeInterval] {
        let range = totalsRange
        var totals: [String: TimeInterval] = [:]
        for block in blocks {
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
