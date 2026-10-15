import Foundation
import Testing
@testable import QuiverCore

private struct FakeActivity: ActivityProviding {
    var blocks: [ActivityBlock]
    /// Like the ledger, only blocks overlapping the range come back.
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) throws -> [ActivityBlock] {
        blocks.filter { $0.end >= range.start && $0.start < range.end }
    }
}

private final class Flag: @unchecked Sendable { var value = false }

/// A provider the test can flip between a reading and a failure.
private struct TogglingActivity: ActivityProviding {
    struct Failure: LocalizedError {
        var errorDescription: String? { "ledger read failed" }
    }
    var blocks: [ActivityBlock]
    var fails: Flag
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) throws -> [ActivityBlock] {
        if fails.value { throw Failure() }
        return blocks.filter { $0.end >= range.start && $0.start < range.end }
    }
}

@MainActor
@Suite struct CalendarModelTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }

    private func model(blocks: [ActivityBlock] = [],
                       firstWeekday: Int = 2,
                       now: Date = utc("2026-10-14T12:00:00Z"),
                       knownAccounts: [String: [String]] = [:],
                       defaults: UserDefaults? = nil) -> CalendarModel {
        var cal = calendar
        cal.firstWeekday = firstWeekday
        return CalendarModel(providers: [FakeActivity(blocks: blocks)],
                             knownAccounts: { knownAccounts[$0] ?? [] },
                             defaults: defaults ?? freshDefaults(),
                             now: { now }, calendar: cal)
    }

    private func freshDefaults() -> UserDefaults {
        let name = "CalendarModelTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    private func block(_ start: String, minutes: Int = 60,
                       harness: String = "claude", home: String = "/h",
                       session: String = "s", project: String? = nil,
                       cwd: String? = nil) -> ActivityBlock {
        let start = utc(start)
        var block = ActivityBlock(harness: harness, accountHome: home, sessionId: session,
                                  start: start,
                                  end: start.addingTimeInterval(TimeInterval(minutes * 60)),
                                  responses: 1, cwd: cwd)
        block.project = project ?? session
        return block
    }

    @Test func theWeekRangeStartsOnTheUsersFirstWeekday() async {
        // 14 Oct 2026 is a Wednesday.
        var mondayFirst = calendar; mondayFirst.firstWeekday = 2
        let week = model(firstWeekday: 2).visibleRange
        #expect(week.start == utc("2026-10-12T00:00:00Z"))
        #expect(week.end == utc("2026-10-19T00:00:00Z"))

        let sundayFirst = model(firstWeekday: 1).visibleRange
        #expect(sundayFirst.start == utc("2026-10-11T00:00:00Z"))
    }

    @Test func dayModeShowsOneDayAndNavigationMovesTheAnchor() async {
        let model = model()
        model.mode = .day
        #expect(model.visibleRange.start == utc("2026-10-14T00:00:00Z"))
        model.next()
        #expect(model.visibleRange.start == utc("2026-10-15T00:00:00Z"))
        model.previous()
        model.previous()
        #expect(model.visibleRange.start == utc("2026-10-13T00:00:00Z"))
        model.mode = .week
        model.next()
        #expect(model.visibleRange.start == utc("2026-10-19T00:00:00Z"))
        model.today()
        #expect(model.visibleRange.start == utc("2026-10-12T00:00:00Z"))
    }

    @Test func hiddenHarnessesAndAccountsFilterVisibleBlocks() async {
        let model = model(blocks: [
            block("2026-10-14T09:00:00Z", harness: "claude", home: "/h"),
            block("2026-10-14T10:00:00Z", harness: "claude", home: "/work", session: "w"),
            block("2026-10-14T11:00:00Z", harness: "codex", session: "c"),
        ])
        await model.load()
        #expect(model.visibleBlocks.count == 3)

        model.toggle(harness: "claude", accountHome: "/work")
        #expect(model.visibleBlocks.count == 2)
        #expect(!model.visibleBlocks.contains { $0.accountHome == "/work" })

        model.toggle("claude")
        #expect(model.visibleBlocks.map(\.harness) == ["codex"])
        // Hiding the harness hides its remaining account too.
        #expect(model.isHidden(harness: "claude", accountHome: "/h"))
        // But its legend row keeps its time, shown dimmed.
        #expect(model.totals["claude"] == 7_200)
        #expect(model.accountTotals(for: "claude")["/work"] == 3_600)
    }

    @Test func totalsSumVisibleBlocksClippedToTheRange() async {
        let model = model(blocks: [
            // Two hours inside the week.
            block("2026-10-14T09:00:00Z", minutes: 120),
            // A block that started last week counts only its in-range
            // part: Sunday 23:00 to Monday 02:00 is two hours inside.
            block("2026-10-11T23:00:00Z", minutes: 180, session: "edge"),
            block("2026-10-14T12:00:00Z", harness: "codex", session: "c"),
        ])
        await model.load()
        #expect((model.totals["claude"] ?? 0) == 14_400)
        #expect(model.totals["codex"] == 3_600)
    }

    @Test func totalsAreWallClockUnionAcrossProjects() async {
        let model = model(blocks: [
            // Two projects of the same harness overlapping in time:
            // the hour they share counts once.
            block("2026-10-14T09:00:00Z", minutes: 120, project: "one"),
            block("2026-10-14T09:30:00Z", minutes: 120, session: "p2", project: "two"),
            block("2026-10-14T13:00:00Z", harness: "codex", session: "c"),
        ])
        await model.load()
        #expect((model.totals["claude"] ?? 0) == 9_000)  // 09:00–11:30
        #expect(model.totals["codex"] == 3_600)
        #expect(model.accountTotals(for: "claude")["/h"] == 9_000)
    }

    @Test func accountsListOnlyHomesWithActivityInTheRange() async {
        let model = model(blocks: [
            block("2026-10-14T09:00:00Z", home: "/h"),
            block("2026-10-14T10:00:00Z", home: "/work", session: "w"),
            block("2026-11-01T10:00:00Z", home: "/away", session: "far"),
        ])
        await model.load()
        #expect(model.accounts(for: "claude").sorted() == ["/h", "/work"])
        #expect(model.accountName(for: "/h") == "/h")
    }

    @Test func knownAccountsJoinTheSplitEvenWhenIdleInRange() async {
        // Cortex ran Oct 14–15 only; an Oct 8–13 week still lists it.
        let model = model(
            blocks: [block("2026-10-14T09:00:00Z", home: "/h")],
            knownAccounts: ["claude": ["/h", "/profiles/cortex"]])
        await model.load()
        #expect(model.accounts(for: "claude") == ["/h", "/profiles/cortex"])
        #expect(model.accountsExpanded("claude"))
        // The idle account lists but contributes nothing.
        #expect(model.accountTotals(for: "claude")["/profiles/cortex"] == nil)
        #expect(model.accountTotals(for: "claude")["/h"] == 3600)
        // A known account hidden while idle stays hidden for its harness.
        model.toggle(harness: "claude", accountHome: "/profiles/cortex")
        #expect(model.isHidden(harness: "claude", accountHome: "/profiles/cortex"))
    }

    @Test func accountExpansionCollapsesAndPersistsPerHarness() async {
        let defaults = freshDefaults()
        let known = ["claude": ["/h", "/w"], "codex": ["/c", "/c2"]]
        let first = model(blocks: [block("2026-10-14T09:00:00Z", home: "/h")],
                          knownAccounts: known, defaults: defaults)
        await first.load()
        #expect(first.accountsExpanded("claude"))
        #expect(first.accountsExpanded("codex")) // no blocks needed at all
        first.toggleAccountsExpanded("claude")
        #expect(!first.accountsExpanded("claude"))
        #expect(first.accountsExpanded("codex"))

        // A fresh model over the same defaults keeps the collapse.
        let restored = model(blocks: [block("2026-10-14T09:00:00Z", home: "/h")],
                             knownAccounts: known, defaults: defaults)
        await restored.load()
        #expect(!restored.accountsExpanded("claude"))
        #expect(restored.accountsExpanded("codex"))

        // One-account harnesses never expand or persist a row split.
        #expect(!restored.accountsExpanded("devin"))
    }

    @Test func knownAccountsAreReadOncePerHarnessPerLoad() async {
        // The profile store is hit lazily and memoised: a redraw calling
        // accounts(for:) per block never re-reads the disk mid-render,
        // and a load refreshes it exactly once more.
        var counts: [String: Int] = [:]
        let counted = CalendarModel(
            providers: [FakeActivity(blocks: [block("2026-10-14T09:00:00Z", home: "/h")])],
            knownAccounts: { counts[$0, default: 0] += 1; return ["/h", "/w"] },
            defaults: freshDefaults(),
            now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await counted.load()
        for _ in 0..<50 { _ = counted.accounts(for: "claude") }
        #expect(counts["claude"] == 1)
        // The next load re-reads the store once, not per call.
        await counted.load()
        _ = counted.accounts(for: "claude")
        #expect(counts["claude"] == 2)
    }

    @Test func theAccountLabelMapsHomesToProfileNames() async {
        var cal = calendar; cal.firstWeekday = 2
        let model = CalendarModel(providers: [FakeActivity(blocks: [
            block("2026-10-14T09:00:00Z", home: "/profiles/work"),
        ])], accountLabel: { $0 == "/profiles/work" ? "Work" : "Default" },
            defaults: freshDefaults(), now: { utc("2026-10-14T12:00:00Z") }, calendar: cal)
        await model.load()
        #expect(model.accountName(for: "/profiles/work") == "Work")
        #expect(model.accountName(for: "/unknown") == "Default")
    }

    @Test func aFailingProviderKeepsItsLastBlocksAndReportsTheError() async {
        let fail = Flag()
        let provider = TogglingActivity(
            blocks: [block("2026-10-14T09:00:00Z")], fails: fail)
        var cal = calendar; cal.firstWeekday = 2
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: cal)
        await model.load()
        #expect(model.blocks.count == 1)
        #expect(model.loadError == nil)

        fail.value = true
        await model.load()
        // The last good blocks stay; the error is on the model.
        #expect(model.blocks.count == 1)
        #expect(model.loadError == "ledger read failed")

        fail.value = false
        await model.load()
        #expect(model.loadError == nil)
    }

    @Test func hiddenSetsPersistAcrossModels() async {
        let defaults = freshDefaults()
        let first = model(defaults: defaults)
        first.toggle("codex")
        first.toggle(harness: "claude", accountHome: "/work")

        let second = model(defaults: defaults)
        #expect(second.hiddenHarnesses == ["codex"])
        #expect(second.hiddenAccounts == ["claude|/work"])
    }

    @Test func aBlockSpanningMidnightKeepsItsSessionsFromBothSides() async {
        // Sunday 23:50 and Monday 00:05 in one project: Monday's day
        // view must show the block from 23:50 with both sessions, even
        // though providers only answer blocks overlapping the range they
        // were asked for. The model widens the query by a day each side
        // so the grouping sees the whole story.
        let model = model(blocks: [
            block("2026-10-18T23:50:00Z", minutes: 10, session: "a", project: "repo"),
            block("2026-10-19T00:05:00Z", minutes: 10, session: "b", project: "repo"),
        ], now: utc("2026-10-19T12:00:00Z"))
        model.mode = .day
        await model.load()
        #expect(model.visibleBlocks.count == 1)
        #expect(model.visibleBlocks[0].start == utc("2026-10-18T23:50:00Z"))
        #expect(model.visibleBlocks[0].sessions.map(\.sessionId) == ["a", "b"])
    }

}

