import Testing
@testable import SWEWidgetsUI

/// The row fit against real desktop content heights: the medium widget's
/// content comes out under 132pt once WidgetKit's margins come off, which
/// used to floor its rows to four and leave a gap.
@Suite struct LimitsRowsTests {
    @Test func mediumHeightsGiveFiveRows() {
        #expect(LimitsView.rows(for: 132, compact: false) == 5)
        #expect(LimitsView.rows(for: 128, compact: false) == 5)
    }

    @Test func smallGivesSixCompactRows() {
        #expect(LimitsView.rows(for: 140, compact: true) == 6)
    }

    @Test func largeGivesAtLeastTheSnapshotCap() {
        // The snapshot carries 12 rows; the large widget wants more.
        #expect(LimitsView.rows(for: 350, compact: false) >= 12)
    }

    @Test func fittedRowsSpreadTheLeftover() {
        // 128pt and five rows leaves 28pt over four gaps: 7 each.
        #expect(LimitsView.rowSpacing(for: 128, count: 5, compact: false) == 7)
        // Room to spare tops out at the maximum gap.
        #expect(LimitsView.rowSpacing(for: 200, count: 5, compact: false) == 8)
        #expect(LimitsView.rowSpacing(for: 140, count: 6, compact: true) == 4)
        // One row gets no spacing at all.
        #expect(LimitsView.rowSpacing(for: 128, count: 1, compact: false) == 0)
    }
}
