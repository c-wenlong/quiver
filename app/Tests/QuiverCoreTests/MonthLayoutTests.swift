import Foundation
import Testing
@testable import QuiverCore

@Suite struct MonthLayoutTests {
    private func calendar(_ tz: String = "UTC", firstWeekday: Int) -> Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: tz)!
        c.firstWeekday = firstWeekday
        return c
    }

    private func block(_ start: String, minutes: Int = 60,
                       harness: String = "claude", home: String = "/h",
                       session: String = "s") -> ActivityBlock {
        let start = utc(start)
        return ActivityBlock(harness: harness, accountHome: home, sessionId: session,
                             start: start, end: start.addingTimeInterval(TimeInterval(minutes * 60)),
                             responses: 1)
    }

    @Test func octoberOnAMondayFirstCalendarStartsSeptember28() {
        let weeks = MonthLayout.weeks(for: utc("2026-10-15T12:00:00Z"),
                                      calendar: calendar(firstWeekday: 2))
        #expect(weeks.count == 6)
        #expect(weeks.allSatisfy { $0.count == 7 })
        #expect(weeks[0][0] == utc("2026-09-28T00:00:00Z"))
        #expect(weeks[5][6] == utc("2026-11-08T00:00:00Z"))
    }

    @Test func octoberOnASundayFirstCalendarStartsSeptember27() {
        let weeks = MonthLayout.weeks(for: utc("2026-10-15T12:00:00Z"),
                                      calendar: calendar(firstWeekday: 1))
        #expect(weeks[0][0] == utc("2026-09-27T00:00:00Z"))
    }

    @Test func twoBlocksOfOneSessionJoinOneEntry() {
        let day = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                               end: utc("2026-10-15T00:00:00Z"))
        let entries = MonthLayout.entries(
            [block("2026-10-14T09:00:00Z"), block("2026-10-14T14:00:00Z")], day: day)
        #expect(entries.count == 1)
        #expect(entries[0].blockCount == 2)
        #expect(entries[0].active == 7200)
        #expect(entries[0].start == utc("2026-10-14T09:00:00Z"))
    }

    @Test func aBlockAcrossMidnightContributesToBothDaysClipped() {
        let overnight = block("2026-10-14T23:00:00Z", minutes: 120)
        let day1 = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                                end: utc("2026-10-15T00:00:00Z"))
        let day2 = DateInterval(start: utc("2026-10-15T00:00:00Z"),
                                end: utc("2026-10-16T00:00:00Z"))
        let first = MonthLayout.entries([overnight], day: day1)
        let second = MonthLayout.entries([overnight], day: day2)
        #expect(first[0].active == 3600)
        #expect(second[0].active == 3600)
        #expect(second[0].start == day2.start)
    }

    @Test func sessionsSortByStartAndStayApart() {
        let day = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                               end: utc("2026-10-15T00:00:00Z"))
        let entries = MonthLayout.entries(
            [block("2026-10-14T14:00:00Z", session: "b"),
             block("2026-10-14T09:00:00Z", session: "a")], day: day)
        #expect(entries.map(\.block.sessionId) == ["a", "b"])
    }

    @Test func splitReservesALineForMore() {
        let day = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                               end: utc("2026-10-15T00:00:00Z"))
        let entries = MonthLayout.entries((0..<5).map {
            block("2026-10-14T0\($0):00:00Z", session: "s\($0)")
        }, day: day)
        let noOverflow = MonthLayout.split(entries, fitting: 5)
        #expect(noOverflow.shown.count == 5 && noOverflow.more == 0)
        let overflow = MonthLayout.split(entries, fitting: 4)
        #expect(overflow.shown.count == 3 && overflow.more == 2)
        let tight = MonthLayout.split(entries, fitting: 0)
        #expect(tight.shown.isEmpty && tight.more == 5)
    }
}