/// `CalendarSessionIndex`: one session id in two accounts resolves each
/// block to its own account's row.
@Suite struct CalendarSessionIndexTests {
    private func row(home: String?, session: String = "s") -> SessionsModel.Row {
        let usage = home.map {
            SessionUsage(sessionId: session, harness: "claude", accountHome: $0,
                         cwd: nil, models: [], totals: UsageTotals(),
                         firstTimestamp: utc("2026-10-14T09:00:00Z"),
                         lastTimestamp: utc("2026-10-14T09:30:00Z"))
        }
        return SessionsModel.Row(
            session: SessionInfo(id: session, harness: "claude", agent: "claude",
                                 cwd: home, title: "Session \(home ?? "default")",
                                 titleSource: nil, lastActiveAt: utc("2026-10-14T09:30:00Z"),
                                 status: nil, resume: nil),
            usage: usage,
            id: "\(home ?? "")|\(session)")
    }

    private func block(home: String) -> ActivityBlock {
        ActivityBlock(harness: "claude", accountHome: home, sessionId: "s",
                      start: utc("2026-10-14T09:00:00Z"),
                      end: utc("2026-10-14T09:05:00Z"), responses: 1)
    }

    private func found(_ index: CalendarSessionIndex, home: String) -> SessionsModel.Row {
        index.row(for: block(home: home).sessions[0], harness: "claude", accountHome: home)
    }

