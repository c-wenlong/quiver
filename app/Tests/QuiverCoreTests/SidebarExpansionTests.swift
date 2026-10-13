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

    @Test func aQuickReturnAfterSuppressionStillExpands() {
        var e = SidebarExpansion()
        e.pointer(inside: true)
        e.toggle()
        // The pointer left and came back before the delayed exit ran:
        // pointerLeft records the leave, the re-entry clears suppression.
        e.pointerLeft()
        e.pointer(inside: true)
        #expect(e.isExpanded)
        #expect(e.isFloating)
    }

    @Test func leavingWhileSuppressedDoesNotFlashOpen() {
        var e = SidebarExpansion()
        e.pointer(inside: true)
        e.toggle()
        // The immediate exit call alone must not change what shows.
        e.pointerLeft()
        #expect(!e.isExpanded)
        #expect(e.hoverSuppressed)
    }

    @Test func suppressionHoldsWhileThePointerNeverLeft() {
        var e = SidebarExpansion()
        e.pointer(inside: true)
        e.toggle()
        // No leave happened, so a repeat enter event stays suppressed.
        e.pointer(inside: true)
        #expect(e.hoverSuppressed)
        #expect(!e.isExpanded)
    }

    @Test func hoverDoesNothingToAnExpandedSidebar() {
        var e = SidebarExpansion()
        e.pointer(inside: true)
        #expect(e.isExpanded)
        #expect(!e.isFloating)
    }
}
