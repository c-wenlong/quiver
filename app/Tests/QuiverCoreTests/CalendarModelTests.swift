import Foundation
import Testing
@testable import QuiverCore

private struct FakeActivity: ActivityProviding {
    var blocks: [ActivityBlock]
    /// Like the ledger, only blocks overlapping the range come back.
    func activityBlocks(in range: DateInterval, gap: TimeInterval) -> [ActivityBlock] {
        blocks.filter { $0.end >= range.start && $0.start < range.end }
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
