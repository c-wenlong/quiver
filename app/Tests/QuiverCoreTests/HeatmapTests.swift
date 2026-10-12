import Foundation
import Testing
@testable import QuiverCore

@Suite struct HeatmapTests {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        c.firstWeekday = 1
        return c
    }
    // Sunday 2026-10-11, 15:30 SGT.
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    func day(_ offset: Int) -> Date {
        calendar.date(byAdding: .day, value: offset, to: calendar.startOfDay(for: now))!
    }

    @Test func columnsAreWholeWeeksEndingThisWeek() {
        let map = Heatmap(days: [], weeks: 26, now: now, calendar: calendar)
        #expect(map.columns.count == 26)
        #expect(map.columns.allSatisfy { $0.count == 7 })
        // This week has only today so far; later days are absent.
        #expect(map.columns.last?.first??.day == day(0))
        #expect(map.columns.last?.dropFirst().allSatisfy { $0 == nil } == true)
        #expect(map.columns.first?.first??.day == day(-25 * 7))
    }

    @Test func levelsSplitBusyDaysIntoFourSteps() {
        let rows = (1...8).map { DayRow(day: day(-$0), totals: totals(tokens: $0 * 100, cost: 0)) }
        let map = Heatmap(days: rows, weeks: 4, now: now, calendar: calendar)
        let cells = map.columns.flatMap { $0 }.compactMap { $0 }
        #expect(cells.first { $0.day == day(-1) }?.level == 1)
        #expect(cells.first { $0.day == day(-8) }?.level == 4)
        #expect(cells.first { $0.day == day(-10) }?.level == 0)
        #expect(cells.first { $0.day == day(0) }?.level == 0)
    }

    @Test func aZeroCostDayIsNotUnpriced() {
        // Priced tokens that costed to nothing still differ from unpriced ones.
        let rows = [DayRow(day: day(-1), totals: totals(tokens: 5, cost: 0))]
        let cell = Heatmap(days: rows, weeks: 4, now: now, calendar: calendar)
            .columns.joined().compactMap { $0 }.first { $0.day == day(-1) }
        #expect(cell?.costUSD == 0)
        #expect(cell?.unpricedTokens == 0)
    }

    @Test func unpricedTokensRideAlongWithTheCost() {
        let rows = [DayRow(day: day(-1), totals: totals(tokens: 5, cost: 1, unpriced: 3)),
                    DayRow(day: day(-1), totals: totals(tokens: 2, cost: 0, unpriced: 2))]
        let cell = Heatmap(days: rows, weeks: 4, now: now, calendar: calendar)
            .columns.joined().compactMap { $0 }.first { $0.day == day(-1) }
        #expect(cell?.costUSD == 1)
        #expect(cell?.unpricedTokens == 5)
    }

    @Test func totalsCountOnlyTheDaysTheGridShows() {
        // A narrow widget draws fewer weeks; its caption must not count the rest.
        let rows = [DayRow(day: day(-1), totals: totals(tokens: 5, cost: 0)),
                    DayRow(day: day(-100), totals: totals(tokens: 9, cost: 0))]
        let narrow = Heatmap(days: rows, weeks: 4, now: now, calendar: calendar)
        #expect(narrow.totalTokens == 5)
        #expect(narrow.activeDays == 1)
        let wide = Heatmap(days: rows, weeks: 26, now: now, calendar: calendar)
        #expect(wide.totalTokens == 14)
        #expect(wide.activeDays == 2)
    }
}
