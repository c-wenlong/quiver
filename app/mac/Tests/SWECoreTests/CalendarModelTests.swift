import Foundation
import Testing
@testable import SWECore

/// Shared with MonthLayoutTests.
struct FakeActivity: ActivityProviding {
    var blocks: [ActivityBlock]
    /// Like the ledger, only blocks overlapping the range come back.
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) throws -> [ActivityBlock] {
        blocks.filter { $0.end >= range.start && $0.start < range.end }
    }
}

/// A provider the test can flip between a reading and a failure.
private struct TogglingActivity: ActivityProviding {
    struct Failure: LocalizedError {
        var errorDescription: String? { "ledger read failed" }
    }
    var blocks: [ActivityBlock]
    var fails: Box<Bool>
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
        let defaults = TestDefaults("CalendarModelTests")
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

    @Test func accountLabelsAreReadOncePerHomePerLoad() async {
        // Same lazy memo as knownAccounts: the label closure reaches the
        // accounts store, so a redraw asking per block calls it once.
        var counts: [String: Int] = [:]
        let counted = CalendarModel(
            providers: [FakeActivity(blocks: [block("2026-10-14T09:00:00Z", home: "/h")])],
            accountLabel: { counts[$0, default: 0] += 1; return "L\($0)" },
            defaults: freshDefaults(),
            now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await counted.load()
        for _ in 0..<50 { _ = counted.accountName(for: "/h") }
        #expect(counts["/h"] == 1)
        // The next load re-reads the store once, not per call.
        await counted.load()
        _ = counted.accountName(for: "/h")
        #expect(counts["/h"] == 2)
    }

    @Test func accountsFollowTheWindowAfterLandingAndLoading() async {
        // The memo is per window: landing elsewhere re-reads it, and a
        // load replacing the blocks re-reads it too.
        let m = model(blocks: [
            block("2026-10-14T09:00:00Z", home: "/h1"),
            block("2026-10-21T09:00:00Z", home: "/h2"),
        ])
        await m.load()
        #expect(m.accounts(for: "claude") == ["/h1"])
        // A cached memo must not survive the anchor's move.
        m.land(on: utc("2026-10-21T00:00:00Z"))
        #expect(m.accounts(for: "claude") == ["/h2"])
        await m.load()
        #expect(m.accounts(for: "claude") == ["/h2"])
        m.land(on: utc("2026-10-14T00:00:00Z"))
        await m.load()
        #expect(m.accounts(for: "claude") == ["/h1"])
    }

    @Test func theRenderRangeBuffersOneWindowOnEachSide() {
        let model = model() // anchor 2026-10-14, a Wednesday
        model.mode = .day
        #expect(model.renderRange == DateInterval(start: utc("2026-10-13T00:00:00Z"),
                                                  end: utc("2026-10-16T00:00:00Z")))
        model.mode = .days(4)
        #expect(model.renderRange == DateInterval(start: utc("2026-10-10T00:00:00Z"),
                                                  end: utc("2026-10-22T00:00:00Z")))
        model.mode = .week // Monday-first week Oct 12–19, ±7 days
        #expect(model.renderRange == DateInterval(start: utc("2026-10-05T00:00:00Z"),
                                                  end: utc("2026-10-26T00:00:00Z")))
        model.mode = .month // never slides: render == visible
        #expect(model.renderRange == model.visibleRange)
    }

    @Test func aLandedLeadingDayBecomesTheAnchorPerMode() {
        let model = model()
        model.mode = .day
        model.land(on: utc("2026-10-20T00:00:00Z"))
        #expect(model.visibleRange == DateInterval(start: utc("2026-10-20T00:00:00Z"),
                                                   end: utc("2026-10-21T00:00:00Z")))
        model.mode = .days(4)
        model.land(on: utc("2026-10-21T15:00:00Z"))
        #expect(model.visibleRange == DateInterval(start: utc("2026-10-21T00:00:00Z"),
                                                   end: utc("2026-10-25T00:00:00Z")))
        // Week mode snaps a mid-week landing to the week start.
        model.mode = .week
        model.land(on: utc("2026-10-21T00:00:00Z"))
        #expect(model.visibleRange == DateInterval(start: utc("2026-10-19T00:00:00Z"),
                                                   end: utc("2026-10-26T00:00:00Z")))
        // Month mode does not slide at all.
        model.mode = .month
        let month = model.visibleRange
        model.land(on: utc("2026-11-15T00:00:00Z"))
        #expect(model.visibleRange == month)
    }

    @Test func aSettledScrollOffsetRoundsToTheNearestSnapTarget() {
        let model = model() // anchor 2026-10-14
        model.mode = .days(4) // render days Oct 10 to Oct 21
        #expect(model.settledLeadingDay(offset: 0, columnWidth: 100) == utc("2026-10-10T00:00:00Z"))
        #expect(model.settledLeadingDay(offset: 240, columnWidth: 100) == utc("2026-10-12T00:00:00Z"))
        #expect(model.settledLeadingDay(offset: 260, columnWidth: 100) == utc("2026-10-13T00:00:00Z"))
        // Rubber-banding past either end clamps to the render days.
        #expect(model.settledLeadingDay(offset: -50, columnWidth: 100) == utc("2026-10-10T00:00:00Z"))
        #expect(model.settledLeadingDay(offset: 5000, columnWidth: 100) == utc("2026-10-21T00:00:00Z"))
        // Week mode snaps whole weeks: render days start Monday Oct 5.
        model.mode = .week
        #expect(model.settledLeadingDay(offset: 1000, columnWidth: 100) == utc("2026-10-12T00:00:00Z"))
        #expect(model.settledLeadingDay(offset: 1100, columnWidth: 100) == utc("2026-10-19T00:00:00Z"))
        // No width yet, or a mode that never slides: nothing to land on.
        #expect(model.settledLeadingDay(offset: 100, columnWidth: 0) == nil)
        model.mode = .month
        #expect(model.settledLeadingDay(offset: 100, columnWidth: 100) == nil)
    }

    @Test func theRestingDayKeepsItsPlaceAfterTheRenderDaysRecentre() {
        // Landing re-centres the render days while the scroll view keeps
        // its pixel offset: resting on Oct 7 at 300pt re-centres Oct 7 to
        // column 4, so 300pt would now read Oct 6. The page scrolls to
        // `leadingOffset` instead, which reads Oct 7 again.
        let model = model() // anchor 2026-10-14
        model.mode = .days(4) // render days Oct 10 to Oct 21
        #expect(model.leadingOffset(of: utc("2026-10-14T00:00:00Z"), columnWidth: 100) == 400)
        let rested = model.settledLeadingDay(offset: 300, columnWidth: 100)!
        #expect(rested == utc("2026-10-13T00:00:00Z"))
        model.land(on: utc("2026-10-07T00:00:00Z")) // render days Oct 3 to Oct 14
        let x = model.leadingOffset(of: model.visibleRange.start, columnWidth: 100)!
        #expect(x == 400)
        #expect(model.settledLeadingDay(offset: x, columnWidth: 100) == utc("2026-10-07T00:00:00Z"))
        // Week mode: the visible week is always the second of three.
        model.mode = .week
        #expect(model.leadingOffset(of: model.visibleRange.start, columnWidth: 100) == 700)
        // Not a column, or no width yet.
        #expect(model.leadingOffset(of: utc("2027-01-01T00:00:00Z"), columnWidth: 100) == nil)
        #expect(model.leadingOffset(of: model.visibleRange.start, columnWidth: 0) == nil)
    }

    @Test func aSingleResponseBlockExactlyAtTheWindowStartCounts() async {
        // start == end == window.start: start < end fails, so a zero-length
        // block at the boundary needs its own clause or the day reads empty.
        var single = ActivityBlock(harness: "claude", accountHome: "/h",
                                   sessionId: "s",
                                   start: utc("2026-10-14T00:00:00Z"),
                                   end: utc("2026-10-14T00:00:00Z"), responses: 1)
        single.project = "s"
        let model = model(blocks: [single])
        model.mode = .day
        await model.load()
        #expect(model.windowBlocks.count == 1)
    }

    // The time limit fails the test instead of hanging the suite if the
    // prefetches never settle or the load never asks the provider.
    @Test(.timeLimit(.minutes(1)))
    func anAnchorMoveKeepsTheOldBlocksUntilTheLoadLands() async {
        let held = HeldActivity(blocks: [block("2026-10-14T09:00:00Z")])
        held.held = false
        let model = CalendarModel(providers: [held], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .day
        await model.load()
        #expect(model.blocks.count == 1)
        // The neighbouring days' prefetches land first, so the next read
        // the provider sees is the landing's own.
        await settled(model)
        let calls = held.calls
        // Oct 20's render range (Oct 19-21) shares no day with anything
        // read so far: a cache miss. The provider read hangs, and the old
        // blocks stay up rather than flashing empty.
        held.held = true
        model.land(on: utc("2026-10-20T00:00:00Z"))
        async let load: () = model.load()
        await held.started(calls + 1)
        #expect(model.blocks.count == 1)
        held.release()
        await load
        // Once it lands, the new range's own (empty) blocks replace them.
        #expect(model.blocks.isEmpty)
    }

    @Test func theCacheIsKeyedByRenderRange() async {
        let provider = FakeActivity(blocks: [block("2026-10-14T09:00:00Z")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .day // render range Oct 13–15
        await model.load()
        // A day two weeks away is a different render range entirely.
        model.go(to: utc("2026-10-28T12:00:00Z"))
        await model.load()
        #expect(model.blocks.isEmpty)
        // Landing back hits the Oct 13–15 render range exactly: its
        // blocks repaint synchronously, before any provider read lands.
        model.land(on: utc("2026-10-14T00:00:00Z"))
        #expect(model.blocks.map(\.project) == ["s"])
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
        let fail = Box(false)
        let provider = TogglingActivity(
            blocks: [block("2026-10-14T09:00:00Z")], fails: fail)
        var cal = calendar; cal.firstWeekday = 2
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: cal)
        await model.load()
        #expect(model.blocks.count == 1)
        #expect(model.loadError == nil)

        // A fresh cached range asks no provider; a finished scan is what
        // sends the next load back to them.
        fail.value = true
        model.noteScanCompleted()
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
        first.toggleUsage("claude-default")

        let second = model(defaults: defaults)
        #expect(second.hiddenHarnesses == ["codex"])
        #expect(second.hiddenAccounts == ["claude|/work"])
        #expect(second.hiddenUsage == ["claude-default"])
    }

    @Test func usageHidingTogglesAndRestores() async {
        let defaults = freshDefaults()
        let first = model(defaults: defaults)
        #expect(!first.isUsageHidden("claude-default"))
        first.toggleUsage("claude-default")
        #expect(first.isUsageHidden("claude-default"))

        let second = model(defaults: defaults)
        #expect(second.isUsageHidden("claude-default"))
        second.toggleUsage("claude-default")
        #expect(!second.isUsageHidden("claude-default"))
    }

    @Test func usageWindowHidingIsSeparateFromItsAccount() async {
        let defaults = freshDefaults()
        let first = model(defaults: defaults)
        #expect(!first.isUsageHidden("claude-default", window: "5h"))
        first.toggleUsage("claude-default", window: "5h")
        #expect(first.isUsageHidden("claude-default", window: "5h"))
        // The window bit never leaks into the account's own toggle or a
        // sibling window.
        #expect(!first.isUsageHidden("claude-default"))
        #expect(!first.isUsageHidden("claude-default", window: "7d"))

        let second = model(defaults: defaults)
        #expect(second.hiddenUsageWindows == ["claude-default|5h"])
        #expect(second.isUsageHidden("claude-default", window: "5h"))
    }

    @Test func usageWindowRowsCollapseAndPersist() async {
        let defaults = freshDefaults()
        let first = model(defaults: defaults)
        // One window folds into the account row; a second gets rows to
        // collapse, like a harness gaining a second account.
        #expect(!first.usageWindowsExpanded("claude-default", windows: 1))
        #expect(first.usageWindowsExpanded("claude-default", windows: 2))
        first.toggleUsageWindowsExpanded("claude-default")
        #expect(!first.usageWindowsExpanded("claude-default", windows: 2))

        let second = model(defaults: defaults)
        #expect(!second.usageWindowsExpanded("claude-default", windows: 2))
        second.toggleUsageWindowsExpanded("claude-default")
        #expect(second.usageWindowsExpanded("claude-default", windows: 2))
    }

    @Test func showingAUsageAccountRestoresItsLoneWindow() async {
        let defaults = freshDefaults()
        let first = model(defaults: defaults)
        // A lone window has no row of its own, so the account row is its
        // only way back on: showing must clear a hidden window key too.
        first.toggleUsage("claude-default", window: "5h")
        first.showUsage("claude-default", windows: ["5h"])
        #expect(!first.isUsageHidden("claude-default", window: "5h"))

        let second = model(defaults: defaults)
        #expect(second.hiddenUsageWindows.isEmpty)
        // Windows outside the ones named stay hidden.
        second.toggleUsage("claude-default", window: "7d")
        second.showUsage("claude-default", windows: ["5h"])
        #expect(second.isUsageHidden("claude-default", window: "7d"))
        #expect(!second.isUsageHidden("claude-default", window: "5h"))
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

    @Test func aFeedBlockFromAnOlderProfileFindsTheMergedRow() {
        // The Sessions page merged this Grok Bot conversation under the
        // home that polled it last; the block was drawn from the first.
        let usage = SessionUsage(sessionId: "conv-1", harness: "grok-bot", accountHome: "/second",
                                 cwd: nil, models: ["grok-bot-default"], totals: UsageTotals(),
                                 firstTimestamp: utc("2026-10-14T09:00:00Z"),
                                 lastTimestamp: utc("2026-10-14T09:30:00Z"))
        let merged = SessionsModel.Row(
            session: SessionInfo(id: "conv-1", harness: "grok-bot", agent: "Grok Bot", cwd: nil, title: nil,
                                 titleSource: nil, lastActiveAt: utc("2026-10-14T09:30:00Z"), status: nil, resume: nil),
            usage: usage, id: "grok-bot|conv-1")
        let index = CalendarSessionIndex(rows: [merged])
        let ref = ActivityBlock.SessionRef(sessionId: "conv-1", start: utc("2026-10-14T09:00:00Z"),
                                           end: utc("2026-10-14T09:05:00Z"), responses: 1)
        #expect(index.row(for: ref, harness: "grok-bot", accountHome: "/first").usage == usage)
        // A day bucket is one account's, so another home's block doesn't match it.
        var day = merged
        day.session.id = "cursor-2026-10-14"
        day.session.harness = "cursor"
        day.usage?.harness = "cursor"
        day.usage?.sessionId = "cursor-2026-10-14"
        let buckets = CalendarSessionIndex(rows: [day])
        let dayRef = ActivityBlock.SessionRef(sessionId: "cursor-2026-10-14", start: ref.start, end: ref.end, responses: 1)
        #expect(buckets.row(for: dayRef, harness: "cursor", accountHome: "/first").usage == nil)
        #expect(buckets.row(for: dayRef, harness: "cursor", accountHome: "/second").usage != nil)
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
    /// Returns once `count` reads have started. load() paints a cached
    /// range before it asks any provider, so this orders a check after
    /// the paint instead of yielding and hoping the load ran first.
    /// Cancellation (a test's time limit) resumes the wait too, so a read
    /// that never starts fails the test instead of hanging the suite.
    func started(_ count: Int = 1) async {
        let id = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                let now = state.withLock { s -> Bool in
                    if s.calls >= count || Task.isCancelled { return true }
                    s.arrivals.append((id, count, continuation))
                    return false
                }
                if now { continuation.resume() }
            }
        } onCancel: { [state] in
            state.withLock { s in
                s.arrivals.firstIndex { $0.id == id }.map { s.arrivals.remove(at: $0) }
            }?.continuation.resume()
        }
    }
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) async throws -> [ActivityBlock] {
        let arrived = state.withLock { s -> [CheckedContinuation<Void, Never>] in
            s.calls += 1
            let ready = s.arrivals.filter { s.calls >= $0.count }
            s.arrivals.removeAll { s.calls >= $0.count }
            return ready.map(\.continuation)
        }
        arrived.forEach { $0.resume() }
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
        var arrivals: [(id: UUID, count: Int,
                        continuation: CheckedContinuation<Void, Never>)] = []
        private let lock = NSLock()
        func withLock<T>(_ body: (State) -> T) -> T {
            lock.lock()
            defer { lock.unlock() }
            return body(self)
        }
    }
}

