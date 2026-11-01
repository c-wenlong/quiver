import CoreGraphics
import Foundation
import Testing
@testable import SWECore

@Suite struct DayColumnGeometryTests {
    private var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }
    private let day = DateInterval(start: utc("2026-10-14T00:00:00Z"), duration: 86400)

    private func block(_ start: String, minutes: Double, session: String) -> ActivityBlock {
        var block = ActivityBlock(harness: "claude", accountHome: "/h", sessionId: session,
                                  start: utc(start),
                                  end: utc(start).addingTimeInterval(minutes * 60),
                                  responses: 1)
        block.project = session
        return block
    }

    private func geometry(_ blocks: [ActivityBlock], maxLanes: Int = 4) -> DayColumnGeometry {
        DayColumnGeometry(layout: CalendarLayout.layout(blocks, day: day, maxLanes: maxLanes),
                          day: day, calendar: calendar, width: 100, hourHeight: 48)
    }

    @Test func aLoneBlockFillsItsColumnFromItsStartHour() {
        let g = geometry([block("2026-10-14T09:00:00Z", minutes: 120, session: "a")])
        // One lane: a point in from each side; 09:00 at 48pt an hour.
        #expect(g.items.map(\.frame) == [CGRect(x: 1, y: 432, width: 98, height: 96)])
    }

    @Test func overlappingBlocksSplitTheColumnIntoLanes() {
        let g = geometry([block("2026-10-14T09:00:00Z", minutes: 120, session: "a"),
                          block("2026-10-14T10:00:00Z", minutes: 60, session: "b")])
        #expect(g.items.map(\.frame) == [CGRect(x: 1, y: 432, width: 48, height: 96),
                                         CGRect(x: 51, y: 480, width: 48, height: 48)])
    }

    @Test func aShortBlockKeepsAReadableSliver() {
        let g = geometry([block("2026-10-14T09:00:00Z", minutes: 5, session: "a")])
        #expect(g.items.first?.frame.height == DayColumnGeometry.minimumHeight)
    }

    @Test func thePointerFindsTheBlockOrChipUnderIt() {
        // Three overlapping blocks with room for two lanes: the longest
        // keeps lane 0, the other two fold into a "+2" chip on lane 1.
        let g = geometry([block("2026-10-14T09:00:00Z", minutes: 180, session: "long"),
                          block("2026-10-14T09:30:00Z", minutes: 60, session: "b"),
                          block("2026-10-14T10:00:00Z", minutes: 60, session: "c")],
                         maxLanes: 2)
        guard case .segment(let segment)? = g.item(at: CGPoint(x: 20, y: 500))?.kind else {
            Issue.record("expected the long block in lane 0"); return
        }
        #expect(segment.block.project == "long")
        guard case .overflow(let chip)? = g.item(at: CGPoint(x: 70, y: 500))?.kind else {
            Issue.record("expected the chip in lane 1"); return
        }
        #expect(chip.hidden.count == 2)
        // Empty hours hit nothing.
        #expect(g.item(at: CGPoint(x: 20, y: 100)) == nil)
    }

    @Test func whereFramesOverlapTheOneDrawnLastWins() {
        // The canvas draws items in order, so a later item covers an
        // earlier one; the pointer must find the one on top.
        let under = block("2026-10-14T09:00:00Z", minutes: 120, session: "under")
        let over = block("2026-10-14T10:00:00Z", minutes: 30, session: "over")
        let layout = CalendarLayout.DayLayout(segments: [
            .init(block: under, start: under.start, end: under.end, lane: 0, laneCount: 1),
            .init(block: over, start: over.start, end: over.end, lane: 0, laneCount: 1),
        ], overflows: [])
        let g = DayColumnGeometry(layout: layout, day: day, calendar: calendar,
                                  width: 100, hourHeight: 48)
        let point = CGPoint(x: 50, y: 492) // 10:15
        #expect(g.items.allSatisfy { $0.frame.contains(point) })
        guard case .segment(let segment)? = g.item(at: point)?.kind else {
            Issue.record("expected a segment"); return
        }
        #expect(segment.block.project == "over")
    }
}
