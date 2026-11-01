import Testing
@testable import SWECore

@Suite struct TextZoomTests {
    @Test func standardIsAStep() {
        #expect(TextZoom.steps.contains(TextZoom.standard))
        #expect(TextZoom.steps == TextZoom.steps.sorted())
    }

    @Test func zoomsClampToTheRange() {
        #expect(TextZoom.clamped(0.1) == 0.75)
        #expect(TextZoom.clamped(1.1) == 1.1)
        #expect(TextZoom.clamped(4) == 1.5)
        #expect(TextZoom.clamped(.nan) == TextZoom.standard)
        #expect(TextZoom.clamped(.infinity) == TextZoom.standard)
    }

    @Test func stepsMoveOneAtATimeAndStopAtTheEnds() {
        #expect(TextZoom.larger(than: 1) == 1.1)
        #expect(TextZoom.smaller(than: 1) == 0.9)
        #expect(TextZoom.larger(than: 1.5) == 1.5)
        #expect(TextZoom.smaller(than: 0.75) == 0.75)
        // A zoom between steps moves to the step past it.
        #expect(TextZoom.larger(than: 0.95) == 1)
        #expect(TextZoom.smaller(than: 0.95) == 0.9)
    }

    @Test func pointSizesScaleButNotUnderTheFloor() {
        #expect(TextZoom.pointSize(28, at: 1) == 28)
        #expect(TextZoom.pointSize(20, at: 1.5) == 30)
        #expect(TextZoom.pointSize(28, at: 0.75) == 21)
        #expect(TextZoom.pointSize(12, at: 0.75) == 9)
        // 11pt and 10pt would go under 9pt; they stop there.
        #expect(TextZoom.pointSize(11, at: 0.75) == 9)
        #expect(TextZoom.pointSize(10, at: 0.8) == 9)
        // Text designed under the floor keeps its size rather than growing.
        #expect(TextZoom.pointSize(8, at: 0.75) == 8)
        #expect(TextZoom.pointSize(8, at: 1) == 8)
        // Zooming in grows it like everything else.
        #expect(TextZoom.pointSize(8, at: 1.5) == 12)
        // An out-of-range zoom is clamped first.
        #expect(TextZoom.pointSize(20, at: 0.1) == 15)
    }
}
