import Foundation
import Testing
@testable import QuiverCore

private struct FakeActivity: ActivityProviding {
    var blocks: [ActivityBlock]
    /// Like the ledger, only blocks overlapping the range come back.
    func activityBlocks(in range: DateInterval, gap: TimeInterval) throws -> [ActivityBlock] {
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
    func activityBlocks(in range: DateInterval, gap: TimeInterval) throws -> [ActivityBlock] {
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
                       defaults: UserDefaults? = nil) -> CalendarModel {
        var cal = calendar
        cal.firstWeekday = firstWeekday
        return CalendarModel(providers: [FakeActivity(blocks: blocks)],
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
                       session: String = "s") -> ActivityBlock {
        let start = utc(start)
        return ActivityBlock(harness: harness, accountHome: home, sessionId: session,
                             start: start, end: start.addingTimeInterval(TimeInterval(minutes * 60)),
                             responses: 1)
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

    @Test func theSameSessionIdInTwoAccountsResolvesEachBlock() {
        let index = CalendarSessionIndex(rows: [row(home: "/h"), row(home: "/work")])
        #expect(index.row(for: block(home: "/h")).usage?.accountHome == "/h")
        #expect(index.row(for: block(home: "/work")).usage?.accountHome == "/work")
    }

    @Test func aRowWithoutUsageIsTheFallbackOnly() {
        let index = CalendarSessionIndex(rows: [row(home: nil)])
        // No account-specific row: the fallback answers.
        #expect(index.row(for: block(home: "/h")).session.title == "Session default")
        // An account-specific row wins over the fallback.
        let both = CalendarSessionIndex(rows: [row(home: nil), row(home: "/work")])
        #expect(both.row(for: block(home: "/work")).usage?.accountHome == "/work")
        #expect(both.row(for: block(home: "/h")).session.title == "Session default")
    }

    @Test func anUnlistedSessionGetsAStandInRow() {
        let index = CalendarSessionIndex(rows: [])
        let row = index.row(for: ActivityBlock(
            harness: "claude", accountHome: "/h", sessionId: "gone",
            start: utc("2026-10-14T09:00:00Z"), end: utc("2026-10-14T09:10:00Z"),
            responses: 2, cwd: "/work/project"))
        #expect(row.session.id == "gone")
        #expect(row.session.cwd == "/work/project")
        #expect(row.session.resume == nil)
        #expect(row.usage == nil)
        #expect(row.id.contains("gone"))
    }
}