    @Test func theSameSessionIdInTwoAccountsResolvesEachBlock() {
        let index = CalendarSessionIndex(rows: [row(home: "/h"), row(home: "/work")])
        #expect(found(index, home: "/h").usage?.accountHome == "/h")
        #expect(found(index, home: "/work").usage?.accountHome == "/work")
    }

    @Test func aRowWithoutUsageIsTheFallbackOnly() {
        let index = CalendarSessionIndex(rows: [row(home: nil)])
        // No account-specific row: the fallback answers.
        #expect(found(index, home: "/h").session.title == "Session default")
        // An account-specific row wins over the fallback.
        let both = CalendarSessionIndex(rows: [row(home: nil), row(home: "/work")])
        #expect(found(both, home: "/work").usage?.accountHome == "/work")
        #expect(found(both, home: "/h").session.title == "Session default")
    }

    @Test func rowsForAMultiSessionBlockComeBackSorted() {
        let index = CalendarSessionIndex(rows: [row(home: "/h")])
        let block = ActivityBlock(
            harness: "claude", accountHome: "/h", project: "p",
            sessions: [
                .init(sessionId: "late", start: utc("2026-10-14T10:00:00Z"),
                      end: utc("2026-10-14T10:30:00Z"), responses: 2),
                .init(sessionId: "s", cwd: "/work",
                      start: utc("2026-10-14T09:00:00Z"),
                      end: utc("2026-10-14T09:30:00Z"), responses: 1),
            ],
            start: utc("2026-10-14T09:00:00Z"), end: utc("2026-10-14T10:30:00Z"),
            responses: 3)
        let rows = index.rows(for: block)
        #expect(rows.map(\.ref.sessionId) == ["s", "late"])
        #expect(rows[0].row.session.title == "Session /h")
        #expect(rows[1].row.session.title == nil)
        #expect(rows[1].row.session.id == "late")
    }

