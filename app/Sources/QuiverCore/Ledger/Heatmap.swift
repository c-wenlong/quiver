import Foundation

/// The Overview's activity grid: one column per week, one row per weekday.
public struct Heatmap: Sendable, Equatable {
    public struct Cell: Sendable, Equatable {
        public var day: Date
        public var tokens: Int
        /// 0 for no usage, else 1...4 by how busy the day was among the busy ones.
        public var level: Int
    }

    /// Oldest week first. A day still in the future is nil.
    public let columns: [[Cell?]]

    public init(days: [DayRow], weeks: Int, now: Date, calendar: Calendar) {
        let today = calendar.startOfDay(for: now)
        let weekStart = calendar.dateInterval(of: .weekOfYear, for: today)?.start ?? today
        let first = calendar.date(byAdding: .day, value: -7 * (weeks - 1), to: weekStart) ?? weekStart

        var tokens: [Date: Int] = [:]
        for row in days { tokens[calendar.startOfDay(for: row.day), default: 0] += row.totals.totalTokens }
        // Quartiles of the busy days, so one huge day doesn't wash out the rest.
        let busy = tokens.values.filter { $0 > 0 }.sorted()
        func level(_ value: Int) -> Int {
            guard value > 0, !busy.isEmpty else { return 0 }
            let below = busy.firstIndex { $0 >= value } ?? busy.count
            return min(4, 1 + below * 4 / busy.count)
        }

        columns = (0..<weeks).map { week in
            (0..<7).map { weekday in
                guard let day = calendar.date(byAdding: .day, value: week * 7 + weekday, to: first),
                      day <= today else { return nil }
                let value = tokens[day] ?? 0
                return Cell(day: day, tokens: value, level: level(value))
            }
        }
    }
}
