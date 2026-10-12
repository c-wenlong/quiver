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

@Suite struct HeatmapHoverTests {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        c.firstWeekday = 1
        return c
    }
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    /// The grid as the Overview draws it: a 26-week map with only the last
    /// ~15 columns on screen, cells 12pt, 3pt gaps, a 24pt day-label gutter.
    /// Every drawn cell must resolve to a bubble fully inside the grid.
    @Test func everyVisibleCellResolvesABubbleInsideTheGrid() throws {
        let cell: CGFloat = 12, gap: CGFloat = 3, gutter: CGFloat = 24
        let visible = 15
        let top = 30.0 // month labels and the spacer sit above row 0
        let map = Heatmap(days: [], weeks: 26, now: now, calendar: calendar)
        let weeks = Array(map.columns.suffix(visible))
        let cells = weeks.joined().compactMap { $0 }
        var frames: [Date: CGRect] = [:]
        for (week, column) in weeks.enumerated() {
            for (weekday, day) in column.enumerated() where day != nil {
                frames[day!.day] = CGRect(
                    x: gutter + gap + CGFloat(week) * (cell + gap),
                    y: top + CGFloat(weekday) * (cell + gap),
                    width: cell, height: cell)
            }
        }
        let grid = CGSize(width: gutter + gap + CGFloat(visible) * (cell + gap),
                          height: top + 7 * (cell + gap))
        let bubble = CGSize(width: 150, height: 40)
        #expect(!cells.isEmpty)
        for cell in cells {
            let resolved = HeatmapHover.resolve(
                day: cell.day, cells: cells, frames: frames,
                gridSize: grid, bubbleSize: bubble)
            let placed = try #require(resolved)
            #expect(placed.cell.day == cell.day)
            let rect = CGRect(x: placed.origin.x - bubble.width / 2,
                              y: placed.origin.y - bubble.height / 2,
                              width: bubble.width, height: bubble.height)
            #expect(rect.minX >= 0 && rect.maxX <= grid.width)
            #expect(rect.minY >= 0 && rect.maxY <= grid.height)
        }
    }

    /// A day with no drawn cell or no measured frame still resolves to nil:
    /// the overlay simply draws nothing.
    @Test func anUndrawnDayResolvesToNothing() {
        let map = Heatmap(days: [], weeks: 26, now: now, calendar: calendar)
        let weeks = map.columns.suffix(15)
        let cells = weeks.joined().compactMap { $0 }
        let first = map.columns[0][0]!.day
        let resolved = HeatmapHover.resolve(
            day: first, cells: cells, frames: [:],
            gridSize: CGSize(width: 300, height: 140),
            bubbleSize: CGSize(width: 150, height: 40))
        #expect(resolved?.cell == nil && resolved?.origin == nil)
    }
}
