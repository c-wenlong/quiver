import Foundation
import Testing
@testable import SWECore

@Suite struct MiniMonthHighlightTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        c.firstWeekday = 2 // Monday
        return c
    }

    private func weeks(for anchor: Date) -> [[Date]] {
        MonthLayout.weeks(for: anchor, calendar: calendar)
    }

    /// Columns as plain triples so #expect can compare them.
    private func plain(_ segments: [(row: Int, firstColumn: Int, lastColumn: Int)])
        -> [[Int]] {
        segments.map { [$0.row, $0.firstColumn, $0.lastColumn] }
    }

    /// Inclusive day range → DateInterval ending at startOfDay(last+1).
    private func dayRange(_ first: String, _ last: String) -> DateInterval {
        let start = calendar.startOfDay(for: utc(first + "T00:00:00Z"))
        let end = calendar.date(byAdding: .day, value: 1,
                                to: calendar.startOfDay(for: utc(last + "T00:00:00Z")))!
        return DateInterval(start: start, end: end)
    }

    @Test func aSingleDayHighlightsOneCell() {
        let weeks = weeks(for: utc("2026-10-14T12:00:00Z"))
        let segments = MiniMonthHighlight.segments(weeks: weeks,
                                                   range: dayRange("2026-10-14", "2026-10-14"),
                                                   calendar: calendar)
        // Oct 14 2026 is a Wednesday: column 2 of the Oct 12 week row.
        let row = weeks.firstIndex { $0.contains {
            calendar.isDate($0, inSameDayAs: utc("2026-10-14T00:00:00Z"))
        } }!
        #expect(plain(segments) == [[row, 2, 2]])
    }

    @Test func aWeekHighlightsOneFullRow() {
        let weeks = weeks(for: utc("2026-10-14T12:00:00Z"))
        let segments = MiniMonthHighlight.segments(weeks: weeks,
                                                   range: dayRange("2026-10-12", "2026-10-18"),
                                                   calendar: calendar)
        let row = weeks.firstIndex { $0.contains {
            calendar.isDate($0, inSameDayAs: utc("2026-10-12T00:00:00Z"))
        } }!
        #expect(plain(segments) == [[row, 0, 6]])
    }

    @Test func sixDaysAcrossTwoRowsMakeTwoPills() {
        // The brief's case: Oct 8–13, Monday-first → Thu–Sun then Mon–Tue.
        let weeks = weeks(for: utc("2026-10-08T12:00:00Z"))
        let segments = MiniMonthHighlight.segments(weeks: weeks,
                                                   range: dayRange("2026-10-08", "2026-10-13"),
                                                   calendar: calendar)
        let first = weeks.firstIndex { $0.contains {
            calendar.isDate($0, inSameDayAs: utc("2026-10-08T00:00:00Z"))
        } }!
        #expect(plain(segments) == [[first, 3, 6], [first + 1, 0, 1]])
    }

    @Test func aRangeTouchingNoRowHighlightsNothing() {
        let weeks = weeks(for: utc("2026-10-14T12:00:00Z"))
        let segments = MiniMonthHighlight.segments(weeks: weeks,
                                                   range: dayRange("2026-12-01", "2026-12-07"),
                                                   calendar: calendar)
        #expect(plain(segments).isEmpty)
    }

    @Test func aRangeCrossingAMonthBoundarySplitsByRow() {
        // Oct 30 – Nov 2: Fri–Sun of one row, then Monday of the next.
        let weeks = weeks(for: utc("2026-10-15T12:00:00Z"))
        let segments = MiniMonthHighlight.segments(weeks: weeks,
                                                   range: dayRange("2026-10-30", "2026-11-02"),
                                                   calendar: calendar)
        let row = weeks.firstIndex { $0.contains {
            calendar.isDate($0, inSameDayAs: utc("2026-10-30T00:00:00Z"))
        } }!
        #expect(plain(segments) == [[row, 4, 6], [row + 1, 0, 0]])
    }
}
