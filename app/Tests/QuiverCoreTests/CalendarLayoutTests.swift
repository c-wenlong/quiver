import Foundation
import Testing
@testable import QuiverCore

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
        let segments = CalendarLayout.segments(
            [block("2026-10-05T23:55:00Z", "2026-10-05T23:55:00Z")], day: day,
            minimumDuration: 22.5 * 60)
        #expect(segments.count == 1)
        #expect(segments[0].end == day.end)
    }

    @Test func twoSeparateClustersKeepTheirOwnLaneCounts() {
        let segments = CalendarLayout.segments([
            block("2026-10-05T09:00:00Z", "2026-10-05T10:00:00Z", session: "a"),
            block("2026-10-05T09:15:00Z", "2026-10-05T10:30:00Z", session: "b"),
            block("2026-10-05T14:00:00Z", "2026-10-05T15:00:00Z", session: "c"),
        ], day: day)
        #expect(segments.map(\.laneCount) == [2, 2, 1])
    }
}
