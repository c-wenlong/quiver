import Foundation
import Testing
@testable import QuiverCore

@Suite struct RowReorderTests {
    @Test func noMovementKeepsTheOriginalIndex() {
        let heights: [CGFloat] = [40, 60, 40]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 0) == 1)
    }

    @Test func draggingDownPastANeighboursCentreLandsAfterIt() {
        let heights: [CGFloat] = [40, 40, 40]
        // Row 1's centre is 60, row 2's is 100.
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 41) == 2)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 39) == 1)
    }

    @Test func targetsFollowCentresWhenHeightsDiffer() {
        // Centres: 15, 60, 105, 135.
        let heights: [CGFloat] = [30, 60, 30, 30]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 46) == 2)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 44) == 1)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 1, translation: 120) == 3)
        #expect(RowReorder.targetIndex(heights: heights, dragged: 3, translation: -80) == 1)
    }

    @Test func draggingUpToTheTopLandsAtZero() {
        let heights: [CGFloat] = [40, 40, 40]
        #expect(RowReorder.targetIndex(heights: heights, dragged: 2, translation: -200) == 0)
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