    @Test func theCardCapsItsSessionListAtEightAndCountsTheRest() {
        let index = CalendarSessionIndex(rows: [row(home: "/h")])
        let refs = (0..<11).map { i in
            ActivityBlock.SessionRef(sessionId: "s\(i)",
                                     start: utc("2026-10-14T09:00:00Z")
                                        .addingTimeInterval(TimeInterval(i * 60)),
                                     end: utc("2026-10-14T09:00:00Z")
                                        .addingTimeInterval(TimeInterval(i * 60 + 30)),
                                     responses: 1)
        }
        let block = ActivityBlock(harness: "claude", accountHome: "/h", project: "p",
                                  sessions: refs,
                                  start: refs[0].start, end: refs[10].end,
                                  responses: 11)
        let (shown, more) = index.rows(for: block, limit: 8)
        #expect(shown.count == 8)
        #expect(more == 3)
        #expect(shown.map(\.ref.sessionId) == (0..<8).map { "s\($0)" })
        // Fewer than the limit reports no overflow.
        let small = ActivityBlock(harness: "claude", accountHome: "/h", project: "p",
                                  sessions: Array(refs.prefix(3)),
                                  start: refs[0].start, end: refs[2].end,
                                  responses: 3)
        let few = index.rows(for: small, limit: 8)
        #expect(few.shown.count == 3)
        #expect(few.more == 0)
    }

