import Testing
@testable import SWECore

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

/// A test clock for `HoverExpansion`: `sleep` suspends until `advance`
/// carries a sleeper's deadline past, so the delayed hover is decided
/// by clock time alone, never by whether the main actor got a slot.
@MainActor
final class ManualClock {
    private var now = Duration.zero
    private var next = 0
    private var sleepers: [(id: Int, deadline: Duration,
                           resume: (Result<Void, Error>) -> Void)] = []
    /// How many sleeps have been registered, counting cancelled and
    /// finished ones: waitForSleepers keys on this so a stale sleeper
    /// never satisfies a wait meant for a newer task.
    private var registrations = 0
    /// Continuations waiting on `waitForSleepers`.
    private var watchers: [(count: Int, resume: () -> Void)] = []

    func sleep(_ delay: Duration) async throws {
        let deadline = now + delay
        let id = next
        next += 1
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
                sleepers.append((id, deadline, { cont.resume(with: $0) }))
                registrations += 1
                let ready = watchers.filter { registrations >= $0.count }
                watchers.removeAll { registrations >= $0.count }
                for watcher in ready { watcher.resume() }
            }
        } onCancel: { [weak self] in
            Task { @MainActor in self?.cancel(id) }
        }
    }

    /// Suspends until `count` sleeps have been registered in all, so the
    /// test knows the delayed hover task reached its sleep before the
    /// clock advances.
    func waitForSleepers(_ count: Int) async {
        if registrations >= count { return }
        await withCheckedContinuation { cont in
            watchers.append((count, { cont.resume() }))
        }
    }

    func advance(by delay: Duration) {
        now += delay
        let ready = sleepers.filter { $0.deadline <= now }
        sleepers.removeAll { $0.deadline <= now }
        for sleeper in ready { sleeper.resume(.success(())) }
    }

    private func cancel(_ id: Int) {
        guard let index = sleepers.firstIndex(where: { $0.id == id }) else { return }
        sleepers.remove(at: index).resume(.failure(CancellationError()))
    }
}

/// The delayed hover both sidebars run through, driven by a manual
/// clock and awaited pending tasks, so nothing waits on real time.
@MainActor
@Suite("Hover expansion")
struct HoverExpansionTests {
    /// enter 20, exit 40 units of clock time.
    private func rail(_ clock: ManualClock) -> HoverExpansion {
        HoverExpansion(collapsed: true,
                       enterDelay: .milliseconds(20),
                       exitDelay: .milliseconds(40),
                       sleep: { try await clock.sleep($0) })
    }

    /// Awaits the hover's pending task once the clock has passed its
    /// deadline.
    private func settle(_ hover: HoverExpansion) async {
        await hover.pendingTaskForTesting?.value
    }

    @Test func enteringACollapsedRailExpandsAfterTheDelay() async {
        let clock = ManualClock()
        let hover = rail(clock)
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(1)
        #expect(!hover.isExpanded)
        clock.advance(by: .milliseconds(19))
        #expect(!hover.isExpanded)
        clock.advance(by: .milliseconds(1))
        await settle(hover)
        #expect(hover.isExpanded)
        #expect(hover.isFloating)
    }

    @Test func repeatInsideReportsDoNotPostponeTheEntry() async {
        // A caller reporting on every pointer move (continuous hover)
        // re-sends the same `inside`; the entry delay must still count
        // from the first report, not the latest.
        let clock = ManualClock()
        let hover = rail(clock)
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(1)
        clock.advance(by: .milliseconds(15))
        hover.hover(true, collapsed: true)
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(1) // no new sleeper registered
        clock.advance(by: .milliseconds(5))
        await settle(hover)
        #expect(hover.isExpanded)
        #expect(hover.isFloating)
    }

    @Test func leavingBeforeTheEntryDelayNeverExpands() async {
        let clock = ManualClock()
        let hover = rail(clock)
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(1)
        hover.hover(false, collapsed: true)
        await clock.waitForSleepers(2)
        clock.advance(by: .milliseconds(200))
        await settle(hover)
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
    }

    @Test func leavingAFloatHidesItAndAReturnKeepsIt() async {
        let clock = ManualClock()
        let hover = rail(clock)
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(1)
        clock.advance(by: .milliseconds(20))
        await settle(hover)
        #expect(hover.isFloating)

        // Out for the full exit delay: hidden.
        hover.hover(false, collapsed: true)
        await clock.waitForSleepers(2)
        #expect(hover.isExpanded) // still shown during the delay
        clock.advance(by: .milliseconds(40))
        await settle(hover)
        #expect(!hover.isExpanded)

        // A return inside the exit delay cancels the hide.
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(3)
        clock.advance(by: .milliseconds(20))
        await settle(hover)
        #expect(hover.isFloating)
        hover.hover(false, collapsed: true)
        await clock.waitForSleepers(4)
        clock.advance(by: .milliseconds(10))
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(5)
        clock.advance(by: .milliseconds(20))
        await settle(hover)
        // The exit delay that was pending when the pointer returned can
        // not hide it; the re-entry's delay has already run too.
        #expect(hover.isExpanded)
        #expect(hover.isFloating)
        // Still expanded after the old exit deadline has passed.
        clock.advance(by: .milliseconds(30))
        await settle(hover)
        #expect(hover.isExpanded)
    }

    @Test func togglingWithThePointerInsideCollapsesAndStays() async {
        // Pinned open with the pointer inside: the toggle collapses and
        // suppresses the hover expansion it would otherwise trigger, so
        // advancing past the entry delay never re-opens the rail.
        let clock = ManualClock()
        let hover = HoverExpansion(collapsed: false,
                                   enterDelay: .milliseconds(20),
                                   exitDelay: .milliseconds(40),
                                   sleep: { try await clock.sleep($0) })
        hover.hover(true, collapsed: false)
        hover.toggle()
        #expect(hover.expansion.collapsed)
        #expect(!hover.isExpanded)
        clock.advance(by: .milliseconds(80))
        await settle(hover)
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
    }

    @Test func togglingWhileAnEntryIsPendingCancelsIt() async {
        // The rail's collapse landing between an enter and its delay
        // settles the pending hover first; no late expand re-opens it.
        let clock = ManualClock()
        let hover = rail(clock)
        hover.hover(true, collapsed: true)
        await clock.waitForSleepers(1)
        hover.toggle()
        #expect(!hover.expansion.collapsed)
        #expect(hover.isExpanded)
        hover.toggle()
        #expect(hover.expansion.collapsed)
        #expect(!hover.isExpanded)
        clock.advance(by: .milliseconds(80))
        await settle(hover)
        #expect(!hover.isExpanded)
        #expect(!hover.isFloating)
    }

    @Test func hoverOnAPinnedOpenPanelChangesNothing() async {
        let clock = ManualClock()
        let hover = HoverExpansion(collapsed: false,
                                   enterDelay: .milliseconds(20),
                                   exitDelay: .milliseconds(20),
                                   sleep: { try await clock.sleep($0) })
        hover.hover(true, collapsed: false)
        #expect(hover.isExpanded)
        #expect(!hover.isFloating)
        hover.hover(false, collapsed: false)
        clock.advance(by: .milliseconds(60))
        await settle(hover)
        #expect(hover.isExpanded)
        #expect(!hover.isFloating)
    }
}
