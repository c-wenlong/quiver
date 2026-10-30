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

        public var id: String { "\(block.id)|\(start.timeIntervalSince1970)" }
    }

    /// The "+N" chip on a crowded day: the cluster pieces that lost their
    /// lane, folded into one labelled overflow over their combined span.
    public struct Overflow: Sendable, Equatable, Identifiable {
        /// The hidden pieces, sorted by start (then block id).
        public var hidden: [Segment]
        /// The union of the hidden display spans.
        public var start: Date
        public var end: Date
        /// The chip's own lane: always the last of `laneCount`.
        public var lane: Int
        public var laneCount: Int
        /// "overflow|<day.start epoch>|<group start epoch>".
        public var id: String
    }

    /// A day's placed segments plus the overflow chips that carry whatever
    /// the lane cap folded away.
    public struct DayLayout: Sendable, Equatable {
        public var segments: [Segment]
        public var overflows: [Overflow]
    }

    /// The day's segments, sorted by start. Two pieces count as overlapping
    /// only when one starts strictly before the other ends, so a block that
    /// ends on the hour sits above one that starts then. `minimumDuration`
    /// stretches a piece's display end (`block` keeps the real times), so a
    /// one-response block still gets a lane and some height.
    public static func segments(_ blocks: [ActivityBlock], day: DateInterval,
                                minimumDuration: TimeInterval = 0) -> [Segment] {
        layout(blocks, day: day, minimumDuration: minimumDuration,
               maxLanes: .max).segments
    }

    /// `segments` with a lane budget: a cluster needing `maxLanes` lanes
    /// or fewer packs exactly as it always did; one needing more keeps
    /// its longest pieces in `maxLanes - 1` lanes and folds the rest
    /// into overflow chips on the last lane — except a hidden run of a
    /// single piece, which keeps the last lane for itself since a "+1"
    /// chip would be as wide as the block it hid.
    public static func layout(_ blocks: [ActivityBlock], day: DateInterval,
                              minimumDuration: TimeInterval = 0,
                              maxLanes: Int) -> DayLayout {
        let maxLanes = Swift.max(2, maxLanes)
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
        var overflows: [Overflow] = []
        var cluster: [Piece] = []
        var clusterEnd = Date.distantPast
        func flush() {
            // Greedy lane packing: each piece takes the first lane free at
            // its start; laneCount is the lanes the cluster needed.
            var laneEnds: [Date] = []
            var lanes: [Int] = []
            for piece in cluster {
                var lane = 0
                while lane < laneEnds.count, laneEnds[lane] > piece.start { lane += 1 }
                if lane == laneEnds.count { laneEnds.append(piece.end) }
                else { laneEnds[lane] = piece.end }
                lanes.append(lane)
            }
            let needed = laneEnds.count
            // A cluster that fits maxLanes renders exactly as always;
            // only one needing more folds, so a block never hides
            // behind a chip as wide as the block itself.
            if needed <= maxLanes {
                for (piece, lane) in zip(cluster, lanes) {
                    segments.append(Segment(block: piece.block, start: piece.start,
                                            end: piece.end, lane: lane,
                                            laneCount: needed))
                }
            } else {
                // Too many lanes to stay readable: the longest blocks keep
                // lanes 0..<maxLanes-1 and the rest fold into "+N" chips on
                // the last lane. Priority is the real span; the minimum-
                // duration stretch only decides where a piece overlaps.
                let ranked = cluster.sorted {
                    let a = $0.block.end.timeIntervalSince($0.block.start)
                    let b = $1.block.end.timeIntervalSince($1.block.start)
                    if a != b { return a > b }
                    if $0.block.start != $1.block.start {
                        return $0.block.start < $1.block.start
                    }
                    return $0.block.id < $1.block.id
                }
                var placed: [[Piece]] = Array(repeating: [], count: maxLanes - 1)
                var hidden: [Piece] = []
                for piece in ranked {
                    var lane = 0
                    while lane < placed.count,
                          placed[lane].contains(where: {
                              piece.start < $0.end && $0.start < piece.end }) {
                        lane += 1
                    }
                    if lane < placed.count { placed[lane].append(piece) }
                    else { hidden.append(piece) }
                }
                for (lane, lanePieces) in placed.enumerated() {
                    for piece in lanePieces {
                        segments.append(Segment(block: piece.block,
                                                start: piece.start,
                                                end: piece.end,
                                                lane: lane,
                                                laneCount: maxLanes))
                    }
                }
                hidden.sort {
                    $0.start != $1.start
                        ? $0.start < $1.start : $0.block.id < $1.block.id
                }
                var hiddenGroups: [[Piece]] = []
                var groupEnd = Date.distantPast
                for piece in hidden {
                    if let last = hiddenGroups.last, piece.start < groupEnd {
                        hiddenGroups[hiddenGroups.count - 1] = last + [piece]
                        groupEnd = Swift.max(groupEnd, piece.end)
                    } else {
                        hiddenGroups.append([piece])
                        groupEnd = piece.end
                    }
                }
                for group in hiddenGroups {
                    let start = group.map(\.start).min()!
                    let end = group.map(\.end).max()!
                    if group.count == 1, let piece = group.first {
                        // A "+1" chip is as wide as the block it hides, so
                        // a lone hidden piece just takes the chip's lane.
                        // Groups are disjoint runs, so it can't collide
                        // with a real chip there.
                        segments.append(Segment(block: piece.block,
                                                start: piece.start,
                                                end: piece.end,
                                                lane: maxLanes - 1,
                                                laneCount: maxLanes))
                        continue
                    }
                    overflows.append(Overflow(
                        hidden: group.map {
                            Segment(block: $0.block, start: $0.start, end: $0.end,
                                    lane: maxLanes - 1, laneCount: maxLanes)
                        },
                        start: start, end: end,
                        lane: maxLanes - 1, laneCount: maxLanes,
                        id: "overflow|\(day.start.timeIntervalSince1970)"
                            + "|\(start.timeIntervalSince1970)"))
                }
            }
            cluster = []
            clusterEnd = .distantPast
        }

        for piece in pieces {
            if !cluster.isEmpty, piece.start >= clusterEnd { flush() }
            cluster.append(piece)
            clusterEnd = max(clusterEnd, piece.end)
        }
        if !cluster.isEmpty { flush() }
        segments.sort {
            $0.start != $1.start ? $0.start < $1.start : $0.end > $1.end
        }
        return DayLayout(segments: segments, overflows: overflows)
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

/// How a day column's header lays out at a given width: the full
/// "Mon 12" inline when there's room, "Mon" over "12" stacked when the
/// column tightens, and a one-letter weekday when it's very narrow.
public enum DayHeaderStyle: Sendable, Equatable {
    case inline, stacked, compact

    public static func forWidth(_ width: CGFloat) -> DayHeaderStyle {
        switch width {
        case 64...: return .inline
        case 36...: return .stacked
        default: return .compact
        }
    }
}