    @Test func anUnlistedSessionGetsAStandInRow() {
        let index = CalendarSessionIndex(rows: [])
        let ref = ActivityBlock.SessionRef(sessionId: "gone", cwd: "/work/project",
                                           start: utc("2026-10-14T09:00:00Z"),
                                           end: utc("2026-10-14T09:10:00Z"), responses: 2)
        let row = index.row(for: ref, harness: "claude", accountHome: "/h")
        #expect(row.session.id == "gone")
        #expect(row.session.cwd == "/work/project")
        #expect(row.session.resume == nil)
        #expect(row.usage == nil)
        #expect(row.id.contains("gone"))
    }
}


/// A provider whose reads the test holds open until it lets them through.
private final class HeldActivity: ActivityProviding, @unchecked Sendable {
    var blocks: [ActivityBlock]
    /// What non-forced (prefetch) reads answer; nil means the same blocks.
    var prefetchBlocks: [ActivityBlock]?
    /// Hold non-forced reads too, so prefetches can be ordered after loads.
    var holdPrefetch = false
    private var state = State()
    init(blocks: [ActivityBlock]) { self.blocks = blocks }
    var calls: Int { state.withLock { $0.calls } }
    var held: Bool {
        get { state.withLock { $0.held } }
        set { state.withLock { $0.held = newValue } }
    }
    /// Let held reads through; calls after this never wait.
    func release() {
        let pending = state.withLock { s -> [CheckedContinuation<Void, Never>] in
            s.held = false
            defer { s.waiting.removeAll() }
            return s.waiting
        }
        pending.forEach { $0.resume() }
    }
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) async throws -> [ActivityBlock] {
        state.withLock { $0.calls += 1 }
        let waitFirst = !force && holdPrefetch ? true : state.withLock { $0.held }
        if waitFirst {
            await withCheckedContinuation { continuation in
                let wait = state.withLock { s -> Bool in
                    if s.held || (!force && holdPrefetch) {
                        s.waiting.append(continuation)
                        return true
                    }
                    return false
                }
                if !wait { continuation.resume() }
            }
        }
        let source = !force ? (prefetchBlocks ?? blocks) : blocks
        return source.filter { $0.end >= range.start && $0.start < range.end }
    }
    private final class State: @unchecked Sendable {
        var calls = 0
        var held = true
        var waiting: [CheckedContinuation<Void, Never>] = []
        private let lock = NSLock()
        func withLock<T>(_ body: (State) -> T) -> T {
            lock.lock()
            defer { lock.unlock() }
            return body(self)
        }
    }
}

/// A snapshot store the test fills and reads back.
private final class SnapSpy: SnapshotStoring, @unchecked Sendable {
    var saved: [CalendarSnapshot] = []
    func load() async -> CalendarSnapshot? { saved.last }
    func save(_ content: CalendarSnapshot) async { saved.append(content) }
}

