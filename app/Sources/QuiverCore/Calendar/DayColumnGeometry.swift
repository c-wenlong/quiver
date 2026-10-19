import CoreGraphics
import Foundation

/// Where a day column draws each block segment and "+N" chip, and which
/// one the pointer is over. The column draws them all in one canvas, so
/// hover and tap find their target through this lookup instead of one
/// view per block.
public struct DayColumnGeometry: Sendable {
    public enum Kind: Sendable {
        case segment(CalendarLayout.Segment)
        case overflow(CalendarLayout.Overflow)
    }

    public struct Item: Sendable, Identifiable {
        /// The segment's or chip's id, which is also its hover-card key.
        public var id: String
        public var frame: CGRect
        public var kind: Kind
    }

    /// Segments first, then chips: the order they are drawn in, so a
    /// later item is on top.
    public let items: [Item]
    private let day: DateInterval
    private let calendar: Calendar
    private let hourHeight: CGFloat

    /// A block shorter than this still gets a readable sliver.
    public static let minimumHeight: CGFloat = 18

    public init(layout: CalendarLayout.DayLayout, day: DateInterval, calendar: Calendar,
                width: CGFloat, hourHeight: CGFloat) {
        self.day = day
        self.calendar = calendar
        self.hourHeight = hourHeight
        func y(_ date: Date) -> CGFloat {
            CGFloat(CalendarLayout.clockOffset(date, in: day, calendar: calendar)) * hourHeight
        }
        func frame(lane: Int, laneCount: Int, start: Date, end: Date) -> CGRect {
            let laneWidth = width / CGFloat(max(1, laneCount))
            return CGRect(x: CGFloat(lane) * laneWidth + 1, y: y(start),
                          width: max(0, laneWidth - 2),
                          height: max(Self.minimumHeight, y(end) - y(start)))
        }
        items = layout.segments.map {
            Item(id: $0.id, frame: frame(lane: $0.lane, laneCount: $0.laneCount,
                                         start: $0.start, end: $0.end),
                 kind: .segment($0))
        } + layout.overflows.map {
            Item(id: $0.id, frame: frame(lane: $0.lane, laneCount: $0.laneCount,
                                         start: $0.start, end: $0.end),
                 kind: .overflow($0))
        }
    }

    /// The item under a point in column coordinates; where frames
    /// overlap, the one drawn last.
    public func item(at point: CGPoint) -> Item? {
        items.last { $0.frame.contains(point) }
    }

    /// Clock hours into the day, not elapsed seconds: on a DST day the
    /// hour a response happened still decides where it sits.
    public func y(_ date: Date) -> CGFloat {
        CGFloat(CalendarLayout.clockOffset(date, in: day, calendar: calendar)) * hourHeight
    }
}
