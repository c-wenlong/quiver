import Foundation
import Testing
@testable import SWECore

/// `CalendarLayout.segments`: blocks clipped to a day and given lanes
/// inside their overlap cluster.
@Suite struct CalendarLayoutTests {
    private let day = DateInterval(start: utc("2026-10-05T00:00:00Z"),
                                   end: utc("2026-10-06T00:00:00Z"))

    private func block(_ start: String, _ end: String,
                       session: String = "s") -> ActivityBlock {
        ActivityBlock(harness: "claude", accountHome: "/h", sessionId: session,
                      start: utc(start), end: utc(end), responses: 1)
    }

    @Test func aBlockPastMidnightSplitsIntoTwoDaySegments() {
        let segments = CalendarLayout.segments(
            [block("2026-10-05T23:00:00Z", "2026-10-06T01:00:00Z")], day: day)
        #expect(segments.count == 1)
        #expect(segments[0].start == utc("2026-10-05T23:00:00Z"))
        #expect(segments[0].end == day.end)

        let next = DateInterval(start: day.end, duration: 86_400)
        let segments2 = CalendarLayout.segments(
            [block("2026-10-05T23:00:00Z", "2026-10-06T01:00:00Z")], day: next)
        #expect(segments2.count == 1)
        #expect(segments2[0].start == next.start)
        #expect(segments2[0].end == utc("2026-10-06T01:00:00Z"))
    }

    @Test func threeOverlappingBlocksGetThreeLanes() {
        let segments = CalendarLayout.segments([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T10:00:00Z", "2026-10-05T12:00:00Z", session: "c"),
        ], day: day)
        #expect(segments.count == 3)
        #expect(Set(segments.map(\.lane)) == [0, 1, 2])
        #expect(segments.allSatisfy { $0.laneCount == 3 })
    }

    @Test func nonOverlappingBlocksShareLaneZero() {
        let segments = CalendarLayout.segments([
            block("2026-10-05T09:00:00Z", "2026-10-05T10:00:00Z", session: "a"),
            // Starts exactly when the first ends: no overlap.
            block("2026-10-05T10:00:00Z", "2026-10-05T11:00:00Z", session: "b"),
        ], day: day)
        #expect(segments.map(\.lane) == [0, 0])
        #expect(segments.allSatisfy { $0.laneCount == 1 })
    }

    @Test func aFreedLaneIsReusedInsideACluster() {
        let segments = CalendarLayout.segments([
            block("2026-10-05T09:00:00Z", "2026-10-05T12:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:00:00Z", session: "b"),
            // Overlaps a but starts after b ends, so it takes b's lane.
            block("2026-10-05T10:30:00Z", "2026-10-05T11:00:00Z", session: "c"),
        ], day: day)
        #expect(segments.map(\.lane) == [0, 1, 1])
        #expect(segments.allSatisfy { $0.laneCount == 2 })
    }

    @Test func aSingleResponseBlockStillGetsASegment() {
        let segments = CalendarLayout.segments(
            [block("2026-10-05T09:00:00Z", "2026-10-05T09:00:00Z")], day: day,
            minimumDuration: 22.5 * 60)
        #expect(segments.count == 1)
        // The real times stay on the block; the display end is stretched.
        #expect(segments[0].block.end == segments[0].block.start)
        #expect(segments[0].end == segments[0].start.addingTimeInterval(22.5 * 60))
    }

    @Test func twoCloseSingleResponseBlocksGetSeparateLanes() {
        let segments = CalendarLayout.segments([
            block("2026-10-05T09:00:00Z", "2026-10-05T09:00:00Z", session: "a"),
            block("2026-10-05T09:10:00Z", "2026-10-05T09:10:00Z", session: "b"),
        ], day: day, minimumDuration: 22.5 * 60)
        #expect(segments.map(\.lane) == [0, 1])
        #expect(segments.allSatisfy { $0.laneCount == 2 })
    }

