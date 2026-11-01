import CoreGraphics
import Foundation
import Testing
@testable import SWECore

/// Stands in for the show and swap delays: a wait returns only once the
/// test releases it, so no test depends on wall-clock timing. A release
/// before the wait starts is kept for it.
@MainActor private final class DelayGate {
    private var permits = 0
    private var waiting: [CheckedContinuation<Void, Never>] = []
    /// Every wait asked for, in order.
    private(set) var requested: [Duration] = []

    func wait(_ duration: Duration) async {
        requested.append(duration)
        if permits > 0 { permits -= 1; return }
        await withCheckedContinuation { waiting.append($0) }
    }

    func release() {
        if waiting.isEmpty { permits += 1 } else { waiting.removeFirst().resume() }
    }
}

/// Stands in for the pointer poller's tick: each call parks until the
/// test releases it, and `reached` lets the test wait until the loop has
/// asked for its nth tick, so a poll can be stepped one at a time.
@MainActor private final class PollGate {
    private(set) var count = 0
    private var permits = 0
    private var waiting: [CheckedContinuation<Void, Never>] = []
    private var observers: [CheckedContinuation<Void, Never>] = []

    func wait(_ duration: Duration) async {
        count += 1
        while !observers.isEmpty { observers.removeFirst().resume() }
        if permits > 0 { permits -= 1; return }
        await withCheckedContinuation { waiting.append($0) }
    }

    func release() {
        if waiting.isEmpty { permits += 1 } else { waiting.removeFirst().resume() }
    }

    /// Resolves once the poller has asked for its nth tick.
    func reached(_ n: Int) async {
        while count < n {
            await withCheckedContinuation { observers.append($0) }
        }
    }
}

/// Counts `animate` calls and lets a test wait for the next open or
/// close to run.
@MainActor private final class AnimLog {
    private(set) var count = 0
    private var waiting: [CheckedContinuation<Void, Never>] = []

    func call() {
        count += 1
        while !waiting.isEmpty { waiting.removeFirst().resume() }
    }

    /// Resolves once the nth animated change has run.
    func reached(_ n: Int) async {
        while count < n {
            await withCheckedContinuation { waiting.append($0) }
        }
    }
}

@MainActor
@Suite struct HoverCardStateTests {
    private let gate = DelayGate()

    /// A clock the test moves by hand.
    private final class Clock: @unchecked Sendable {
        var now = ContinuousClock.now
    }
    private let clock = Clock()

    /// No view has reported a rect unless `card` is given, and the
    /// pointer sits at the origin.
    private func state(card: CGRect? = nil) -> HoverCardState {
        HoverCardState(pointer: { CGPoint(x: 10, y: 10) },
                       rects: { _ in (nil, card) },
                       wait: { [gate] in await gate.wait($0) },
                       pollWait: { _ in try? await Task.sleep(for: .seconds(3600)) },
                       clock: { [clock] in clock.now })
    }

    @Test func passingOverANeighbourKeepsTheOpenCard() async {
        let state = state()
        state.open("a")
        state.hover("b", inside: true)
        state.hover("b", inside: false) // gone before the swap fires
        gate.release()
        await state.settled()
        #expect(state.key == "a")
    }

    @Test func restingOnANeighbourSwapsAfterTheWait() async {
        let state = state()
        state.open("a")
        state.hover("b", inside: true)
        #expect(state.key == "a") // never on entry
        gate.release()
        await state.settled()
        #expect(state.key == "b")
    }

    @Test func leavingTheOldAnchorAfterEnteringTheNewKeepsTheSwap() async {
        // Exit and enter events can arrive in either order: the old
        // anchor's exit must not drop the new anchor's pending swap.
        let state = state()
        state.open("a")
        state.hover("b", inside: true)
        state.hover("a", inside: false)
        gate.release()
        await state.settled()
        #expect(state.key == "b")
    }

    @Test func aLateCloseOfASwappedAwayCardKeepsItsReplacement() async {
        let state = state()
        state.open("a")
        state.hover("b", inside: true)
        gate.release()
        await state.settled()
        state.closed("a") // AppKit closing the old popover after the swap
        #expect(state.key == "b")
        state.closed("b") // the open card's own close, pointer outside
        #expect(state.key == nil)
    }

