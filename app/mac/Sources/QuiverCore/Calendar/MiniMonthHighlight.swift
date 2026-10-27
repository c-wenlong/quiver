import Foundation

/// Which day cells of the mini month carry the range highlight: one
/// continuous pill per week row covering just the in-range days, never
/// the week-number column. A range crossing a week boundary makes two
/// segments, one per row.
public enum MiniMonthHighlight {
    /// `(row, firstColumn, lastColumn)` runs of in-range days in `weeks`.
    /// Columns index into the row's day cells, not the week number; a
    /// contiguous range gives at most one segment per row.
    public static func segments(weeks: [[Date]], range: DateInterval,
                                calendar: Calendar)
        -> [(row: Int, firstColumn: Int, lastColumn: Int)] {
        var out: [(row: Int, firstColumn: Int, lastColumn: Int)] = []
        for (row, week) in weeks.enumerated() {
            var first: Int?
            var last = 0
            for (column, day) in week.enumerated() {
                let start = calendar.startOfDay(for: day)
                guard let end = calendar.date(byAdding: .day, value: 1, to: start)
                else { continue }
                if end > range.start && start < range.end {
                    if first == nil { first = column }
                    last = column
                }
            }
            if let first { out.append((row, first, last)) }
        }
        return out
    }
}