@MainActor
@Suite struct CalendarMonthModelTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        c.firstWeekday = 2
        return c
    }

    private func freshDefaults() -> UserDefaults {
        let name = "CalendarMonthTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    private func model(now: String = "2026-10-14T12:00:00Z") -> CalendarModel {
        CalendarModel(providers: [], defaults: freshDefaults(),
                      now: { utc(now) }, calendar: calendar)
    }

    @Test func theMonthRangeIsTheSixWeekGrid() {
        let model = self.model()
        model.mode = .month
        #expect(model.visibleRange.start == utc("2026-09-28T00:00:00Z"))
        #expect(model.visibleRange.end == utc("2026-11-09T00:00:00Z"))
        #expect(model.visibleRange.duration == 42 * 86400)
    }

    @Test func nextAndPreviousMoveByMonth() {
        let model = self.model()
        model.mode = .month
        model.next()
        #expect(model.anchor == utc("2026-11-14T12:00:00Z"))
        #expect(model.visibleRange.start == utc("2026-10-26T00:00:00Z"))
        model.previous(); model.previous()
        #expect(model.visibleRange.start == utc("2026-08-31T00:00:00Z"))
    }

    @Test func monthTotalsCountOnlyTheAnchorMonth() async {
        // A block wholly in the adjacent September days must not count.
        let provider = CalendarMonthFake(blocks: [
            ActivityBlock(harness: "claude", accountHome: "/h", sessionId: "sep",
                          start: utc("2026-09-29T09:00:00Z"),
                          end: utc("2026-09-29T10:00:00Z"), responses: 1),
            ActivityBlock(harness: "claude", accountHome: "/h", sessionId: "oct",
                          start: utc("2026-10-14T09:00:00Z"),
                          end: utc("2026-10-14T10:00:00Z"), responses: 1)])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .month
        await model.load()
        #expect(model.totals["claude"] == 3600)
        // In week mode on the September week the same blocks count fully.
        model.mode = .week
        model.go(to: utc("2026-09-29T12:00:00Z"))
        #expect(model.totals["claude"] == 3600)
    }

    @Test func threeDayRangeAndShift() {
        let model = self.model()
        model.mode = .days(3)
        #expect(model.visibleRange.start == utc("2026-10-14T00:00:00Z"))
        #expect(model.visibleRange.duration == 3 * 86400)
        model.next()
        #expect(model.anchor == utc("2026-10-17T12:00:00Z"))
        #expect(model.visibleRange.start == utc("2026-10-17T00:00:00Z"))
        model.previous()
        #expect(model.anchor == utc("2026-10-14T12:00:00Z"))
    }

    @Test func daysOneNormalisesToDayAndSevenIsNotTheWeek() {
        let model = self.model()
        model.mode = .days(1)
        #expect(model.mode == .day)
        // `.days(7)` starts on the anchor (a Wednesday), not Monday.
        model.mode = .days(7)
        #expect(model.visibleRange.start == utc("2026-10-14T00:00:00Z"))
        #expect(model.visibleRange.duration == 7 * 86400)
        #expect(model.mode.title == "7 days")
        // Out-of-range counts clamp.
        model.mode = .days(12)
        #expect(model.mode == .days(9))
    }

    @Test func neighbourPrefetchLandsOnTheNextMonthAndNextNdaySpan() async throws {
        final class Calls: ActivityProviding, @unchecked Sendable {
            var ranges: [DateInterval] = []
            func activityBlocks(in range: DateInterval, gap: TimeInterval,
                                force: Bool) async throws -> [ActivityBlock] {
                ranges.append(range)
                return []
            }
        }
        let provider = Calls()
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .month
        await model.load()
        try await Task.sleep(for: .milliseconds(300))
        let expectedNext = model.range(for: model.shiftedAnchor(1, mode: .month), mode: .month)
        #expect(provider.ranges.contains(expectedNext))
        #expect(expectedNext == DateInterval(start: utc("2026-10-26T00:00:00Z"),
                                           end: utc("2026-12-07T00:00:00Z")))
        // The same for a 3-day span.
        provider.ranges.removeAll()
        model.mode = .days(3)
        await model.load()
        try await Task.sleep(for: .milliseconds(300))
        #expect(provider.ranges.contains(
            model.range(for: model.shiftedAnchor(1, mode: .days(3)), mode: .days(3))))
    }

    @Test func keyCommandsMapLettersDigitsAndArrows() {
        #expect(CalendarModel.KeyCommand(character: "d") == .mode(.day))
        #expect(CalendarModel.KeyCommand(character: "w") == .mode(.week))
        #expect(CalendarModel.KeyCommand(character: "m") == .mode(.month))
        #expect(CalendarModel.KeyCommand(character: "t") == .today)
        #expect(CalendarModel.KeyCommand(character: "3") == .mode(.days(3)))
        #expect(CalendarModel.KeyCommand(character: "9") == .mode(.days(9)))
        #expect(CalendarModel.KeyCommand(character: "x") == nil)
        #expect(CalendarModel.KeyCommand(arrow: .left) == .previous)
        #expect(CalendarModel.KeyCommand(arrow: .right) == .next)
        // apply() routes them: "1" lands on Day.
        let model = self.model()
        model.apply(CalendarModel.KeyCommand(character: "1")!)
        #expect(model.mode == .day)
        model.apply(.init(arrow: .right))
        #expect(model.anchor == utc("2026-10-15T12:00:00Z"))
        model.apply(.today)
        #expect(model.anchor == utc("2026-10-14T12:00:00Z"))
    }
}

/// Same filter-by-overlap fake the other calendar tests use.
private struct CalendarMonthFake: ActivityProviding {
    var blocks: [ActivityBlock]
    func activityBlocks(in range: DateInterval, gap: TimeInterval,
                        force: Bool) throws -> [ActivityBlock] {
        blocks.filter { $0.end >= range.start && $0.start < range.end }
    }
}
