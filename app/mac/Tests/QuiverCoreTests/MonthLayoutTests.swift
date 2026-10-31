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
                       session: String = "s", project: String? = nil) -> ActivityBlock {
        let start = utc(start)
        var block = ActivityBlock(harness: harness, accountHome: home, sessionId: session,
                                  start: start,
                                  end: start.addingTimeInterval(TimeInterval(minutes * 60)),
                                  responses: 1)
        block.project = project ?? session
        return block
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

    @Test func twoBlocksOfOneProjectJoinOneEntry() {
        let day = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                               end: utc("2026-10-15T00:00:00Z"))
        let entries = MonthLayout.entries(
            [block("2026-10-14T09:00:00Z"), block("2026-10-14T14:00:00Z")], day: day)
        #expect(entries.count == 1)
        #expect(entries[0].blockCount == 2)
        #expect(entries[0].active == 7200)
        #expect(entries[0].start == utc("2026-10-14T09:00:00Z"))
    }

    @Test func anEntryKeepsEverySessionOfTheDay() {
        // Two blocks of one project from different sessions: the entry's
        // block carries both refs so its click opens the project sheet,
        // and a session with two blocks keeps one clipped ref.
        let day = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                               end: utc("2026-10-15T00:00:00Z"))
        let entries = MonthLayout.entries(
            [block("2026-10-14T09:00:00Z", session: "a", project: "repo"),
             block("2026-10-14T14:00:00Z", session: "b", project: "repo"),
             block("2026-10-14T16:00:00Z", session: "a", project: "repo")],
            day: day)
        #expect(entries.count == 1)
        #expect(entries[0].block.sessions.map(\.sessionId) == ["a", "b"])
        #expect(entries[0].block.sessions[0].start == utc("2026-10-14T09:00:00Z"))
        #expect(entries[0].block.sessions[0].end == utc("2026-10-14T17:00:00Z"))
        #expect(entries[0].block.sessions[0].responses == 2)
    }

    @Test func aSessionFromTheOtherDayStaysOutOfTheEntry() {
        // One block spanning midnight holds a Sunday-only session and a
        // Monday-only one: Monday's entry must not list Sunday's.
        let day = DateInterval(start: utc("2026-10-19T00:00:00Z"),
                               end: utc("2026-10-20T00:00:00Z"))
        var spanning = block("2026-10-18T23:50:00Z", minutes: 20, session: "a",
                             project: "repo")
        spanning.sessions = [
            .init(sessionId: "sun", start: utc("2026-10-18T23:50:00Z"),
                  end: utc("2026-10-18T23:55:00Z"), responses: 1),
            .init(sessionId: "mon", start: utc("2026-10-19T00:05:00Z"),
                  end: utc("2026-10-19T00:10:00Z"), responses: 1),
        ]
        let entries = MonthLayout.entries([spanning], day: day)
        #expect(entries.count == 1)
        #expect(entries[0].block.sessions.map(\.sessionId) == ["mon"])
        #expect(entries[0].block.sessions[0].start <= entries[0].block.sessions[0].end)
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

    @Test func projectsSortByStartAndStayApart() {
        let day = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                               end: utc("2026-10-15T00:00:00Z"))
        let entries = MonthLayout.entries(
            [block("2026-10-14T14:00:00Z", session: "b"),
             block("2026-10-14T09:00:00Z", session: "a")], day: day)
        #expect(entries.map(\.block.project) == ["a", "b"])
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
        let defaults = TestDefaults("CalendarMonthTests")
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
        let provider = FakeActivity(blocks: [
            ActivityBlock(harness: "claude", accountHome: "/h", sessionId: "sep",
                          start: utc("2026-09-29T09:00:00Z"),
                          end: utc("2026-09-29T11:00:00Z"), responses: 1),
            ActivityBlock(harness: "claude", accountHome: "/h", sessionId: "oct",
                          start: utc("2026-10-14T09:00:00Z"),
                          end: utc("2026-10-14T10:00:00Z"), responses: 1)])
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .month
        await model.load()
        #expect(model.totals["claude"] == 3600)
        // In week mode on the September week the two-hour block counts fully
        // and the October one drops out.
        model.mode = .week
        model.go(to: utc("2026-09-29T12:00:00Z"))
        #expect(model.totals["claude"] == 7200)
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
        actor Calls: ActivityProviding {
            private(set) var ranges: [DateInterval] = []
            /// Counts every call, prefetches included, which run as tasks
            /// of their own after `load` returns.
            private let calls = CountGate()
            func activityBlocks(in range: DateInterval, gap: TimeInterval,
                                force: Bool) throws -> [ActivityBlock] {
                ranges.append(range)
                let calls = self.calls
                Task { await calls.bump() }
                return []
            }
            /// The recorded ranges once at least `count` calls have run
            /// in all, or what has run by `gateTimeout`.
            func wait(for count: Int) async -> [DateInterval] {
                _ = await calls.reach(count)
                return ranges
            }
            func reset() { ranges = [] }
        }
        let provider = Calls()
        let model = CalendarModel(providers: [provider], defaults: freshDefaults(),
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .month
        await model.load()
        // The load plus both neighbour prefetches make three calls.
        var ranges = await provider.wait(for: 3)
        let expectedNext = model.range(for: model.shiftedAnchor(1, mode: .month), mode: .month)
        #expect(expectedNext == DateInterval(start: utc("2026-10-26T00:00:00Z"),
                                           end: utc("2026-12-07T00:00:00Z")))
        // October's six weeks already cover Oct 26 to Nov 8, so the
        // prefetch reads only the days after them, widened by a day each
        // side for edge-spanning blocks.
        #expect(ranges.contains(DateInterval(
            start: model.visibleRange.end.addingTimeInterval(-CalendarModel.edgeLookout),
            end: expectedNext.end.addingTimeInterval(CalendarModel.edgeLookout))))
        // The same for a 3-day span, on a model with nothing cached.
        let days = Calls()
        let threeDay = CalendarModel(providers: [days], defaults: freshDefaults(),
                                     now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        threeDay.mode = .days(3)
        await threeDay.load()
        ranges = await days.wait(for: 3)
        // The next span's render range reaches 3 days past this one's;
        // only those days are read.
        let next = threeDay.renderRange(for: threeDay.shiftedAnchor(1, mode: .days(3)),
                                        mode: .days(3))
        #expect(ranges.contains(DateInterval(
            start: threeDay.renderRange.end.addingTimeInterval(-CalendarModel.edgeLookout),
            end: next.end.addingTimeInterval(CalendarModel.edgeLookout))))
    }

    @Test func keyCommandsMapLettersDigitsAndArrows() {
        #expect(CalendarModel.KeyCommand(character: "d") == .mode(.day))
        #expect(CalendarModel.KeyCommand(character: "w") == .mode(.week))
        #expect(CalendarModel.KeyCommand(character: "m") == .mode(.month))
        #expect(CalendarModel.KeyCommand(character: "t") == .today)
        #expect(CalendarModel.KeyCommand(character: "s") == .togglePanel)
        #expect(CalendarModel.KeyCommand(character: "S") == .togglePanel)
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

    @Test func theSKeyTogglesTheSidePanel() {
        let model = self.model()
        #expect(!model.panelCollapsed)
        model.apply(CalendarModel.KeyCommand(character: "s")!)
        #expect(model.panelCollapsed)
        model.apply(.togglePanel)
        #expect(!model.panelCollapsed)
    }
}

