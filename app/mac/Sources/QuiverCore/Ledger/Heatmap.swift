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

/// Where the drawn cells sit, worked out from the grid's fixed metrics
/// instead of measured per cell. Per-cell measuring missed every column
/// added after the first layout: the card first lays out 8 weeks (width
/// still 0), then widens to about 15, and the added cells never reported
/// a frame, so hovering them drew no bubble.
public struct HeatmapLayout: Sendable, Equatable {
    /// One cell's side.
    public var cell: CGFloat
    /// The space between cells, and between the day labels and the first column.
    public var gap: CGFloat
    /// The day-label column left of the first week.
    public var gutter: CGFloat
    /// The first row's top-left corner (its day label's), in grid coordinates.
    public var origin: CGPoint

    public init(cell: CGFloat, gap: CGFloat, gutter: CGFloat, origin: CGPoint) {
        self.cell = cell
        self.gap = gap
        self.gutter = gutter
        self.origin = origin
    }

    /// The cell for `week` (0 is the leftmost drawn column) and `weekday`.
    public func frame(week: Int, weekday: Int) -> CGRect {
        CGRect(x: origin.x + gutter + gap + CGFloat(week) * (cell + gap),
               y: origin.y + CGFloat(weekday) * (cell + gap),
               width: cell, height: cell)
    }

    /// Every drawn cell's frame, keyed by its day.
    public func frames(columns: [[Heatmap.Cell?]]) -> [Date: CGRect] {
        var frames: [Date: CGRect] = [:]
        for (week, column) in columns.enumerated() {
            for (weekday, cell) in column.enumerated() {
                if let cell { frames[cell.day] = frame(week: week, weekday: weekday) }
            }
        }
        return frames
    }

    /// The drawn cell under a point in grid coordinates; nil over a gap,
    /// the labels, an undrawn day or outside the columns.
    public func cell(at point: CGPoint, columns: [[Heatmap.Cell?]]) -> Heatmap.Cell? {
        let x = point.x - origin.x - gutter - gap
        let y = point.y - origin.y
        guard x >= 0, y >= 0 else { return nil }
        let pitch = cell + gap
        let week = Int(x / pitch), weekday = Int(y / pitch)
        guard x - CGFloat(week) * pitch < cell, y - CGFloat(weekday) * pitch < cell,
              week < columns.count, weekday < columns[week].count else { return nil }
        return columns[week][weekday]
    }
}

/// Which cell the hover bubble belongs to and where it goes. Pure so every
/// cell in a grid can be exercised at once.
public enum HeatmapHover {
    /// The day's cell plus the bubble's center in grid coordinates, clamped
    /// so the bubble stays fully inside the grid: sideways first, then
    /// flipped under the cell when the top row leaves no room above.
    /// Nil when the day is not a drawn cell or has no measured frame.
    public static func resolve(
        day: Date, cells: [Heatmap.Cell], frames: [Date: CGRect],
        gridSize: CGSize, bubbleSize: CGSize
    ) -> (cell: Heatmap.Cell, origin: CGPoint)? {
        guard let cell = cells.first(where: { $0.day == day }),
              let frame = frames[day] else { return nil }
        let gap: CGFloat = 6
        let inset: CGFloat = 4
        let half = bubbleSize.width / 2
        let low = half + inset
        let high = max(low, gridSize.width - half - inset)
        let x = min(max(frame.midX, low), high)
        let above = frame.minY - gap - bubbleSize.height / 2
        let y = above - bubbleSize.height / 2 < inset
            ? frame.maxY + gap + bubbleSize.height / 2
            : above
        return (cell, CGPoint(x: x, y: y))
    }
}
