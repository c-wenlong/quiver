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

        /// Month mode shows no range highlight on the mini month.
        public var isMonth: Bool {
            if case .month = self { return true }
            return false
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
    /// sideways, jump to today, or toggle the side panel. Pure so it can
    /// be tested without a view; arrows are handled by the view since
    /// they are not characters.
    public enum KeyCommand: Sendable, Equatable {
        case mode(Mode)
        case previous, next, today
        case togglePanel

        /// Arrow keys step the anchor, like the header chevrons.
        public enum Arrow: Sendable {
            case left, right
        }

        public init(arrow: Arrow) {
            self = arrow == .left ? .previous : .next
        }

        /// `d` Day, `w` Week, `m` Month, `t` Today, `s` side panel,
        /// `1`...`9` N days.
        public init?(character: Character) {
            switch character.lowercased().first {
            case "d": self = .mode(.day)
            case "w": self = .mode(.week)
            case "m": self = .mode(.month)
            case "t": self = .today
            case "s": self = .togglePanel
            case let digit? where ("1"..."9").contains(digit):
                self = .mode(.days(Int(String(digit)) ?? 1))
            default: return nil
            }
        }
    }

    /// A project's responses read as one block until this much silence
    /// passes between two of them.
    public nonisolated static let gap: TimeInterval = 30 * 60

    /// How far past the visible range providers are asked for: a project
    /// block straddling the edge joins sessions on both sides of it, so
    /// the query widens a day each way and the grouped result clips back
    /// to the range. Matches the ledger's own lookout.
    static let edgeLookout: TimeInterval = 24 * 3600

    public var mode: Mode = .week {
        didSet {
            if mode != mode.normalized { mode = mode.normalized }
            applyCached()
            persist()
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
    /// The range the newest finished load covered; the page scrolls to
    /// its first block once this catches up with `visibleRange`.
    public private(set) var loadedRange: DateInterval?
    /// Harnesses hidden in the legend: every account of one goes with it.
    public private(set) var hiddenHarnesses: Set<String>
    /// "harness|accountHome" pairs hidden on their own.
    public private(set) var hiddenAccounts: Set<String>
    /// Harnesses whose account rows the legend user collapsed; accounts
    /// expand by default once a harness has more than one.
    public private(set) var collapsedAccounts: Set<String>
    /// The mini-month-and-legend panel pinned away on the page's right.
    /// Collapsing still lets the pointer float it over the grid.
    public var panelCollapsed: Bool {
        didSet { persist() }
    }

    private let providers: [any ActivityProviding]
    private let projects: ProjectResolver
    private let accountLabel: (String) -> String
    /// Quiver's known accounts per harness: profile homes so a quiet
    /// account still lists in the legend like Notion lists calendars.
    private let knownAccounts: (String) -> [String]
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
    static let collapsedAccountsKey = "calendar.collapsedAccounts"
    static let modeKey = "calendar.mode"
    static let panelCollapsedKey = "calendar.panelCollapsed"

    public init(providers: [any ActivityProviding],
                projects: ProjectResolver = ProjectResolver(),
                accountLabel: @escaping (String) -> String = { $0 },
                knownAccounts: @escaping (String) -> [String] = { _ in [] },
                defaults: UserDefaults = .standard,
                now: @escaping () -> Date = Date.init,
                calendar: Calendar = .current,
                snapshots: (any SnapshotStoring<CalendarSnapshot>)? = nil,
                restored: CalendarSnapshot? = nil) {
        self.providers = providers
        self.projects = projects
        self.accountLabel = accountLabel
        self.knownAccounts = knownAccounts
        self.defaults = defaults
        self.now = now
        self.calendar = calendar
        anchor = now()
        hiddenHarnesses = Set(defaults.stringArray(forKey: Self.hiddenHarnessesKey) ?? [])
        hiddenAccounts = Set(defaults.stringArray(forKey: Self.hiddenAccountsKey) ?? [])
        collapsedAccounts = Set(defaults.stringArray(forKey: Self.collapsedAccountsKey) ?? [])
        mode = Self.decodedMode(defaults.string(forKey: Self.modeKey))
        panelCollapsed = defaults.bool(forKey: Self.panelCollapsedKey)
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
        case .togglePanel: panelCollapsed.toggle()
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
        let widened = DateInterval(start: range.start.addingTimeInterval(-Self.edgeLookout),
                                   end: range.end.addingTimeInterval(Self.edgeLookout))
        await withTaskGroup(of: (Int, Result<[ActivityBlock], Error>, TimeInterval).self) { group in
            for (i, provider) in providers.enumerated() {
                group.addTask {
                    let start = Date()
                    do {
                        let batch = try await provider.activityBlocks(in: widened, gap: Self.gap,
                                                                      force: force)
                        return (i, .success(self.projects.group(batch, gap: Self.gap)
                            .filter { $0.end >= range.start && $0.start < range.end }),
                            Date().timeIntervalSince(start))
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
        loadedRange = range
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
            let widened = DateInterval(start: neighbour.start.addingTimeInterval(-Self.edgeLookout),
                                       end: neighbour.end.addingTimeInterval(Self.edgeLookout))
            Task(priority: .utility) { [providers, projects, weak self] in
                var fetched: [Int: [ActivityBlock]] = [:]
                await withTaskGroup(of: (Int, [ActivityBlock]?).self) { group in
                    for (i, provider) in providers.enumerated() {
                        group.addTask {
                            (i, (try? await provider.activityBlocks(in: widened,
                                                                    gap: Self.gap, force: false))
                                .map { projects.group($0, gap: Self.gap)
                                    .filter { $0.end >= neighbour.start && $0.start < neighbour.end } })
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

    /// The empty-state noun phrase: "today", "this week", "this month"
    /// or "in these N days".
    public var emptyRangeNoun: String {
        switch mode {
        case .day: "today"
        case .week: "this week"
        case .month: "this month"
        case .days(let n): "in these \(n) days"
        }
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

    /// Every account the harness has: the union of homes with a block in
    /// the range and Quiver's known accounts for it, so a quiet account
    /// still lists (like Notion's per-calendar rows) at "0m".
    public func accounts(for harness: String) -> [String] {
        var homes = Set(blocks.filter { $0.harness == harness }.map(\.accountHome))
        homes.formUnion(knownAccounts(harness))
        return homes.sorted()
    }

    /// The account rows show whenever the harness has more than one
    /// account, expanded until the user collapses them.
    public func accountsExpanded(_ harness: String) -> Bool {
        accounts(for: harness).count > 1 && !collapsedAccounts.contains(harness)
    }

    public func toggleAccountsExpanded(_ harness: String) {
        if collapsedAccounts.remove(harness) == nil { collapsedAccounts.insert(harness) }
        persist()
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
        // Wall-clock union: parallel sessions and overlapping projects
        // count once, so clipped spans merge before they sum.
        var spans: [String: [DateInterval]] = [:]
        for block in blocks {
            guard let name = key(block),
                  block.end > range.start, block.start < range.end
            else { continue }
            let span = DateInterval(start: max(block.start, range.start),
                                    end: min(block.end, range.end))
            spans[name, default: []].append(span)
        }
        var totals: [String: TimeInterval] = [:]
        for (name, list) in spans {
            var total: TimeInterval = 0
            var current: DateInterval?
            for span in list.sorted(by: { $0.start < $1.start }) {
                if let held = current, span.start <= held.end {
                    current = DateInterval(start: held.start, end: max(held.end, span.end))
                } else {
                    if let held = current { total += held.duration }
                    current = span
                }
            }
            if let held = current { total += held.duration }
            totals[name] = total
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

    /// The mode's UserDefaults spelling: "day", "week", "month" or
    /// "days:N". Unknown values restore as the week.
    static func encodedMode(_ mode: Mode) -> String {
        switch mode {
        case .day: "day"
        case .week: "week"
        case .month: "month"
        case .days(let n): "days:\(n)"
        }
    }

    static func decodedMode(_ text: String?) -> Mode {
        switch text {
        case "day": .day
        case "week": .week
        case "month": .month
        case let days? where days.hasPrefix("days:"):
            if let n = Int(days.dropFirst(5)), n >= 1 { .days(n).normalized } else { .week }
        default: .week
        }
    }

    private func persist() {
        defaults.set(Array(hiddenHarnesses), forKey: Self.hiddenHarnessesKey)
        defaults.set(Array(hiddenAccounts), forKey: Self.hiddenAccountsKey)
        defaults.set(Array(collapsedAccounts), forKey: Self.collapsedAccountsKey)
        defaults.set(Self.encodedMode(mode), forKey: Self.modeKey)
        defaults.set(panelCollapsed, forKey: Self.panelCollapsedKey)
    }
}
