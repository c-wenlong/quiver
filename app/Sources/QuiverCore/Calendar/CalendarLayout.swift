import Foundation

/// Where each block lands on a calendar day: clipped to the day (a block
/// that runs past midnight becomes a segment on each day it touches) and
/// given a lane inside its overlap cluster, so side-by-side blocks share
/// the column the way Notion and Google Calendar split them.
public enum CalendarLayout {
    /// A block's slice on one day.
    public struct Segment: Sendable, Equatable, Identifiable {
        /// The block this slice belongs to; it keeps its real times.
        public var block: ActivityBlock
        /// The part of the block inside the day.
        public var start: Date
        public var end: Date
        /// 0-based column inside the overlap cluster.
        public var lane: Int
        /// How many lanes the cluster needs; a lone block gets 1.
        public var laneCount: Int

        /// The block's own span; `start`/`end` are the display times, which
        /// `minimumDuration` can stretch past the real end.
        public var realStart: Date { block.start }
        public var realEnd: Date { block.end }

        public var id: String { "\(block.id)|\(start.timeIntervalSince1970)" }
    }

    /// The day's segments, sorted by start. Two pieces count as overlapping
    /// only when one starts strictly before the other ends, so a block that
    /// ends on the hour sits above one that starts then. `minimumDuration`
    /// stretches a piece's display end (`block` keeps the real times), so a
    /// one-response block still gets a lane and some height.
    public static func segments(_ blocks: [ActivityBlock], day: DateInterval,
                                minimumDuration: TimeInterval = 0) -> [Segment] {
        struct Piece {
            var block: ActivityBlock
            var start: Date
            var end: Date
        }
        var pieces = blocks.compactMap { block -> Piece? in
            // Real overlap first: the minimum-duration stretch must not
            // pull a block onto a day it never touched.
            guard block.start < day.end, block.end >= day.start else { return nil }
            let start = max(block.start, day.start)
            let end = min(max(block.end, block.start + minimumDuration), day.end)
            guard end > start else { return nil }
            return Piece(block: block, start: start, end: end)
        }
        pieces.sort {
            $0.start != $1.start ? $0.start < $1.start : $0.end > $1.end
        }

        var segments: [Segment] = []
        var cluster: [Piece] = []
        var clusterEnd = Date.distantPast
        func flush() {
            // Greedy lane packing: each piece takes the first lane free at
            // its start; laneCount is the lanes the cluster needed.
            var laneEnds: [Date] = []
            for piece in cluster {
                var lane = 0
                while lane < laneEnds.count, laneEnds[lane] > piece.start { lane += 1 }
                if lane == laneEnds.count { laneEnds.append(piece.end) }
                else { laneEnds[lane] = piece.end }
                segments.append(Segment(block: piece.block, start: piece.start,
                                        end: piece.end, lane: lane, laneCount: 0))
            }
            let lanes = laneEnds.count
            for i in segments.indices.suffix(cluster.count) { segments[i].laneCount = lanes }
            cluster = []
            clusterEnd = .distantPast
        }

        for piece in pieces {
            if !cluster.isEmpty, piece.start >= clusterEnd { flush() }
            cluster.append(piece)
            clusterEnd = max(clusterEnd, piece.end)
        }
        if !cluster.isEmpty { flush() }
        return segments
    }

    /// Clock hours into `day`: the mapping the grid uses for a block's top
    /// and bottom, the now-line and its pill, so a DST shift never moves a
    /// block (a 10:00 response sits at 10.0 on a 23- or 25-hour day too).
    /// A time at or past the day's end reads as 24, before it as 0.
    public static func clockOffset(_ date: Date, in day: DateInterval,
                                   calendar: Calendar) -> TimeInterval {
        if date >= day.end { return 24 }
        if date < day.start { return 0 }
        let c = calendar.dateComponents([.hour, .minute, .second], from: date)
        return TimeInterval(c.hour ?? 0) + TimeInterval(c.minute ?? 0) / 60
            + TimeInterval(c.second ?? 0) / 3600
    }
}
