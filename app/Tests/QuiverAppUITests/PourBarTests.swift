import AppKit
import Foundation
import Testing
@testable import QuiverApp

/// The pill fills in the logo row's order, so each colour sits over the
/// logo it folds into, and its split hands over to the logos once.
@Suite(.serialized)
@MainActor
struct PourBarTests {
    /// Three logos' row: 3 × 30pt circles, 6pt apart.
    let row = CGFloat(3) * PourBarView.circle + 2 * PourBarView.gap

    func hosted() -> (NSWindow, PourBarView) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 300, height: 100),
                              styleMask: [.titled], backing: .buffered, defer: false)
        let view = PourBarView(frame: NSRect(x: 0, y: 0, width: row, height: PourBarView.circle))
        window.contentView?.addSubview(view)
        view.layoutSubtreeIfNeeded()
        return (window, view)
    }

    func spin(_ seconds: TimeInterval) {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
    }

    @Test func fillsLeftToRightInTheLogosOrder() {
        let (window, view) = hosted()
        view.begin(order: ["claude", "codex", "devin"])
        view.fill(order: ["claude", "codex", "devin"], landed: ["claude": 4, "devin": 2, "codex": 2], total: 8)
        let frames = view.segmentFrames
        #expect(frames.map(\.harness) == ["claude", "codex", "devin"])
        #expect(frames[0].frame.minX == 0)
        #expect(frames[1].frame.minX == frames[0].frame.maxX)
        #expect(frames[2].frame.minX == frames[1].frame.maxX)
        #expect(abs(frames[0].frame.width - row / 2) <= 1)
        #expect(abs(frames[2].frame.maxX - row) <= 1)
        _ = window
    }

    @Test func aHarnessWithNothingLandedYetTakesNoRoom() {
        let (window, view) = hosted()
        view.begin(order: ["claude", "codex", "devin"])
        view.fill(order: ["claude", "codex", "devin"], landed: ["claude": 1, "devin": 1], total: 8)
        let frames = view.segmentFrames
        #expect(frames[1].frame.width == 0)
        #expect(frames[2].frame.minX == frames[0].frame.maxX)
        _ = window
    }

    @Test func splitPutsEachColourOnItsLogoAndHandsOverOnce() {
        let (window, view) = hosted()
        view.begin(order: ["claude", "codex", "devin"])
        view.fill(order: ["claude", "codex", "devin"], landed: ["claude": 4, "codex": 2, "devin": 2], total: 8)
        var handedOver = 0
        view.split { handedOver += 1 }
        let frames = view.segmentFrames
        for (index, entry) in frames.enumerated() {
            #expect(entry.frame == CGRect(x: CGFloat(index) * (PourBarView.circle + PourBarView.gap), y: 0,
                                          width: PourBarView.circle, height: PourBarView.circle))
        }
        #expect(handedOver == 0)
        spin(PourBarView.splitDuration + 3 * PourBarView.splitStagger + 0.3)
        #expect(handedOver == 1)
        _ = window
    }

    @Test func aWrappedRowFadesAcrossInsteadOfFolding() {
        let (window, view) = hosted()
        view.frame.size.height = 2 * PourBarView.circle + PourBarView.gap
        view.begin(order: ["claude", "codex", "devin"])
        var handedOver = 0
        view.split { handedOver += 1 }
        #expect(handedOver == 1)
        _ = window
    }
}