    @Test func aStretchedBlockIsStillClippedToTheDayEnd() {
        let block = block("2026-10-05T23:55:00Z", "2026-10-05T23:55:00Z")
        let segments = CalendarLayout.segments([block], day: day,
                                               minimumDuration: 22.5 * 60)
        #expect(segments.count == 1)
        #expect(segments[0].end == day.end)
        // The stretch must not paint it onto the next day too.
        let next = DateInterval(start: day.end, duration: 86_400)
        #expect(CalendarLayout.segments([block], day: next,
                                        minimumDuration: 22.5 * 60).isEmpty)
    }

    @Test func twoSeparateClustersKeepTheirOwnLaneCounts() {
        let segments = CalendarLayout.segments([
            block("2026-10-05T09:00:00Z", "2026-10-05T10:00:00Z", session: "a"),
            block("2026-10-05T09:15:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T14:00:00Z", "2026-10-05T15:00:00Z", session: "c"),
        ], day: day)
        #expect(segments.map(\.laneCount) == [2, 2, 1])
    }

    @Test func aClusterInsideTheLaneCapMatchesTheUncappedLayout() {
        let blocks = [
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T10:00:00Z", "2026-10-05T12:00:00Z", session: "c"),
        ]
        let layout = CalendarLayout.layout(blocks, day: day, maxLanes: 4)
        #expect(layout.overflows.isEmpty)
        #expect(layout.segments
            == CalendarLayout.segments(blocks, day: day))
    }

    @Test func fourOverThreeLanesFoldTwoIntoOneChip() throws {
        // A–D all overlap; the two longest keep lanes 0 and 1, the rest
        // land in one chip on lane 2 spanning their union.
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:15:00Z", "2026-10-05T10:45:00Z", session: "b"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:00:00Z", session: "c"),
            block("2026-10-05T09:45:00Z", "2026-10-05T10:15:00Z", session: "d"),
        ], day: day, maxLanes: 3)
        #expect(layout.segments.compactMap(\.block.sessions.first?.sessionId).sorted() == ["a", "b"])
        #expect(layout.segments.map(\.lane).sorted() == [0, 1])
        #expect(layout.segments.allSatisfy { $0.laneCount == 3 })
        try #require(layout.overflows.count == 1)
        let chip = layout.overflows[0]
        #expect(chip.hidden.compactMap(\.block.sessions.first?.sessionId).sorted() == ["c", "d"])
        #expect(chip.start == utc("2026-10-05T09:30:00Z"))
        #expect(chip.end == utc("2026-10-05T10:15:00Z"))
        #expect(chip.lane == 2 && chip.laneCount == 3)
    }

    @Test func theLongestBlockKeepsItsLane() {
        // The early short block loses to the later long one: priority is
        // real span, not start order.
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T09:15:00Z", session: "short"),
            block("2026-10-05T09:05:00Z", "2026-10-05T11:00:00Z", session: "long"),
            block("2026-10-05T09:10:00Z", "2026-10-05T10:30:00Z", session: "mid"),
        ], day: day, maxLanes: 2)
        #expect(layout.segments.compactMap(\.block.sessions.first?.sessionId) == ["long"])
        #expect(layout.overflows.flatMap(\.hidden).compactMap(\.block.sessions.first?.sessionId)
            == ["short", "mid"])
    }

    @Test func aClusterThatExactlyFitsKeepsEveryLane() {
        // Three blocks, three lanes, maxLanes 3: nothing folds, so no
        // "+1" chip ever sits as wide as the block it would hide.
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T10:00:00Z", "2026-10-05T12:00:00Z", session: "c"),
        ], day: day, maxLanes: 3)
        #expect(layout.overflows.isEmpty)
        #expect(layout.segments.map(\.lane).sorted() == [0, 1, 2])
        #expect(layout.segments.allSatisfy { $0.laneCount == 3 })
    }

    @Test func twoHiddenPiecesInOneRunMakeAChip() throws {
        // Three mutually overlapping blocks over two lanes: one keeps
        // the lane, the other two overlap each other while hidden, so
        // they share one "+2" chip.
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T10:00:00Z", "2026-10-05T11:30:00Z", session: "c"),
        ], day: day, maxLanes: 2)
        #expect(layout.segments.compactMap(\.block.sessions.first?.sessionId) == ["a"])
        try #require(layout.overflows.count == 1)
        #expect(layout.overflows[0].hidden.compactMap(\.block.sessions.first?.sessionId) == ["b", "c"])
        #expect(layout.overflows[0].lane == 1 && layout.overflows[0].laneCount == 2)
    }

    /// Long blocks fill every lane but the last. Of the hidden ones, B is
    /// alone in its run, so it renders as a real block on the last lane
    /// rather than a "+1" chip as wide as itself; C and G overlap and fold
    /// into one "+2" chip there too. The same at any lane count.
    @Test(arguments: [2, 3])
    func aLoneHiddenRunRendersAsASegmentInTheLastLane(maxLanes: Int) throws {
        let long = (1..<maxLanes).map { "long\($0)" }
        let layout = CalendarLayout.layout(long.map {
            block("2026-10-05T09:00:00Z", "2026-10-05T12:00:00Z", session: $0)
        } + [
            block("2026-10-05T09:30:00Z", "2026-10-05T10:00:00Z", session: "b"),
            block("2026-10-05T11:00:00Z", "2026-10-05T11:30:00Z", session: "c"),
            block("2026-10-05T11:10:00Z", "2026-10-05T11:20:00Z", session: "g"),
        ], day: day, maxLanes: maxLanes)
        #expect(layout.segments.compactMap(\.block.sessions.first?.sessionId).sorted() == ["b"] + long)
        let b = try #require(layout.segments.first {
            $0.block.sessions.first?.sessionId == "b" })
        #expect(b.lane == maxLanes - 1 && b.laneCount == maxLanes)
        try #require(layout.overflows.count == 1)
        #expect(layout.overflows[0].hidden.compactMap(\.block.sessions.first?.sessionId) == ["c", "g"])
        #expect(layout.overflows[0].start == utc("2026-10-05T11:00:00Z"))
        #expect(layout.overflows[0].end == utc("2026-10-05T11:30:00Z"))
        #expect(layout.overflows[0].lane == maxLanes - 1 && layout.overflows[0].laneCount == maxLanes)
    }

    @Test func aLaterLoneBlockKeepsItsOwnClusterLaneCount() {
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:15:00Z", "2026-10-05T10:45:00Z", session: "b"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "c"),
            block("2026-10-05T14:00:00Z", "2026-10-05T15:00:00Z", session: "alone"),
        ], day: day, maxLanes: 2)
        let alone = layout.segments.first { $0.block.sessions.first?.sessionId == "alone" }
        #expect(alone?.laneCount == 1)
        #expect(alone?.lane == 0)
    }

    @Test func maxLanesOneBehavesLikeTwo() throws {
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T10:00:00Z", "2026-10-05T11:30:00Z", session: "c"),
        ], day: day, maxLanes: 1)
        try #require(layout.segments.count == 1)
        #expect(layout.segments[0].lane == 0)
        #expect(layout.segments[0].laneCount == 2)
        try #require(layout.overflows.count == 1)
        #expect(layout.overflows[0].lane == 1)
    }

    @Test func aZeroLengthBlockOverlapsByItsStretchButRanksByItsRealSpan() throws {
        // The single-response block is stretched for overlap, so it joins
        // the cluster, but its real span of zero loses to any real block.
        let layout = CalendarLayout.layout([
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:15:00Z", "2026-10-05T10:45:00Z", session: "b"),
            block("2026-10-05T09:30:00Z", "2026-10-05T09:30:00Z", session: "z"),
        ], day: day, minimumDuration: 22.5 * 60, maxLanes: 2)
        #expect(layout.segments.compactMap(\.block.sessions.first?.sessionId) == ["a"])
        try #require(layout.overflows.count == 1)
        #expect(layout.overflows[0].hidden.compactMap(\.block.sessions.first?.sessionId) == ["b", "z"])
    }

    @Test func overflowIdsDifferAcrossDays() throws {
        let blocks = [
            block("2026-10-05T09:00:00Z", "2026-10-05T11:00:00Z", session: "a"),
            block("2026-10-05T09:30:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T10:00:00Z", "2026-10-05T11:30:00Z", session: "c"),
        ]
        let first = CalendarLayout.layout(blocks, day: day, maxLanes: 2)
        let next = DateInterval(start: day.end, duration: 86_400)
        var shifted: [ActivityBlock] = []
        for b in blocks {
            var copy = b
            copy.start = b.start.addingTimeInterval(86_400)
            copy.end = b.end.addingTimeInterval(86_400)
            shifted.append(copy)
        }
        let second = CalendarLayout.layout(shifted, day: next, maxLanes: 2)
        try #require(first.overflows.count == 1)
        try #require(second.overflows.count == 1)
        #expect(first.overflows[0].id != second.overflows[0].id)
    }

    @Test func clockOffsetReadsClockTimeThroughDST() {
        // 8 March 2026: America/New_York springs forward; a 10:00 event
        // sits at clock hour 10 even though midnight is 9 hours away.
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "America/New_York")!
        let day = DateInterval(start: cal.startOfDay(for: utc("2026-03-08T15:00:00Z")),
                               duration: 86_400)
        let ten = cal.date(from: DateComponents(year: 2026, month: 3, day: 8,
                                                hour: 10))!
        #expect(CalendarLayout.clockOffset(ten, in: day, calendar: cal) == 10)
        #expect(CalendarLayout.clockOffset(day.end, in: day, calendar: cal) == 24)
        #expect(CalendarLayout.clockOffset(day.start, in: day, calendar: cal) == 0)
        // Half past ten is ten and a half.
        let half = ten.addingTimeInterval(30 * 60)
        #expect(CalendarLayout.clockOffset(half, in: day, calendar: cal) == 10.5)
    }
}

@Suite struct DayHeaderStyleTests {
    @Test func `headers shrink with the column`() {
        #expect(DayHeaderStyle.forWidth(70) == .inline)
        #expect(DayHeaderStyle.forWidth(50) == .stacked)
        #expect(DayHeaderStyle.forWidth(30) == .compact)
        // The exact boundaries.
        #expect(DayHeaderStyle.forWidth(64) == .inline)
        #expect(DayHeaderStyle.forWidth(36) == .stacked)
        #expect(DayHeaderStyle.forWidth(63.9) == .stacked)
        #expect(DayHeaderStyle.forWidth(35.9) == .compact)
    }
}
