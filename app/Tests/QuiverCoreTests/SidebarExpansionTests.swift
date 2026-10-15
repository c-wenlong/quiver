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

/// The delayed hover both sidebars run through, with short injected
/// delays so the suite stays fast.
@MainActor
@Suite("Hover expansion")
struct HoverExpansionTests {
    /// enter 20 ms, exit 40 ms; sleeps run comfortably past each delay
    /// while staying well under real timers' jitter.
    private func rail() -> HoverExpansion {
        HoverExpansion(collapsed: true,
                       enterDelay: .milliseconds(20),
                       exitDelay: .milliseconds(40))
    }

    @Test func enteringACollapsedRailExpandsAfterTheDelay() async {
        let hover = rail()
        hover.hover(true, collapsed: true)
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
        try? await Task.sleep(for: .milliseconds(60))
        #expect(hover.isExpanded)
        #expect(hover.isFloating)
    }

    @Test func leavingBeforeTheEntryDelayNeverExpands() async {
        let hover = rail()
        hover.hover(true, collapsed: true)
        hover.hover(false, collapsed: true)
        try? await Task.sleep(for: .milliseconds(60))
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
    }

    @Test func leavingAFloatHidesItAndAReturnKeepsIt() async {
        let hover = rail()
        hover.hover(true, collapsed: true)
        try? await Task.sleep(for: .milliseconds(60))
        #expect(hover.isFloating)

        // Out for the full exit delay: hidden.
        hover.hover(false, collapsed: true)
        #expect(hover.isExpanded) // still shown during the delay
        try? await Task.sleep(for: .milliseconds(80))
        #expect(!hover.isExpanded)

        // A return inside the exit delay cancels the hide.
        hover.hover(true, collapsed: true)
        try? await Task.sleep(for: .milliseconds(60))
        #expect(hover.isFloating)
        hover.hover(false, collapsed: true)
        try? await Task.sleep(for: .milliseconds(20))
        hover.hover(true, collapsed: true)
        try? await Task.sleep(for: .milliseconds(60))
        #expect(hover.isExpanded)
        #expect(hover.isFloating)
    }

    @Test func togglingWithThePointerInsideCollapsesAndStays() async {
        // Pinned open with the pointer inside: the toggle collapses and
        // suppresses the hover expansion it would otherwise trigger, so
        // sleeping past the entry delay never re-opens the rail.
        let hover = HoverExpansion(collapsed: false,
                                   enterDelay: .milliseconds(20),
                                   exitDelay: .milliseconds(40))
        hover.hover(true, collapsed: false)
        hover.toggle()
        #expect(hover.expansion.collapsed)
        #expect(!hover.isExpanded)
        try? await Task.sleep(for: .milliseconds(80))
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
    }

    @Test func togglingWhileAnEntryIsPendingCancelsIt() async {
        // The rail's collapse landing between an enter and its delay
        // settles the pending hover first; no late expand re-opens it.
        let hover = rail()
        hover.hover(true, collapsed: true)
        hover.toggle()
        #expect(!hover.expansion.collapsed)
        #expect(hover.isExpanded)
        hover.toggle()
        #expect(hover.expansion.collapsed)
        #expect(!hover.isExpanded)
        try? await Task.sleep(for: .milliseconds(80))
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
    }

    @Test func hoverOnAPinnedOpenPanelChangesNothing() async {
        let hover = HoverExpansion(collapsed: false,
                                   enterDelay: .milliseconds(20),
                                   exitDelay: .milliseconds(20))
        hover.hover(true, collapsed: false)
        #expect(hover.isExpanded)
        #expect(!hover.isFloating)
        hover.hover(false, collapsed: false)
        try? await Task.sleep(for: .milliseconds(60))
        #expect(hover.isExpanded)
        #expect(!hover.isFloating)
    }
}
