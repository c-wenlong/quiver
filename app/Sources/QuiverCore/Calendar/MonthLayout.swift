import Foundation

/// The month view's pure layout: which days fill the 6×7 grid and which
/// session entries sit in each cell. The model's `range(for: .month)`
/// uses the same arithmetic, so the grid and the query never disagree.
public enum MonthLayout {
    /// The six week rows covering the anchor's month: the grid starts on
    /// the first weekday on or before the 1st and always holds 42 days,
    /// adjacent-month days included (Notion Calendar's fixed six rows).
    public static func weeks(for anchor: Date, calendar: Calendar) -> [[Date]] {
        guard let first = calendar.date(
            from: calendar.dateComponents([.year, .month], from: anchor)),
            let start = calendar.date(
                from: calendar.dateComponents([.yearForWeekOfYear, .weekOfYear], from: first))
        else { return [] }
        return (0..<6).map { week in
            (0..<7).compactMap {
                calendar.date(byAdding: .day, value: week * 7 + $0, to: start)
            }
        }
    }

    /// The interval the six weeks span; `CalendarModel.range(for:.month)`
    /// is exactly this, so the cell borders are the range borders.
    static func weekRange(for anchor: Date, calendar: Calendar) -> DateInterval? {
        guard let first = weeks(for: anchor, calendar: calendar).first,
              let start = first.first,
              let end = calendar.date(byAdding: .day, value: 42, to: start)
        else { return nil }
        return DateInterval(start: start, end: end)
    }

    /// One project's activity inside a day: the row a month cell shows.
    public struct Entry: Equatable, Sendable, Identifiable {
        /// The project's earliest block overlapping the day: carries the
        /// harness, account and sessions the row opens and colours by.
        public let block: ActivityBlock
        /// Its first block's start that day, clipped to the day.
        public let start: Date
        /// Seconds active inside the day, all its blocks summed.
        public let active: TimeInterval
        /// How many blocks of the session touch the day.
        public let blockCount: Int

        public var id: String {
            "\(block.harness)|\(block.accountHome)|\(block.project)|\(start.timeIntervalSince1970)"
        }
    }

    /// One entry per harness+account+project with blocks overlapping the
    /// day, sorted by the first block's start. A block running across
    /// midnight contributes its clipped share to both days' sums.
    public static func entries(_ blocks: [ActivityBlock], day: DateInterval) -> [Entry] {
        var grouped: [String: (earliest: ActivityBlock, start: Date,
                               active: TimeInterval, count: Int)] = [:]
        for block in blocks where block.start < day.end && block.end >= day.start {
            let key = "\(block.harness)|\(block.accountHome)|\(block.project)"
            let clippedStart = max(block.start, day.start)
            let clipped = min(block.end, day.end).timeIntervalSince(clippedStart)
            if var group = grouped[key] {
                group.active += clipped
                group.count += 1
                if clippedStart < group.start { group.start = clippedStart }
                if block.start < group.earliest.start { group.earliest = block }
                grouped[key] = group
            } else {
                grouped[key] = (block, clippedStart, clipped, 1)
            }
        }
        return grouped.values
            .map { Entry(block: $0.earliest, start: $0.start,
                         active: $0.active, blockCount: $0.count) }
            .sorted { $0.start != $1.start ? $0.start < $1.start : $0.id < $1.id }
    }

    /// `fitting` is how many rows the cell has room for. When the entries
    /// don't all fit, the last row becomes a "N more" button, so at most
    /// `fitting - 1` entries show alongside it.
    public static func split(_ entries: [Entry],
                             fitting rows: Int) -> (shown: [Entry], more: Int) {
        if entries.count <= rows { return (entries, 0) }
        let shown = max(0, rows - 1)
        return (Array(entries.prefix(shown)), entries.count - shown)
    }
}
