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
            harnessAccounts.removeAll()
            applyCached()
            persist()
        }
    }
    /// The day the visible range is built around.
    public private(set) var anchor: Date {
        didSet { harnessAccounts.removeAll() }
    }
    /// Every block the providers reported for the visible range, before
    /// the legend's hidden toggles apply. `visibleBlocks` is the grid's.
    public private(set) var blocks: [ActivityBlock] = [] {
        didSet {
            harnessAccounts.removeAll()
            refreshDayBlocks()
        }
    }
    /// Each rendered day's slice of `blocks`, observed on its own. A day
    /// column reads its slice rather than `blocks`, so publishing a new
    /// render range redraws only the columns whose blocks changed.
    @ObservationIgnored private var dayBlocks: [Date: DayBlocks] = [:]
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
    /// Usage accounts hidden in the legend: their reset markers leave the
    /// grid. Keyed on the profile id, so a rename keeps the toggle.
    public private(set) var hiddenUsage: Set<String>
    /// "profile|window" pairs hidden inside a shown account, like
    /// hiddenAccounts under a harness.
    public private(set) var hiddenUsageWindows: Set<String>
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
    /// The closure reads the accounts store, so results are memoised per
    /// harness for the life of a load rather than per call.
    private let knownAccounts: (String) -> [String]
    /// Filled lazily while views read accounts, so it must not be
    /// observed: a write during a render would invalidate that render.
    @ObservationIgnored private var knownHomes: [String: [String]] = [:]
    /// Same rule for the per-block lookups: `accountName` calls the
    /// injected label closure, which on the live app rebuilds the
    /// accounts store from disk — memoised once per home per load.
    @ObservationIgnored private var accountLabels: [String: String] = [:]
    /// `accounts(for:)` per harness, rebuilt when `blocks` or the anchor
    /// (and with it `windowBlocks`) changes.
    @ObservationIgnored private var harnessAccounts: [String: [String]] = [:]

    private func knownHomes(for harness: String) -> [String] {
        if let cached = knownHomes[harness] { return cached }
        let homes = knownAccounts(harness)
        knownHomes[harness] = homes
        return homes
    }
    private let defaults: UserDefaults
    private let now: () -> Date
    public let calendar: Calendar
    private var generation = 0
    /// One day's blocks per provider: the session cache. Each day keeps
    /// what the newest read covering it returned, so a landing paints
    /// every day it has seen this session at once and asks providers only
    /// for the days it has not. A block is listed under every day it
    /// overlaps.
    struct DayEntry {
        struct Slot {
            var blocks: [ActivityBlock]
            /// The read that wrote it: an older read landing late never
            /// replaces what a newer one wrote.
            var read: Int
        }
        var providers: [Int: Slot] = [:]
        /// A finished scan, a restored snapshot or a provider that failed
        /// last time: still painted, read again on the next load.
        var stale = false
        /// When the newest read covering it landed; an entry older than
        /// `dayLifetime` is read again, for sources no scan announces.
        var fetched = Date.distantPast
    }
    private var dayCache: [Date: DayEntry] = [:]
    /// How long a day read stays fresh without a scan marking it stale.
    static let dayLifetime: TimeInterval = 300
    /// Reads still running, by the days they cover, so a load needing a
    /// day a prefetch is already reading waits for it instead of asking
    /// the providers twice.
    private var inFlight: [Date: Task<String?, Never>] = [:]
    private var nextRead = 0
    /// Most recently loaded render ranges, for the persisted snapshot.
    private var recent: [DateInterval] = []
    private let snapshots: (any SnapshotStoring<CalendarSnapshot>)?
    /// Prefetches and snapshot saves still running, by id. Each removes
    /// itself when done, so `backgroundWork()` can wait for the rest.
    private var background: [Int: Task<Void, Never>] = [:]
    private var nextBackgroundID = 0
    /// How many ranges the snapshot keeps.
    static let snapshotRangeCount = 8

    static let hiddenHarnessesKey = "calendar.hiddenHarnesses"
    static let hiddenAccountsKey = "calendar.hiddenAccounts"
    static let collapsedAccountsKey = "calendar.collapsedAccounts"
    static let hiddenUsageKey = "calendar.hiddenUsage"
    static let hiddenUsageWindowsKey = "calendar.hiddenUsageWindows"
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
        hiddenUsage = Set(defaults.stringArray(forKey: Self.hiddenUsageKey) ?? [])
        hiddenUsageWindows = Set(defaults.stringArray(forKey: Self.hiddenUsageWindowsKey) ?? [])
        mode = Self.decodedMode(defaults.string(forKey: Self.modeKey))
        panelCollapsed = defaults.bool(forKey: Self.panelCollapsedKey)
        self.snapshots = snapshots
        // Hydrate the block cache from the last run's snapshot so the
        // first paint does not wait on a provider read.
        // Its days count as stale: they paint, then read again.
        for entry in restored?.ranges ?? [] {
            let range = DateInterval(start: entry.start, end: entry.end)
            store(entry.providers, days: days(in: range), read: 0, stale: true,
                  fetched: .distantPast)
            recent.append(range)
        }
        // First paint off the snapshot: the cached blocks for the range
        // on screen show before the session list or any provider answers.
        applyCached()
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
        days(in: visibleRange)
    }

    /// The day columns the horizontal sliding window renders: the
    /// render range's days, one window of buffer on each side.
    public var renderDays: [DateInterval] {
        days(in: renderRange)
    }

    private func days(in range: DateInterval) -> [DateInterval] {
        var days: [DateInterval] = []
        var day = calendar.startOfDay(for: range.start)
        while day < range.end {
            // A nil or non-advancing day-add must stop the walk, or this
            // loops forever on a calendar that cannot answer.
            guard let next = calendar.date(byAdding: .day, value: 1, to: day), next > day else { break }
            days.append(DateInterval(start: max(day, range.start), end: min(next, range.end)))
            day = next
        }
        return days
    }

    /// The range the grid renders and the cache keys: the visible range
    /// plus one window of buffer columns on each side, so swiping shows
    /// painted content before the next anchor lands. Month mode does not
    /// slide, so it is just the month.
    public var renderRange: DateInterval {
        renderRange(for: anchor, mode: mode)
    }

    public func renderRange(for anchor: Date, mode: Mode) -> DateInterval {
        let visible = range(for: anchor, mode: mode)
        let window: Int
        switch mode {
        case .day: window = 1
        case .week: window = 7
        case .month: return visible
        case .days(let n): window = max(1, n)
        }
        let start = calendar.date(byAdding: .day, value: -window, to: visible.start) ?? visible.start
        let end = calendar.date(byAdding: .day, value: window, to: visible.end) ?? visible.end
        return DateInterval(start: start, end: end)
    }

    /// A horizontal scroll settled on a leading day: the anchor lands on
    /// it — on its week's first day in week mode, where the whole week
    /// is the scroll unit. Month mode never slides.
    public func land(on leadingDay: Date) {
        let snapped: Date
        switch mode {
        case .month: return
        case .week:
            let week = calendar.dateComponents([.yearForWeekOfYear, .weekOfYear],
                                               from: leadingDay)
            snapped = calendar.date(from: week) ?? calendar.startOfDay(for: leadingDay)
        default:
            snapped = calendar.startOfDay(for: leadingDay)
        }
        guard snapped != anchor else { return }
        anchor = snapped
        applyCached()
    }

    /// The leading day a horizontal scroll came to rest on, read from its
    /// offset: the nearest snap target (a day, or a whole week in week
    /// mode) among `renderDays`. The page lands here only once the scroll
    /// is idle, so the render window never re-centres under a moving
    /// swipe. nil in month mode or before the grid has a width.
    public func settledLeadingDay(offset: Double, columnWidth: Double) -> Date? {
        let days = renderDays
        guard !mode.isMonth, columnWidth > 0, !days.isEmpty else { return nil }
        let step = mode == .week ? 7 : 1
        let target = Int((offset / (columnWidth * Double(step))).rounded()) * step
        return days[min(max(0, target), days.count - 1)].start
    }

    /// Where `day` sits in the scroll content: its column's leading edge
    /// among `renderDays`. Landing re-centres the render days, which puts
    /// the resting day in a different column while the scroll view keeps
    /// its pixel offset, so the page scrolls here in the same update.
    /// nil when the day is not a column or the grid has no width.
    public func leadingOffset(of day: Date, columnWidth: Double) -> Double? {
        guard columnWidth > 0,
              let index = renderDays.firstIndex(where: { $0.start == day })
        else { return nil }
        return Double(index) * columnWidth
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

    /// Whatever the session cache holds for the new render range, right
    /// away; the load the page then starts reads only the days it lacks.
    /// With no day of the range cached the old blocks stay up until the
    /// load lands, rather than flashing empty.
    private func applyCached() {
        if let cached = composed(renderRange) { publish(cached) }
    }

    /// Assigns only a real change, so a landing whose days are all cached
    /// redraws nothing that reads `blocks`.
    private func publish(_ new: [ActivityBlock]) {
        if new != blocks { blocks = new }
    }

    /// A finished ledger scan: every cached day is stale but stays on
    /// screen; the page follows with `load(force:)` for the visible one.
    public func noteScanCompleted() {
        for key in dayCache.keys { dayCache[key]?.stale = true }
    }

    /// Paint the range from the session cache, then read every provider
    /// concurrently for the days it lacks: days never read, stale ones,
    /// and ones older than `dayLifetime`. Days a running read (a
    /// prefetch) already covers wait for it instead of being read twice.
    /// `force` reads every day of the range regardless and asks cached
    /// sources to skip their throttle. A provider that fails keeps its
    /// cached blocks for those days, leaves them stale for the next load
    /// and sets `loadError`; a load with every read clean clears it. A
    /// load overtaken by a newer one stores what it read but publishes
    /// nothing.
    public func load(force: Bool = false) async {
        await load(range: renderRange, force: force)
    }

    /// The range-explicit form, so tests can load a range the mode does
    /// not produce. The range is the render range: the days read and the
    /// clip for the published blocks alike.
    func load(range: DateInterval, force: Bool) async {
        generation += 1
        let mine = generation
        // Profile edits land here: fresh homes get picked up next load.
        knownHomes.removeAll()
        accountLabels.removeAll()
        harnessAccounts.removeAll()
        if let cached = composed(range) { publish(cached) }
        let days = days(in: range)
        var failed: String?
        if force {
            failed = await read(days, force: true)
        } else {
            // A running read's error counts like this load's own: the
            // days it covers are still missing or stale if it failed.
            let running = Set(days.compactMap { inFlight[dayKey($0)] })
            let reads = runs(of: days.filter(needsRead)).map { run in
                Task { await self.read(run, force: false) }
            }
            for task in running { if let error = await task.value { failed = error } }
            for task in reads { if let error = await task.value { failed = error } }
        }
        guard mine == generation else { return }
        publish(composed(range) ?? [])
        loadError = failed
        loadedRange = range
        persistSnapshot(of: range)
        prefetchNeighbours()
    }

    private func dayKey(_ day: DateInterval) -> Date { calendar.startOfDay(for: day.start) }

    /// Never read, stale, or past `dayLifetime`, and no read running.
    private func needsRead(_ day: DateInterval) -> Bool {
        let key = dayKey(day)
        guard inFlight[key] == nil else { return false }
        guard let entry = dayCache[key] else { return true }
        return entry.stale || now().timeIntervalSince(entry.fetched) > Self.dayLifetime
    }

    /// Consecutive days as separate runs, so a read never spans fresh
    /// days sitting between two missing ones.
    private func runs(of days: [DateInterval]) -> [[DateInterval]] {
        var runs: [[DateInterval]] = []
        for day in days {
            if let last = runs.last?.last, last.end == day.start {
                runs[runs.count - 1].append(day)
            } else {
                runs.append([day])
            }
        }
        return runs
    }

    /// Reads every provider once for the span from the first to the last
    /// of `days` (one run of consecutive days), widened by the lookout so a block straddling an edge
    /// keeps its real ends, and stores each day's slice. Registered in
    /// `inFlight` for those days until it lands. Returns the last
    /// provider error, nil when every provider answered.
    private func read(_ days: [DateInterval], force: Bool,
                      priority: TaskPriority? = nil) async -> String? {
        guard let first = days.first, let last = days.last else { return nil }
        let span = DateInterval(start: first.start, end: max(first.start, last.end))
        let covered = self.days(in: span)
        nextRead += 1
        let readID = nextRead
        // Detached so the provider reads and the project resolution
        // behind `group` — `.git` walks and the `swe` lookup — run off
        // the main actor; `store` alone hops back.
        let lookout = Self.edgeLookout
        let task = Task.detached(priority: priority) { [providers, projects] () -> String? in
            let widened = DateInterval(start: span.start.addingTimeInterval(-lookout),
                                       end: span.end.addingTimeInterval(lookout))
            var good: [Int: [ActivityBlock]] = [:]
            var failed: String?
            await withTaskGroup(of: (Int, Result<[ActivityBlock], Error>, TimeInterval).self) { group in
                for (i, provider) in providers.enumerated() {
                    group.addTask {
                        let start = Date()
                        do {
                            let batch = try await provider.activityBlocks(in: widened, gap: Self.gap,
                                                                          force: force)
                            return (i, .success(projects.group(batch, gap: Self.gap)
                                .filter { $0.end >= span.start && $0.start < span.end }),
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
                        Logger.calendar.debug("\(i) \(String(describing: type(of: providers[i])), privacy: .public) loaded \(batch.count) blocks in \(seconds, format: .fixed(precision: 1), privacy: .public)s")
                    case .failure(let error):
                        failed = error.localizedDescription
                    }
                }
            }
            // `now` is a closure kept on the actor: the call, not the
            // closure itself, hops back for the fetch timestamp.
            let fetched = await MainActor.run { self.now() }
            await self.store(good, days: covered, read: readID, stale: failed != nil,
                             fetched: fetched)
            return failed
        }
        for day in covered { inFlight[dayKey(day)] = task }
        let failed = await task.value
        for day in covered where inFlight[dayKey(day)] == task {
            inFlight[dayKey(day)] = nil
        }
        return failed
    }

    /// Files each provider's blocks under every day they overlap. A slot
    /// a newer read already wrote keeps its blocks, so a prefetch landing
    /// after a load never replaces fresher ones; a provider missing from
    /// `providers` (its read failed) keeps whatever it had.
    private func store(_ providers: [Int: [ActivityBlock]], days: [DateInterval],
                       read: Int, stale: Bool, fetched: Date) {
        for day in days {
            let key = dayKey(day)
            var entry = dayCache[key] ?? DayEntry()
            let newest = entry.providers.values.map(\.read).max() ?? -1
            for (i, blocks) in providers {
                if let slot = entry.providers[i], slot.read > read { continue }
                entry.providers[i] = .init(
                    blocks: blocks.filter { $0.end >= day.start && $0.start < day.end },
                    read: read)
            }
            if read >= newest {
                entry.stale = stale
                entry.fetched = fetched
            }
            dayCache[key] = entry
        }
    }

    /// The range's blocks per provider from the day cache; nil when no
    /// day of the range has been read.
    private func composedByProvider(_ range: DateInterval) -> [Int: [ActivityBlock]]? {
        var lists: [Int: [ActivityBlock]] = [:]
        var any = false
        for day in days(in: range) {
            guard let entry = dayCache[dayKey(day)] else { continue }
            any = true
            for (i, slot) in entry.providers { lists[i, default: []] += slot.blocks }
        }
        guard any else { return nil }
        return lists.mapValues {
            Self.merged($0).filter { $0.end >= range.start && $0.start < range.end }
        }
    }

    /// The range's blocks from the day cache as one sorted list.
    private func composed(_ range: DateInterval) -> [ActivityBlock]? {
        composedByProvider(range).map { Self.merged($0.values.flatMap { $0 }) }
    }

    /// One list from several reads. A block filed under each day it
    /// touches appears once, and pieces of one long block that two reads
    /// cut at different lookout edges join back into one. A project's
    /// real blocks never overlap (grouping merges them), so an overlap
    /// within one project is the same block read more than once.
    static func merged(_ blocks: [ActivityBlock]) -> [ActivityBlock] {
        var byProject: [String: [ActivityBlock]] = [:]
        for block in blocks {
            let project = block.projectKey.isEmpty ? block.project : block.projectKey
            byProject["\(block.harness)|\(block.accountHome)|\(project)", default: []].append(block)
        }
        var out: [ActivityBlock] = []
        for list in byProject.values {
            let sorted = list.sorted { $0.start != $1.start ? $0.start < $1.start : $0.end > $1.end }
            var current = sorted[0]
            for block in sorted.dropFirst() {
                if block.start <= current.end {
                    current = joined(current, block)
                } else {
                    out.append(current)
                    current = block
                }
            }
            out.append(current)
        }
        return out.sorted { $0.start != $1.start ? $0.start < $1.start : $0.end < $1.end }
    }

    /// Two overlapping reads of one block. Each is exact inside the span
    /// it read, so a piece covering the other is the whole story; one
    /// with the larger count wins a tie. Pieces each reaching past the
    /// other join: the block runs from the earlier start to the later
    /// end, and each session from its earliest start to its latest end.
    /// Both reads counted the responses where they overlap, so a session
    /// keeps the larger count rather than the sum, and the block's count
    /// is its sessions' total; it can under-count by what the shorter
    /// piece alone saw.
    private static func joined(_ a: ActivityBlock, _ b: ActivityBlock) -> ActivityBlock {
        func covers(_ x: ActivityBlock, _ y: ActivityBlock) -> Bool {
            x.start <= y.start && x.end >= y.end
        }
        if covers(a, b) && covers(b, a) { return a.responses >= b.responses ? a : b }
        if covers(a, b) { return a }
        if covers(b, a) { return b }
        var block = a
        block.start = min(a.start, b.start)
        block.end = max(a.end, b.end)
        var sessions = a.sessions
        for session in b.sessions {
            if let i = sessions.firstIndex(where: { $0.sessionId == session.sessionId }) {
                sessions[i].start = min(sessions[i].start, session.start)
                sessions[i].end = max(sessions[i].end, session.end)
                sessions[i].responses = max(sessions[i].responses, session.responses)
                sessions[i].cwd = sessions[i].cwd ?? session.cwd
            } else {
                sessions.append(session)
            }
        }
        block.sessions = sessions.sorted { $0.start < $1.start }
        block.responses = max(a.responses, b.responses,
                              block.sessions.reduce(0) { $0 + $1.responses })
        return block
    }

    /// The days one mode step either side would add, read quietly so a
    /// swipe or step lands on cached days. A swipe moves at most one
    /// window, so these cover every day the next landing can expose.
    /// Results land in the cache only; landing there publishes them.
    private func prefetchNeighbours() {
        for neighbour in [renderRange(for: shiftedAnchor(-1, mode: mode), mode: mode),
                          renderRange(for: shiftedAnchor(1, mode: mode), mode: mode)] {
            for run in runs(of: days(in: neighbour).filter(needsRead)) {
                inBackground(priority: .utility) { [weak self] in
                    _ = await self?.read(run, force: false, priority: .utility)
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
            composedByProvider(key).map { CalendarSnapshot.Entry(start: key.start, end: key.end,
                                                                 providers: $0) }
        }
        inBackground { await snapshots.save(CalendarSnapshot(ranges: entries)) }
    }

    /// Runs `work` in an unstructured task the model keeps track of.
    private func inBackground(priority: TaskPriority? = nil,
                              _ work: @escaping @MainActor () async -> Void) {
        nextBackgroundID += 1
        let id = nextBackgroundID
        background[id] = Task(priority: priority) { [weak self] in
            await work()
            self?.background[id] = nil
        }
    }

    /// Returns once every prefetch and snapshot save started so far has
    /// finished, so a test can wait for them instead of sleeping.
    func backgroundWork() async {
        while let task = background.values.first {
            await task.value
        }
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
        blocks.filter(isShown)
    }

    /// Whether the legend shows the block's harness and account.
    public func isShown(_ block: ActivityBlock) -> Bool {
        !hiddenHarnesses.contains(block.harness)
            && !hiddenAccounts.contains(Self.key(harness: block.harness, home: block.accountHome))
    }

    /// The day's slice of `blocks` (hidden toggles not applied), for its
    /// column. Made on first read and kept while the day is rendered.
    public func blocks(on day: DateInterval) -> DayBlocks {
        let key = dayKey(day)
        if let slice = dayBlocks[key] { return slice }
        let slice = DayBlocks(blocks: blocks.filter { Self.overlaps($0, day) })
        dayBlocks[key] = slice
        return slice
    }

    private static func overlaps(_ block: ActivityBlock, _ day: DateInterval) -> Bool {
        block.start < day.end && block.end >= day.start
    }

    /// Re-slices `blocks` for every rendered day, writing only slices
    /// that changed; days that left the render range are dropped.
    private func refreshDayBlocks() {
        let rendered = Set(renderDays.map(dayKey))
        for (key, slice) in dayBlocks {
            guard rendered.contains(key),
                  let day = calendar.dateInterval(of: .day, for: key)
            else {
                dayBlocks[key] = nil
                continue
            }
            let fresh = blocks.filter { Self.overlaps($0, day) }
            if fresh != slice.blocks { slice.blocks = fresh }
        }
    }

    static func key(harness: String, home: String) -> String { "\(harness)|\(home)" }

    /// Blocks overlapping the visible window — the render range's buffer
    /// columns stay on the grid but out of the legend and empty state.
    public var windowBlocks: [ActivityBlock] {
        let range = visibleRange
        return blocks.filter {
            // A zero-length block (one response) exactly at the window
            // start still counts as inside.
            $0.start < range.end
                && ($0.end > range.start || ($0.start == $0.end && $0.start >= range.start))
        }
    }

    /// Same, with the legend's hidden toggles applied.
    public var visibleWindowBlocks: [ActivityBlock] {
        windowBlocks.filter {
            !hiddenHarnesses.contains($0.harness)
                && !hiddenAccounts.contains(Self.key(harness: $0.harness, home: $0.accountHome))
        }
    }

    /// Harnesses with any block in the window, hidden or not, sorted.
    public var harnesses: [String] {
        Array(Set(windowBlocks.map(\.harness))).sorted()
    }

    /// Every account the harness has: the union of homes with a block in
    /// the window and Quiver's known accounts for it, so a quiet account
    /// still lists (like Notion's per-calendar rows) at "0m".
    public func accounts(for harness: String) -> [String] {
        if let cached = harnessAccounts[harness] { return cached }
        var homes = Set(windowBlocks.filter { $0.harness == harness }.map(\.accountHome))
        homes.formUnion(knownHomes(for: harness))
        let sorted = homes.sorted()
        harnessAccounts[harness] = sorted
        return sorted
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
        if let cached = accountLabels[accountHome] { return cached }
        let name = accountLabel(accountHome)
        accountLabels[accountHome] = name
        return name
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

    /// Whether the usage legend keeps the account's reset markers off
    /// the grid.
    public func isUsageHidden(_ account: String) -> Bool { hiddenUsage.contains(account) }

    public func toggleUsage(_ account: String) {
        if hiddenUsage.remove(account) == nil { hiddenUsage.insert(account) }
        persist()
    }

    /// Whether one window's reset marker stays off while its account
    /// shows.
    public func isUsageHidden(_ account: String, window: String) -> Bool {
        hiddenUsageWindows.contains("\(account)|\(window)")
    }

    public func toggleUsage(_ account: String, window: String) {
        let key = "\(account)|\(window)"
        if hiddenUsageWindows.remove(key) == nil { hiddenUsageWindows.insert(key) }
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
        defaults.set(Array(hiddenUsage), forKey: Self.hiddenUsageKey)
        defaults.set(Array(hiddenUsageWindows), forKey: Self.hiddenUsageWindowsKey)
        defaults.set(Self.encodedMode(mode), forKey: Self.modeKey)
        defaults.set(panelCollapsed, forKey: Self.panelCollapsedKey)
    }
}

/// One day's blocks for its column: an object so each column observes
/// only its own day.
@MainActor @Observable public final class DayBlocks {
    public internal(set) var blocks: [ActivityBlock]
    init(blocks: [ActivityBlock]) { self.blocks = blocks }
}
