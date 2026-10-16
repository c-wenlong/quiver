import Foundation
import Testing
@testable import QuiverCore

@Suite struct HoverSafeRegionTests {
    /// A chip at (500, 300), its card to the left at (100, 250): the union
    /// covers the arrow gap between them.
    private let chip = CGRect(x: 500, y: 300, width: 60, height: 24)
    private let card = CGRect(x: 100, y: 250, width: 300, height: 200)

    private func region() -> HoverSafeRegion {
        HoverSafeRegion(rect: chip.union(card))
    }

    /// Mutating `update` cannot sit inside `#expect`; unwrap the result.
    private func closing(_ region: inout HoverSafeRegion, _ point: CGPoint,
                         _ now: ContinuousClock.Instant) -> Bool {
        region.update(point: point, now: now)
    }

    @Test func theArrowGapBetweenChipAndCardCountsAsInside() {
        var region = region()
        let gap = CGPoint(x: 460, y: 350) // between the card's right edge and the chip
        #expect(!closing(&region, gap, ContinuousClock.now))
    }

    @Test func pausingInTheGapKeepsTheCardOpen() {
        var region = region()
        let gap = CGPoint(x: 460, y: 350)
        let t0 = ContinuousClock.now
        for step in 0..<20 {
            #expect(!closing(&region, gap, t0.advanced(by: .milliseconds(50 * step))))
        }
    }

    @Test func aShortTripOutsideThenBackKeepsTheCardOpen() {
        var region = region()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(100))))
        #expect(!closing(&region, CGPoint(x: 550, y: 310), t0.advanced(by: .milliseconds(150))))
        // The timer reset: another 100 ms outside still does not close.
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(200))))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(290))))
    }

    @Test func stayingOutsideForTheGraceClosesTheCard() {
        var region = region()
        let t0 = ContinuousClock.now
        let away = CGPoint(x: 900, y: 900)
        #expect(!closing(&region, away, t0))
        #expect(!closing(&region, away, t0.advanced(by: .milliseconds(149))))
        #expect(closing(&region, away, t0.advanced(by: .milliseconds(150))))
    }
}
