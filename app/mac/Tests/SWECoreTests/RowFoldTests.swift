import Testing
@testable import SWECore

@Suite struct RowFoldTests {
    private let rows = [1, 2, 3, 4, 5, 6]

    @Test func aShortListShowsEverything() {
        #expect(RowFold.visible([1, 2, 3, 4, 5], limit: 5, expanded: false) == [1, 2, 3, 4, 5])
        #expect(RowFold.hiddenCount(total: 5, limit: 5) == 0)
    }

    @Test func aLongListCollapsedShowsTheTopAndHidesTheRest() {
        #expect(RowFold.visible(rows, limit: 5, expanded: false) == [1, 2, 3, 4, 5])
        #expect(RowFold.hiddenCount(total: 6, limit: 5) == 1)
    }

    @Test func expandedShowsEverything() {
        #expect(RowFold.visible(rows, limit: 5, expanded: true) == rows)
    }

    @Test func aFlaggedRowBehindTheFoldCountsUntilItIsShown() {
        let isFlagged = { $0 == 6 }
        #expect(RowFold.hiddenFlagged(rows, limit: 5, expanded: false, isFlagged: isFlagged) == 1)
        #expect(RowFold.hiddenFlagged(rows, limit: 5, expanded: true, isFlagged: isFlagged) == 0)
    }

    @Test func aFlaggedRowAboveTheFoldCountsForNothing() {
        #expect(RowFold.hiddenFlagged(rows, limit: 5, expanded: false, isFlagged: { $0 == 3 }) == 0)
    }
}