@MainActor
@Suite struct CalendarCacheTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        c.firstWeekday = 2
        return c
    }

    private func freshDefaults() -> UserDefaults {
        let name = "CalendarCacheTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    private func block(_ start: String, session: String = "s",
                       project: String? = nil, cwd: String? = nil) -> ActivityBlock {
        let start = utc(start)
        var block = ActivityBlock(harness: "claude", accountHome: "/h", sessionId: session,
                                  start: start, end: start.addingTimeInterval(3600),
                                  responses: 1, cwd: cwd)
        block.project = project ?? session
        return block
    }

    @Test func aRestoredSnapshotPaintsBeforeTheLoadLands() async {
        let snap = CalendarSnapshot(ranges: [
            .init(start: utc("2026-10-12T00:00:00Z"), end: utc("2026-10-19T00:00:00Z"),
                  providers: [0: [block("2026-10-14T09:00:00Z")]])])
        let held = HeldActivity(blocks: [])
        let model = CalendarModel(providers: [held], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") },
                                  calendar: calendar, restored: snap)
        async let load: () = model.load()
        await Task.yield()
        // The cached range's blocks are on screen while the provider's
        // read is still suspended.
        #expect(model.blocks.count == 1)
        held.release()
        await load
        #expect(model.blocks.isEmpty)
    }

    @Test func prefetchFillsNeighbouringRangesWithoutPublishing() async throws {
        let provider = FakeActivity(blocks: [block("2026-10-05T10:00:00Z", session: "prev"),
                                             block("2026-10-14T10:00:00Z", session: "now"),
                                             block("2026-10-21T10:00:00Z", session: "next")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load()
        #expect(model.blocks.map(\.project) == ["now"])
        // Give the utility-priority prefetch tasks a moment.
        try await Task.sleep(for: .milliseconds(300))
        // Moving back a week publishes from the cache: the blocks appear
        // before the provider's load could run again.
        model.previous()
        #expect(model.blocks.map(\.project) == ["prev"])
    }

    @Test func aCompletedScanKeepsStaleBlocksUntilTheRefetchLands() async {
        let held = HeldActivity(blocks: [block("2026-10-14T09:00:00Z")])
        held.held = false
        let model = CalendarModel(providers: [held], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load()
        #expect(model.blocks.count == 1)
        // Next load hangs; the stale range stays visible meanwhile.
        held.held = true
        model.noteScanCompleted()
        async let load: () = model.load(force: true)
        await Task.yield()
        await Task.yield()
        #expect(model.blocks.count == 1)
        held.release()
        await load
        #expect(held.calls >= 2)
    }

    @Test func theSnapshotPersistsTheRecentRanges() async throws {
        let spy = SnapSpy()
        let provider = FakeActivity(blocks: [block("2026-10-14T09:00:00Z")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") },
                                  calendar: calendar, snapshots: spy)
        await model.load()
        try await Task.sleep(for: .milliseconds(200))
        let saved = try #require(spy.saved.last)
        #expect(saved.ranges.contains {
            $0.start == utc("2026-10-12T00:00:00Z") && $0.end == utc("2026-10-19T00:00:00Z")
        })
        // A new model hydrates from it.
        let restored = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                     now: { utc("2026-10-14T12:00:00Z") },
                                     calendar: calendar, restored: saved)
        async let load: () = restored.load()
        await Task.yield()
        #expect(restored.blocks.count == 1)
        await load
    }

    @Test func aTwentyEightDayRangeCachesAndPublishes() async {
        let provider = FakeActivity(blocks: [block("2026-10-05T10:00:00Z", session: "a"),
                                             block("2026-11-01T10:00:00Z", session: "b")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        let month = DateInterval(start: utc("2026-10-05T00:00:00Z"),
                                 end: utc("2026-11-02T00:00:00Z"))
        await model.load(range: month, force: false)
        #expect(model.blocks.map(\.project) == ["a", "b"])
        // The long range is cached like any other: a reload paints it
        // synchronously even with a provider that never answers.
        let held = HeldActivity(blocks: [])
        let cached = CalendarModel(providers: [held], defaults: freshDefaults(),
                                   now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar,
                                   restored: CalendarSnapshot(ranges: [
                                       .init(start: month.start, end: month.end,
                                             providers: [0: [block("2026-10-05T10:00:00Z")]])]))
        async let load: () = cached.load(range: month, force: false)
        await Task.yield()
        #expect(cached.blocks.count == 1)
        held.release()
        await load
    }
}

extension CalendarCacheTests {
    @Test func neighboursUseCalendarArithmeticAcrossDST() async {
        // New York's 2026 spring-forward week is 167 hours long; only
        // calendar arithmetic lands the neighbour on next()'s range.
        var newYork = Calendar(identifier: .gregorian)
        newYork.timeZone = TimeZone(identifier: "America/New_York")!
        newYork.firstWeekday = 1
        let anchor = utc("2026-03-06T12:00:00Z")  // Friday of the DST week
        let model = CalendarModel(providers: [], defaults: freshDefaults(),
                                  now: { anchor }, calendar: newYork)
        model.mode = .week
        let expected = model.range(for: model.shiftedAnchor(1, mode: .week), mode: .week)
        model.next()
        #expect(model.visibleRange == expected)
        #expect(expected.duration == 7 * 86400 - 3600)  // 167 hours

        model.mode = .day
        model.go(to: utc("2026-03-07T12:00:00Z"))
        let dayAfter = model.range(for: model.shiftedAnchor(1, mode: .day), mode: .day)
        model.next()
        #expect(model.visibleRange == dayAfter)
        #expect(dayAfter.duration == 23 * 3600)  // DST day
    }

    @Test func aLatePrefetchDoesNotOverwriteFresherBlocks() async throws {
        let week = DateInterval(start: utc("2026-10-12T00:00:00Z"),
                                end: utc("2026-10-19T00:00:00Z"))
        let nextWeek = DateInterval(start: week.end, end: utc("2026-10-26T00:00:00Z"))
        let stale = [block("2026-10-20T09:00:00Z", session: "prefetch")]
        let fresh = [block("2026-10-20T10:00:00Z", session: "fresh")]
        let provider = HeldActivity(blocks: fresh)
        provider.prefetchBlocks = stale
        provider.held = false
        provider.holdPrefetch = true
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load(force: true)
        // The neighbour prefetch into next week is still suspended.
        model.next()
        #expect(model.visibleRange == nextWeek)
        await model.load(force: true)
        #expect(model.blocks.map(\.project) == ["fresh"])
        // Let the prefetch land: the fresh write wins.
        provider.holdPrefetch = false
        provider.release()
        try await Task.sleep(for: .milliseconds(200))
        await model.load(force: true)
        #expect(model.blocks.map(\.project) == ["fresh"])
    }
}

extension CalendarCacheTests {
    @Test func aFailingPrefetchKeepsTheProvidersCachedBlocks() async throws {
        let flag = Flag()
        let good = FakeActivity(blocks: [block("2026-10-14T09:00:00Z", session: "a")])
        let flaky = TogglingActivity(blocks: [block("2026-10-14T10:00:00Z",
                                                    session: "b")], fails: flag)
        let model = CalendarModel(providers: [good, flaky], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load()
        try await Task.sleep(for: .milliseconds(300))  // let prefetches land
        #expect(model.blocks.count == 2)
        // Every cached range goes stale; the flaky provider now fails.
        flag.value = true
        model.noteScanCompleted()
        model.next()
        await model.load(force: true)
        // Give the stale-range prefetch (back on the October 12 week) a
        // moment to finish with the flaky provider failing.
        try await Task.sleep(for: .milliseconds(300))
        model.previous()
        // The stale cache paints synchronously and must still hold both
        // providers' blocks.
        #expect(model.blocks.map(\.project).sorted() == ["a", "b"])
    }

    @Test func aSnapshotFromAnOlderVersionIsDiscarded() throws {
        let temp = FileManager.default.temporaryDirectory
            .appending(path: "calendar-snap-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: temp) }
        // The version-1 shape: per-session blocks, no project/sessions.
        let old = """
            {"version":1,"savedAt":778000000,
             "ranges":[{"start":778000000,"end":778600000,
              "providers":{"0":[{"harness":"claude","accountHome":"/h",
                "sessionId":"s","start":778000000,"end":778003600,
                "responses":2,"cwd":"/w"}]}}]}
            """
        let url = temp.appending(path: "app-calendar.json")
        try FileManager.default.createDirectory(at: temp, withIntermediateDirectories: true)
        try old.write(to: url, atomically: true, encoding: .utf8)
        #expect(SnapshotFile<CalendarSnapshot>(url: url).load() == nil)
        #expect(CalendarSnapshot.currentVersion == 2)
    }

    @Test func aRestoredSnapshotFillsBlocksBeforeAnyLoad() {
        let snap = CalendarSnapshot(ranges: [
            .init(start: utc("2026-10-12T00:00:00Z"), end: utc("2026-10-19T00:00:00Z"),
                  providers: [0: [block("2026-10-14T09:00:00Z")]])])
        let model = CalendarModel(providers: [], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") },
                                  calendar: calendar, restored: snap)
        #expect(model.blocks.count == 1)
    }
}
