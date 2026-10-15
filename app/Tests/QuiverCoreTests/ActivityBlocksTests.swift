import Foundation
import Testing
@testable import QuiverCore

/// `ActivityBlocks.make` mirrors the ledger's SQL block rule; these are
/// the same cases `CalendarActivityTests` puts through the query.
@Suite struct ActivityBlocksTests {
    private let range = DateInterval(start: utc("2026-10-14T00:00:00Z"),
                                     end: utc("2026-10-15T00:00:00Z"))

    private func make(_ times: [Date]) -> [ActivityBlock] {
        ActivityBlocks.make(harness: "pi", accountHome: "/h", sessionId: "s",
                            cwd: "/w", timestamps: times, range: range,
                            gap: CalendarModel.gap)
    }

    @Test func responsesInsideTheGapJoinIntoOneBlock() {
        let blocks = make([utc("2026-10-14T10:00:00Z"),
                           utc("2026-10-14T10:15:00Z")])
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-14T10:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-14T10:15:00Z"))
        #expect(blocks[0].responses == 2)
        #expect(blocks[0].cwd == "/w")
    }

    @Test func aGapLongerThanTheLimitStartsANewBlock() {
        let blocks = make([utc("2026-10-14T10:00:00Z"),
                           utc("2026-10-14T10:15:01Z")])
        #expect(blocks.count == 2)
    }

    @Test func aBlockStraddlingTheRangeEdgeStaysWhole() {
        let blocks = make([utc("2026-10-13T23:50:00Z"),
                           utc("2026-10-14T00:04:00Z")])
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-13T23:50:00Z"))
    }

    @Test func blocksOutsideTheRangeAreDropped() {
        #expect(make([utc("2026-10-12T10:00:00Z")]).isEmpty)
        #expect(make([utc("2026-10-15T00:10:00Z")]).isEmpty)
    }

    @Test func aSingleResponseIsOneBlock() {
        let blocks = make([utc("2026-10-14T09:00:00Z")])
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 1)
    }
}
