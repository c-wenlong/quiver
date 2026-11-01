import Foundation
import Testing
@testable import SWECore

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

@Suite struct HeatmapLayoutTests {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        c.firstWeekday = 1
        return c
    }
    // Wednesday 2026-10-28, 12:00 SGT.
    let now = Date(timeIntervalSince1970: 1_793_160_000)
    let layout = HeatmapLayout(cell: 12, gap: 3, gutter: 24, origin: CGPoint(x: 0, y: 19))

    /// The Overview's window: the last 15 of 26 weeks, July to October.
    var columns: [[Heatmap.Cell?]] {
        Array(Heatmap(days: [], weeks: 26, now: now, calendar: calendar).columns.suffix(15))
    }

    /// The bug: only the 8 weeks of the first layout (Sep 6 on) had frames,
    /// so July and August cells drew no bubble. Every drawn cell, the
    /// leftmost columns included, now gets a frame and a bubble.
    @Test func everyDrawnCellHasAFrameAndABubble() throws {
        let columns = columns
        let cells = columns.joined().compactMap { $0 }
        let frames = layout.frames(columns: columns)
        #expect(frames.count == cells.count)
        let july = try #require(columns[0][0]?.day)
        #expect(calendar.component(.month, from: july) == 7)
        let grid = CGSize(width: 24 + 3 + 15 * 15, height: 19 + 7 * 15)
        for cell in cells {
            let resolved = HeatmapHover.resolve(
                day: cell.day, cells: cells, frames: frames,
                gridSize: grid, bubbleSize: CGSize(width: 150, height: 40))
            #expect(resolved?.cell.day == cell.day)
        }
    }

    /// Frames step 15pt a column and a row, after the label gutter and gap.
    @Test func framesFollowTheGridMetrics() {
        #expect(layout.frame(week: 0, weekday: 0) == CGRect(x: 27, y: 19, width: 12, height: 12))
        #expect(layout.frame(week: 14, weekday: 6) == CGRect(x: 237, y: 109, width: 12, height: 12))
    }

    /// The pointer over a cell's middle finds that cell; over a gap, the
    /// day labels, an undrawn future day or past the last column, nothing.
    @Test func cellAtAPointRoundTripsAndSkipsGaps() throws {
        let columns = columns
        for (week, column) in columns.enumerated() {
            for (weekday, cell) in column.enumerated() {
                let frame = layout.frame(week: week, weekday: weekday)
                let hit = layout.cell(at: CGPoint(x: frame.midX, y: frame.midY), columns: columns)
                #expect(hit?.day == cell?.day)
            }
        }
        let first = layout.frame(week: 0, weekday: 0)
        #expect(layout.cell(at: CGPoint(x: first.maxX + 1, y: first.midY), columns: columns) == nil)
        #expect(layout.cell(at: CGPoint(x: 10, y: first.midY), columns: columns) == nil)
        #expect(layout.cell(at: CGPoint(x: first.midX, y: 5), columns: columns) == nil)
        let past = layout.frame(week: 15, weekday: 0)
        #expect(layout.cell(at: CGPoint(x: past.midX, y: past.midY), columns: columns) == nil)
        // This week's Saturday is still ahead: drawn as blank, never hovered.
        #expect(columns[14][6] == nil)
        let saturday = layout.frame(week: 14, weekday: 6)
        #expect(layout.cell(at: CGPoint(x: saturday.midX, y: saturday.midY), columns: columns) == nil)
    }
}
