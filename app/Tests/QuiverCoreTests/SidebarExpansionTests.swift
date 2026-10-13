import Testing
@testable import QuiverCore

@Suite("Sidebar expansion")
struct SidebarExpansionTests {
    @Test func defaultsToExpandedAndPinned() {
        let e = SidebarExpansion()
        #expect(!e.collapsed)
        #expect(e.isExpanded)
        #expect(!e.isFloating)
    }

    @Test func collapsedRailExpandsOnHoverAsFloating() {
        var e = SidebarExpansion(collapsed: true)
        e.pointer(inside: true)
        #expect(e.isExpanded)
        #expect(e.isFloating)
    }

    @Test func collapsedWithoutHoverStaysARail() {
        let e = SidebarExpansion(collapsed: true)
        #expect(!e.isExpanded)
        #expect(!e.isFloating)
    }

    @Test func togglingClosedUnderThePointerSuppressesHover() {
        var e = SidebarExpansion()
        e.pointer(inside: true)
        e.toggle()
        #expect(e.collapsed)
        #expect(e.hoverSuppressed)
        #expect(!e.isExpanded)
        // Leaving once clears the suppression; re-entering expands.
        e.pointer(inside: false)
        #expect(!e.hoverSuppressed)
        e.pointer(inside: true)
        #expect(e.isExpanded)
        #expect(e.isFloating)
    }

    @Test func togglingWhileFloatingPinsItOpen() {
        var e = SidebarExpansion(collapsed: true)
        e.pointer(inside: true)
        #expect(e.isFloating)
        e.toggle()
        #expect(!e.collapsed)
        #expect(e.isExpanded)
        #expect(!e.isFloating)
    }

    @Test func hoverDoesNothingToAnExpandedSidebar() {
        var e = SidebarExpansion()
        e.pointer(inside: true)
        #expect(e.isExpanded)
        #expect(!e.isFloating)
    }
}
