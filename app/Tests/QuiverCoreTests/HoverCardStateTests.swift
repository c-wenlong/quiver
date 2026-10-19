import CoreGraphics
import Foundation
import Testing
@testable import QuiverCore

/// Stands in for the show and swap delays: a wait returns only once the
/// test releases it, so no test depends on wall-clock timing. A release
/// before the wait starts is kept for it.
@MainActor private final class DelayGate {
    private var permits = 0
    private var waiting: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if permits > 0 { permits -= 1; return }
        await withCheckedContinuation { waiting.append($0) }
    }

    func release() {
        if waiting.isEmpty { permits += 1 } else { waiting.removeFirst().resume() }
    }
}

@MainActor
@Suite struct HoverCardStateTests {
    private let gate = DelayGate()

    /// No view has reported a rect unless `card` is given, and the
    /// pointer sits at the origin.
    private func state(card: CGRect? = nil) -> HoverCardState {
        HoverCardState(pointer: { CGPoint(x: 10, y: 10) },
                       rects: { _ in (nil, card) },
                       wait: { [gate] _ in await gate.wait() },
                       pollWait: { _ in try? await Task.sleep(for: .seconds(3600)) })
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
}