/// Returns once the model's prefetches and snapshot saves have finished,
/// or once the test is cancelled (its time limit), so a background task
/// that never ends fails the test instead of hanging the suite.
@MainActor
private func settled(_ model: CalendarModel) async {
    let gate = OnceGate()
    await withTaskCancellationHandler {
        await withCheckedContinuation { continuation in
            gate.arm(continuation)
            Task { @MainActor in
                await model.backgroundWork()
                gate.open()
            }
        }
    } onCancel: {
        gate.open()
    }
}

/// A continuation resumed exactly once, by whichever of `open()` and
/// `arm(_:)` comes second.
private final class OnceGate: @unchecked Sendable {
    private var continuation: CheckedContinuation<Void, Never>?
    private var opened = false
    private let lock = NSLock()
    func arm(_ continuation: CheckedContinuation<Void, Never>) {
        lock.lock()
        if opened {
            lock.unlock()
            continuation.resume()
            return
        }
        self.continuation = continuation
        lock.unlock()
    }
    func open() {
        lock.lock()
        opened = true
        let waiting = continuation
        continuation = nil
        lock.unlock()
        waiting?.resume()
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
        let defaults = TestDefaults("CalendarCacheTests")
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

    // The time limit fails the test instead of hanging the suite if the
    // load never asks the provider.
    @Test(.timeLimit(.minutes(1)))
    func aRestoredSnapshotPaintsBeforeTheLoadLands() async {
        // Snapshot entries are keyed by render range: the visible week
        // Oct 12–19 plus one window of buffer on each side.
        let snap = CalendarSnapshot(ranges: [
            .init(start: utc("2026-10-05T00:00:00Z"), end: utc("2026-10-26T00:00:00Z"),
                  providers: [0: [block("2026-10-14T09:00:00Z")]])])
        let held = HeldActivity(blocks: [])
        let model = CalendarModel(providers: [held], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") },
                                  calendar: calendar, restored: snap)
        async let load: () = model.load()
        await held.started()
        // The cached range's blocks are on screen while the provider's
        // read is still suspended.
        #expect(model.blocks.count == 1)
        held.release()
        await load
        #expect(model.blocks.isEmpty)
    }

    // Each time limit below fails the test instead of hanging the suite
    // if a prefetch or snapshot save never finishes.
    @Test(.timeLimit(.minutes(1)))
    func prefetchFillsNeighbouringRangesWithoutPublishing() async {
        let provider = FakeActivity(blocks: [block("2026-10-05T10:00:00Z", session: "prev"),
                                             block("2026-10-14T10:00:00Z", session: "now"),
                                             block("2026-10-21T10:00:00Z", session: "next")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load()
        // The render range buffers a window either side, so the buffer
        // days' blocks are published too; the window clip is separate.
        #expect(model.blocks.map(\.project) == ["prev", "now", "next"])
        #expect(model.windowBlocks.map(\.project) == ["now"])
        // Wait for the utility-priority prefetch tasks to land.
        await settled(model)
        // Moving back a week publishes from the cache: the blocks appear
        // before the provider's load could run again.
        model.previous()
        #expect(model.windowBlocks.map(\.project) == ["prev"])
        #expect(model.blocks.map(\.project) == ["prev", "now"])
    }

    // The time limit fails the test instead of hanging the suite if the
    // prefetches never settle or the refetch never asks the provider.
    @Test(.timeLimit(.minutes(1)))
    func aCompletedScanKeepsStaleBlocksUntilTheRefetchLands() async {
        let held = HeldActivity(blocks: [block("2026-10-14T09:00:00Z")])
        held.held = false
        let model = CalendarModel(providers: [held], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load()
        #expect(model.blocks.count == 1)
        // The prefetches land first, so the next read is the refetch.
        await settled(model)
        let calls = held.calls
        // Next load hangs; the stale range stays visible meanwhile.
        held.held = true
        model.noteScanCompleted()
        async let load: () = model.load(force: true)
        await held.started(calls + 1)
        #expect(model.blocks.count == 1)
        held.release()
        await load
        #expect(held.calls >= 2)
    }

    @Test(.timeLimit(.minutes(1)))
    func theSnapshotPersistsTheRecentRanges() async throws {
        let spy = SnapSpy()
        let provider = FakeActivity(blocks: [block("2026-10-14T09:00:00Z")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") },
                                  calendar: calendar, snapshots: spy)
        await model.load()
        await settled(model)
        let saved = try #require(spy.saved.last)
        // The saved range is the render range, not the visible one.
        #expect(saved.ranges.contains {
            $0.start == utc("2026-10-05T00:00:00Z") && $0.end == utc("2026-10-26T00:00:00Z")
        })
        // A new model hydrates from it, before any load.
        let restored = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                     now: { utc("2026-10-14T12:00:00Z") },
                                     calendar: calendar, restored: saved)
        #expect(restored.blocks.count == 1)
        await restored.load()
    }

    // The time limit fails the test instead of hanging the suite if
    // load() ever stops asking the provider.
    @Test(.timeLimit(.minutes(1)))
    func aTwentyEightDayRangeCachesAndPublishes() async {
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
        // One yield does not promise the async-let child has reached the
        // main actor yet; under load it often has not. Wait for the held
        // read instead: the paint comes first, and the read never answers.
        await held.started()
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

    @Test(.timeLimit(.minutes(1)))
    func aLatePrefetchDoesNotOverwriteFresherBlocks() async {
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
        await settled(model)
        // Step away and back: the paint comes from the cache alone, so a
        // forced read cannot hide a prefetch that overwrote it.
        model.previous()
        model.next()
        #expect(model.blocks.map(\.project) == ["fresh"])
    }
}

extension CalendarCacheTests {
    @Test(.timeLimit(.minutes(1)))
    func aFailingPrefetchKeepsTheProvidersCachedBlocks() async {
        let flag = Box(false)
        let good = FakeActivity(blocks: [block("2026-10-14T09:00:00Z", session: "a")])
        let flaky = TogglingActivity(blocks: [block("2026-10-14T10:00:00Z",
                                                    session: "b")], fails: flag)
        let model = CalendarModel(providers: [good, flaky], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        await model.load()
        await settled(model)  // let prefetches land
        #expect(model.blocks.count == 2)
        // Every cached range goes stale; the flaky provider now fails.
        flag.value = true
        model.noteScanCompleted()
        model.next()
        await model.load(force: true)
        // Wait for the stale-range prefetch (back on the October 12 week)
        // to finish with the flaky provider failing.
        await settled(model)
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
            .init(start: utc("2026-10-05T00:00:00Z"), end: utc("2026-10-26T00:00:00Z"),
                  providers: [0: [block("2026-10-14T09:00:00Z")]])])
        let model = CalendarModel(providers: [], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") },
                                  calendar: calendar, restored: snap)
        #expect(model.blocks.count == 1)
    }
}

/// Records every range a provider is asked for.
private actor RecordingActivity: ActivityProviding {
    private(set) var ranges: [DateInterval] = []
    let blocks: [ActivityBlock]
    init(blocks: [ActivityBlock] = []) { self.blocks = blocks }
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) throws -> [ActivityBlock] {
        ranges.append(range)
        return blocks.filter { $0.end >= range.start && $0.start < range.end }
    }
}

/// Answers its first read; every later read waits for `release()` and
/// then fails, so a test can hold a prefetch and fail it later.
private final class LaterReadsFail: ActivityProviding, @unchecked Sendable {
    struct Failure: LocalizedError {
        var errorDescription: String? { "prefetch failed" }
    }
    private let lock = NSLock()
    private var calls = 0
    private var open = false
    private var held: [CheckedContinuation<Void, Never>] = []
    private var arrivals: [(count: Int, continuation: CheckedContinuation<Void, Never>)] = []

