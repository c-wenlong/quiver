import Testing
@testable import QuiverCore

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
}
