import Foundation

/// The Overview's activity grid: one column per week, one row per weekday.
public struct Heatmap: Sendable, Equatable {
    public struct Cell: Sendable, Equatable {
        public var day: Date
        public var tokens: Int
        /// The day's priced spend, for the hover stat.
        public var costUSD: Double
        /// Tokens from models with no list price, so $0 reads differently.
        public var unpricedTokens: Int
        /// 0 for no usage, else 1...4 by how busy the day was among the busy ones.
        public var level: Int
    }

    /// Oldest week first. A day still in the future is nil.
    public let columns: [[Cell?]]

    /// Tokens across the days drawn, and how many of them had any.
    public var totalTokens: Int { columns.joined().compactMap { $0?.tokens }.reduce(0, +) }
    public var activeDays: Int { columns.joined().filter { ($0?.tokens ?? 0) > 0 }.count }

    public init(days: [DayRow], weeks: Int, now: Date, calendar: Calendar) {
        let today = calendar.startOfDay(for: now)
        let weekStart = calendar.dateInterval(of: .weekOfYear, for: today)?.start ?? today
        let first = calendar.date(byAdding: .day, value: -7 * (weeks - 1), to: weekStart) ?? weekStart

        var tokens: [Date: Int] = [:]
        var costs: [Date: Double] = [:]
        var unpriced: [Date: Int] = [:]
        for row in days {
            let day = calendar.startOfDay(for: row.day)
            tokens[day, default: 0] += row.totals.totalTokens
            costs[day, default: 0] += row.totals.costUSD
            unpriced[day, default: 0] += row.totals.unpricedTokens
        }
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
                return Cell(day: day, tokens: value, costUSD: costs[day] ?? 0,
                            unpricedTokens: unpriced[day] ?? 0, level: level(value))
            }
        }
    }
}
