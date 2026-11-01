import Foundation
import Testing
@testable import SWECore

@Suite struct RowReorderTests {
    @Test func noMovementKeepsTheOriginalIndex() {
        let heights: [CGFloat] = [40, 60, 40]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 0) == 1)
    }

    @Test func draggingDownItsBottomEdgePastANeighboursCentreLandsAfterIt() {
        let heights: [CGFloat] = [40, 40, 40]
        // Row 1's bottom edge starts at 80; row 2's centre is 100.
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 21) == 2)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 20) == 1)
    }

    @Test func theLastSlotIsReachableAtTheClamp() {
        let heights: [CGFloat] = [44, 44]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 0, translation: 44) == 1)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 0, translation: 22) == 0)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 0, translation: 23) == 1)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 0, translation: 500) == 1)
        let three: [CGFloat] = [44, 44, 44]
        #expect(RowReorder.targetIndex(heights: three, dragged: 0, translation: 88) == 2)
    }

    @Test func targetsFollowTheLeadingEdgeWhenHeightsDiffer() {
        // Centres: 15, 60, 105, 135. Row 1's bottom edge starts at 90.
        let heights: [CGFloat] = [30, 60, 30, 30]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 15) == 1)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 16) == 2)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 46) == 3)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 3, translation: -80) == 1)
        // [80,44]: the row clamps at 44, but its bottom edge already
        // clears the neighbour's centre (102) well before that.
        #expect(RowReorder.targetIndex(heights: [80, 44], dragged: 0, translation: 44) == 1)
        #expect(RowReorder.targetIndex(heights: [80, 44], dragged: 0, translation: 22) == 0)
        #expect(RowReorder.targetIndex(heights: [80, 44], dragged: 0, translation: 23) == 1)
        // [44,80]: the taller neighbour's centre sits at 84.
        #expect(RowReorder.targetIndex(heights: [44, 80], dragged: 0, translation: 40) == 0)
        #expect(RowReorder.targetIndex(heights: [44, 80], dragged: 0, translation: 41) == 1)
    }

    @Test func draggingUpToTheTopLandsAtZero() {
        let heights: [CGFloat] = [40, 40, 40]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 2, translation: -200) == 0)
    }

    @Test func draggingUpItsTopEdgePastACentreLandsBeforeIt() {
        let heights: [CGFloat] = [44, 44, 44]
        // Row 2's top edge starts at 88; centres sit at 22 and 66.
        #expect(RowReorder.targetIndex(heights: heights, dragged: 2, translation: -88) == 0)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 2, translation: -23) == 1)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 2, translation: -22) == 2)
    }

    @Test func theDragClampsAtBothEnds() {
        let heights: [CGFloat] = [40, 40, 40]
        #expect(RowReorder.clamped(-50, heights: heights, dragged: 0) == 0)
        #expect(RowReorder.clamped(200, heights: heights, dragged: 0) == 80)
        #expect(RowReorder.clamped(-200, heights: heights, dragged: 2) == -80)
        #expect(RowReorder.clamped(50, heights: heights, dragged: 2) == 0)
        #expect(RowReorder.clamped(30, heights: heights, dragged: 1) == 30)
    }

    @Test func onlyRowsBetweenTheDraggedRowAndTheTargetSlide() {
        let heights: [CGFloat] = [40, 50, 30]
        // Row 0 (40pt) dragged below row 2: rows 1 and 2 slide up 40.
        #expect(RowReorder.offset(of: 1, dragged: 0, target: 2, heights: heights) == -40)
        #expect(RowReorder.offset(of: 2, dragged: 0, target: 2, heights: heights) == -40)
        #expect(RowReorder.offset(of: 0, dragged: 0, target: 2, heights: heights) == 0)
        // Row 2 (30pt) dragged to the top: rows 0 and 1 slide down 30.
        #expect(RowReorder.offset(of: 0, dragged: 2, target: 0, heights: heights) == 30)
        #expect(RowReorder.offset(of: 1, dragged: 2, target: 0, heights: heights) == 30)
        #expect(RowReorder.offset(of: 2, dragged: 2, target: 0, heights: heights) == 0)
        // A drop back in place moves nothing.
        #expect(RowReorder.offset(of: 1, dragged: 0, target: 0, heights: heights) == 0)
    }

    @Test func insertBeforeNamesTheRowAtTheTargetOrNilAtTheEnd() {
        let ids = ["a", "b", "c", "d"]
        // "b" lands between "c" and "d": insert before "d".
        #expect(RowReorder.insertBefore(ids: ids, dragged: 1, target: 2) == "d")
        // "b" lands last.
        #expect(RowReorder.insertBefore(ids: ids, dragged: 1, target: 3) == nil)
        // "b" dropped back where it was: before "c".
        #expect(RowReorder.insertBefore(ids: ids, dragged: 1, target: 1) == "c")
    }
}
