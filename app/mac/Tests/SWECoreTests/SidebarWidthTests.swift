import Testing
@testable import SWECore

@Suite struct SidebarWidthTests {
    @Test func standardSitsInsideTheRange() {
        #expect(SidebarWidth.range.contains(SidebarWidth.standard))
    }

    @Test func widthsClampToTheRange() {
        #expect(SidebarWidth.clamped(0) == SidebarWidth.range.lowerBound)
        #expect(SidebarWidth.clamped(120) == SidebarWidth.range.lowerBound)
        #expect(SidebarWidth.clamped(200) == 200)
        #expect(SidebarWidth.clamped(400) == SidebarWidth.range.upperBound)
        #expect(SidebarWidth.clamped(.infinity) == SidebarWidth.range.upperBound)
    }

    @Test func stepsMoveByTheStepAndClampAtTheBounds() {
        #expect(SidebarWidth.clamped(SidebarWidth.standard + SidebarWidth.step) == 216)
        #expect(SidebarWidth.clamped(SidebarWidth.standard - SidebarWidth.step) == 176)
        #expect(SidebarWidth.clamped(SidebarWidth.range.upperBound + SidebarWidth.step)
            == SidebarWidth.range.upperBound)
        #expect(SidebarWidth.clamped(SidebarWidth.range.lowerBound - SidebarWidth.step)
            == SidebarWidth.range.lowerBound)
    }
}