    func release() {
        let waiting = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            open = true
            defer { held = [] }
            return held
        }
        waiting.forEach { $0.resume() }
    }

    /// Returns once `count` reads have started.
    func started(_ count: Int) async {
        await withCheckedContinuation { continuation in
            let now = lock.withLock { () -> Bool in
                if calls >= count { return true }
                arrivals.append((count, continuation))
                return false
            }
            if now { continuation.resume() }
        }
    }

    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) async throws -> [ActivityBlock] {
        let (n, ready) = lock.withLock { () -> (Int, [CheckedContinuation<Void, Never>]) in
            calls += 1
            let ready = arrivals.filter { calls >= $0.count }.map(\.continuation)
            arrivals.removeAll { calls >= $0.count }
            return (calls, ready)
        }
        ready.forEach { $0.resume() }
        if n == 1 { return [] }
        await withCheckedContinuation { continuation in
            let wait = lock.withLock { () -> Bool in
                if open { return false }
                held.append(continuation)
                return true
            }
            if !wait { continuation.resume() }
        }
        throw Failure()
    }
}

@MainActor
@Suite struct CalendarSessionCacheTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        c.firstWeekday = 2
        return c
    }

    private func freshDefaults() -> UserDefaults {
        let defaults = TestDefaults("CalendarSessionCacheTests")
        return defaults
    }

    private func block(_ start: String, hours: Double = 1, session: String = "s",
                       responses: Int = 1) -> ActivityBlock {
        let start = utc(start)
        var block = ActivityBlock(harness: "claude", accountHome: "/h", sessionId: session,
                                  start: start, end: start.addingTimeInterval(hours * 3600),
                                  responses: responses)
        block.project = session
        return block
    }

    private func widened(_ start: String, _ end: String) -> DateInterval {
        DateInterval(start: utc(start).addingTimeInterval(-CalendarModel.edgeLookout),
                     end: utc(end).addingTimeInterval(CalendarModel.edgeLookout))
    }

    @Test(.timeLimit(.minutes(1)))
    func aLandingOnCachedDaysAsksNoProvider() async {
        let provider = RecordingActivity(blocks: [block("2026-10-16T09:00:00Z", session: "a"),
                                                  block("2026-10-25T09:00:00Z", session: "b")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .days(9)
        await model.load()
        await settled(model) // the neighbour prefetch reaches 9 days past each edge
        let asked = await provider.ranges.count
        // A swipe of three days: every day of the new render range is
        // cached, so the blocks are up at once and nothing is read.
        model.land(on: utc("2026-10-17T00:00:00Z"))
        #expect(model.blocks.map(\.project) == ["a", "b"])
        await model.load()
        #expect(await provider.ranges.count == asked)
    }

    @Test(.timeLimit(.minutes(1)))
    func aLoadReadsOnlyTheDaysItLacks() async {
        let provider = RecordingActivity()
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .days(3) // render range Oct 11 to Oct 20
        await model.load()
        await settled(model) // prefetched Oct 8 to 10 and Oct 20 to 22
        let asked = await provider.ranges.count
        // Render range Oct 17 to Oct 26: only Oct 23 to 25 is new.
        model.go(to: utc("2026-10-20T12:00:00Z"))
        await model.load()
        let ranges = await provider.ranges
        #expect(ranges[asked] == widened("2026-10-23T00:00:00Z", "2026-10-26T00:00:00Z"))
    }

    @Test(.timeLimit(.minutes(1)))
    func aDayIsReadAgainOnceItOutlivesItsLifetime() async {
        final class Clock: @unchecked Sendable { var now = utc("2026-10-14T12:00:00Z") }
        let clock = Clock()
        let provider = RecordingActivity()
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { clock.now }, calendar: calendar)
        model.mode = .day
        await model.load()
        await settled(model)
        let asked = await provider.ranges.count
        await model.load()
        #expect(await provider.ranges.count == asked) // still fresh
        clock.now = clock.now.addingTimeInterval(CalendarModel.dayLifetime + 1)
        await model.load()
        #expect(await provider.ranges.count > asked)
    }

    @Test(.timeLimit(.minutes(1)))
    func aLandingRedrawsOnlyTheDaysWhoseBlocksChanged() async {
        let provider = RecordingActivity(blocks: [block("2026-10-14T09:00:00Z", session: "a"),
                                                  block("2026-10-20T09:00:00Z", session: "b")])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .days(3) // render range Oct 11 to Oct 19
        await model.load()
        await settled(model)
        let oct14 = DateInterval(start: utc("2026-10-14T00:00:00Z"), duration: 86400)
        let slice = model.blocks(on: oct14)
        #expect(slice.blocks.map(\.project) == ["a"])
        let notified = Box(false)
        withObservationTracking { _ = slice.blocks } onChange: { notified.value = true }
        // Render range Oct 12 to Oct 20 brings Oct 20's block in: the
        // published list changes, Oct 14's slice does not.
        model.land(on: utc("2026-10-15T00:00:00Z"))
        #expect(model.blocks.map(\.project) == ["a", "b"])
        #expect(!notified.value)
        #expect(model.blocks(on: oct14) === slice)
    }

    @Test(.timeLimit(.minutes(1)))
    func aFailedPrefetchTheLoadWaitedForSetsTheError() async {
        let provider = LaterReadsFail()
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .day // render range Oct 13 to Oct 15
        await model.load()
        #expect(model.loadError == nil)
        // Both neighbour prefetches (Oct 12, Oct 16) are reading, held.
        await provider.started(3)
        // Oct 14 to Oct 16: only Oct 16 is new, and a prefetch already
        // reads it, so the load waits for that read instead of its own.
        model.land(on: utc("2026-10-15T00:00:00Z"))
        async let load: () = model.load()
        provider.release()
        await load
        #expect(model.loadError == "prefetch failed")
    }

    @Test(.timeLimit(.minutes(1)))
    func missingDaysOnBothSidesAreReadAsTwoRuns() async {
        let provider = RecordingActivity()
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .day
        await model.load()
        await settled(model) // Oct 12 to Oct 16 are cached
        let asked = await provider.ranges.count
        model.mode = .week // render range Oct 5 to Oct 25
        await model.load()
        let reads = Array(await provider.ranges.dropFirst(asked))
        // Oct 5 to 11 and Oct 17 to 25, never the fresh days between.
        #expect(reads.contains(widened("2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z")))
        #expect(reads.contains(widened("2026-10-17T00:00:00Z", "2026-10-26T00:00:00Z")))
        #expect(!reads.contains { $0.contains(utc("2026-10-14T12:00:00Z")) })
    }

    @Test func piecesEachReachingPastTheOtherJoin() {
        // Two reads cut one all-day block at different edges, each with
        // days the other lacks: neither may be dropped.
        let first = block("2026-10-10T00:00:00Z", hours: 240, session: "bot", responses: 100)
        let second = block("2026-10-19T00:00:00Z", hours: 96, session: "bot", responses: 20)
        let merged = CalendarModel.merged([first, second])
        #expect(merged.count == 1)
        #expect(merged.first?.start == utc("2026-10-10T00:00:00Z"))
        #expect(merged.first?.end == utc("2026-10-23T00:00:00Z"))
        #expect(merged.first?.sessions.map(\.sessionId) == ["bot"])
        #expect(merged.first?.sessions.first?.end == utc("2026-10-23T00:00:00Z"))
        #expect(merged.first?.responses == 100)
    }

    @Test func aBlockFiledUnderTwoDaysOrCutTwiceShowsOnce() {
        // Filed under both days it touches: the same block twice.
        let night = block("2026-10-14T23:00:00Z", hours: 2, session: "night")
        // One long block read twice, cut at different lookout edges: the
        // read holding more responses wins.
        let short = block("2026-10-16T00:00:00Z", hours: 20, session: "bot", responses: 40)
        let full = block("2026-10-15T20:00:00Z", hours: 30, session: "bot", responses: 70)
        // A separate block of the same project, after a gap, stays.
        let later = block("2026-10-17T12:00:00Z", session: "bot")
        let merged = CalendarModel.merged([night, short, night, full, later])
        #expect(merged == [full, night, later].sorted { $0.start < $1.start })
    }
}