    @Test func aCloseWithThePointerOnTheCardIsIgnored() {
        let state = state(card: CGRect(x: 0, y: 0, width: 100, height: 100))
        state.open("a")
        state.closed("a")
        #expect(state.key == "a")
    }

    @Test func thePollerClosesTheCardOnlyAfterTheGrace() async {
        var point = CGPoint(x: 50, y: 50)
        let poll = PollGate()
        let anim = AnimLog()
        let state = HoverCardState(
            pointer: { point },
            rects: { _ in (CGRect(x: 0, y: 0, width: 20, height: 20),
                           CGRect(x: 40, y: 40, width: 20, height: 20)) },
            wait: { [gate] in await gate.wait($0) },
            pollWait: { [poll] in await poll.wait($0) },
            clock: { [clock] in clock.now })
        state.animate = { body in body(); anim.call() }

        state.open("a")
        await poll.reached(1) // first tick ran with the pointer inside
        #expect(state.key == "a")

        point = CGPoint(x: 500, y: 500) // off the anchor and the card
        poll.release()
        await poll.reached(2)
        #expect(state.key == "a") // the grace has only just started

        clock.now += .milliseconds(100) // inside the 150 ms grace
        poll.release()
        await poll.reached(3)
        #expect(state.key == "a")

        clock.now += .milliseconds(100) // 200 ms outside: past the grace
        poll.release()
        await anim.reached(2) // open was the first animated change
        #expect(state.key == nil)
    }

    @Test func aScrollClosesTheOpenCardAndHoldsNewOnes() async {
        let state = state()
        state.open("a")
        state.scrollBegan("days")
        #expect(state.key == nil)
        state.hover("b", inside: true) // the grid moves under the pointer
        gate.release()
        await state.settled()
        #expect(state.key == nil)
        #expect(gate.requested.isEmpty)
    }

    @Test func theBlockUnderThePointerOpensASecondAfterTheScrollSettles() async {
        let state = state()
        state.scrollBegan("days")
        state.hover("b", inside: true)
        state.scrollSettled("days")
        gate.release()
        await state.settled()
        // No pointer move needed: the card under it opened after a full
        // second's wait.
        #expect(gate.requested == [HoverCardTiming.afterScroll])
        #expect(state.key == "b")
    }

    @Test func aHoverJustAfterAScrollWaitsOutTheQuiet() async {
        let state = state()
        state.scrollBegan("days")
        state.scrollSettled("days")
        clock.now += .milliseconds(200)
        state.hover("c", inside: true)
        gate.release()
        await state.settled()
        // 800 ms of quiet left beats the usual 350 ms show delay.
        #expect(gate.requested == [.milliseconds(800)])
        // Well after the quiet, with no card up, the usual show delay is
        // back.
        clock.now += .seconds(5)
        state.dismiss()
        state.hover("d", inside: true)
        gate.release()
        await state.settled()
        #expect(gate.requested.last == HoverCardTiming.show)
    }

    @Test func cardsStayQuietUntilEveryScrollViewRests() async {
        let state = state()
        state.scrollBegan("days")
        state.scrollBegan("hours")
        state.scrollSettled("hours")
        state.hover("b", inside: true) // the days are still moving
        #expect(gate.requested.isEmpty)
        state.scrollSettled("days")
        gate.release()
        await state.settled()
        #expect(gate.requested == [HoverCardTiming.afterScroll])
        #expect(state.key == "b")
    }

    @Test func aChipTapStillOpensAtOnceRightAfterAScroll() {
        let state = state()
        state.scrollBegan("days")
        state.scrollSettled("days")
        state.open("chip") // a deliberate click, not a passing pointer
        #expect(state.key == "chip")
    }

    @Test func removingAMovingGridUnblocksCards() async {
        // Month view replaced the grid mid-swipe: neither scroll view
        // reports coming to rest, so their removal has to clear them.
        let state = state()
        state.scrollBegan("days")
        state.scrollBegan("hours")
        state.scrollsRemoved()
        state.hover("entry", inside: true)
        gate.release()
        await state.settled()
        #expect(gate.requested == [HoverCardTiming.show])
        #expect(state.key == "entry")
    }
}