extension CalendarMonthModelTests {
    @Test func theModePersistsAcrossModels() {
        let defaults = TestDefaults("CalendarModePersist")
        var model = CalendarModel(providers: [], defaults: defaults,
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        model.mode = .days(3)
        model = CalendarModel(providers: [], defaults: defaults,
                              now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        #expect(model.mode == .days(3))
        model.mode = .month
        #expect(CalendarModel.decodedMode(defaults.string(forKey: "calendar.mode")) == .month)
        // An unreadable value falls back to the week.
        #expect(CalendarModel.decodedMode("fortnight") == .week)
        #expect(CalendarModel.decodedMode("days:x") == .week)
        #expect(CalendarModel.decodedMode("days:0") == .week)
        #expect(CalendarModel.decodedMode(nil) == .week)
        // Round-trips.
        for mode in [CalendarModel.Mode.day, .week, .month, .days(4)] {
            #expect(CalendarModel.decodedMode(CalendarModel.encodedMode(mode)) == mode)
        }
    }

    @Test func thePanelCollapsePersistsAcrossModels() {
        let defaults = TestDefaults("CalendarPanelPersist")
        var model = CalendarModel(providers: [], defaults: defaults,
                                  now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        #expect(!model.panelCollapsed)
        model.panelCollapsed = true
        model = CalendarModel(providers: [], defaults: defaults,
                              now: { utc("2026-10-14T12:00:00Z") }, calendar: calendar)
        #expect(model.panelCollapsed)
        #expect(defaults.bool(forKey: "calendar.panelCollapsed"))
    }

    @Test func onlyAsciiDigitsPickADaySpan() {
        #expect(CalendarModel.KeyCommand(character: "0") == nil)
        #expect(CalendarModel.KeyCommand(character: "٣") == nil)
        #expect(CalendarModel.KeyCommand(character: "4") == .mode(.days(4)))
    }
}
