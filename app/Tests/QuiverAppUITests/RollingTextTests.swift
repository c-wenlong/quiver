import AppKit
import Foundation
import Testing
@testable import QuiverApp

/// `RollingTextView` rolls on Core Animation and tidies up in a
/// transaction's completion block, so these run the main run loop past a
/// roll's end. The view sits in a window: without one it doesn't animate.
@Suite(.serialized)
@MainActor
struct RollingTextTests {
    let style = RollingTextView.Style(font: .monospacedDigitSystemFont(ofSize: 72, weight: .bold),
                                      color: .white, kern: -1.8)
    /// Past the longest roll: duration plus the stagger across 5 characters.
    let settled: TimeInterval = RollingTextView.duration + 6 * RollingTextView.stagger + 0.3

    func hosted() -> (NSWindow, RollingTextView) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 100),
                              styleMask: [.titled], backing: .buffered, defer: false)
        let view = RollingTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 90))
        window.contentView?.addSubview(view)
        return (window, view)
    }

    func spin(_ seconds: TimeInterval) {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end { RunLoop.main.run(until: Date().addingTimeInterval(0.01)) }
    }

    @Test func longerToShorterEndsWithOneBoxPerCharacter() {
        let (window, view) = hosted()
        view.show("35.3B", style: style, perCharacter: true, up: false)
        view.show("7.0B", style: style, perCharacter: true, up: false)
        #expect(view.shownText == "7.0B")
        spin(settled)
        #expect(view.shownText == "7.0B")
        #expect(view.boxCount == 4)
        #expect(view.glyphCount == 4)
        #expect(view.glyphOpacities.allSatisfy { $0 == 1 })
        _ = window
    }

    @Test func shorterToLongerAddsBoxes() {
        let (window, view) = hosted()
        view.show("7.0B", style: style, perCharacter: true, up: true)
        view.show("35.3B", style: style, perCharacter: true, up: true)
        spin(settled)
        #expect(view.shownText == "35.3B")
        #expect(view.boxCount == 5)
        #expect(view.glyphCount == 5)
        _ = window
    }

    @Test func aChangeMidRollWinsAndTheOlderCleanupLeavesItAlone() {
        let (window, view) = hosted()
        view.show("35.3B", style: style, perCharacter: true, up: false)
        view.show("22.8B", style: style, perCharacter: true, up: false)
        spin(0.05)
        view.show("7.0B", style: style, perCharacter: true, up: false)
        // The first roll's completion comes due in here; it must not tidy
        // away the second roll's glyphs.
        spin(settled)
        #expect(view.shownText == "7.0B")
        #expect(view.boxCount == 4)
        #expect(view.glyphCount == 4)
        #expect(view.glyphOpacities.allSatisfy { $0 == 1 })
        _ = window
    }

    @Test func aLabelRollsAsOneLine() {
        let (window, view) = hosted()
        let caption = RollingTextView.Style(font: .systemFont(ofSize: 12), color: .white, kern: 0)
        view.show("Total tokens", style: caption, perCharacter: false, up: false)
        view.show("Claude Code tokens", style: caption, perCharacter: false, up: false)
        spin(settled)
        #expect(view.shownText == "Claude Code tokens")
        #expect(view.boxCount == 1)
        #expect(view.glyphCount == 1)
        _ = window
    }

    @Test func theSameTextAgainChangesNothing() {
        let (window, view) = hosted()
        view.show("35.3B", style: style, perCharacter: true, up: true)
        view.show("35.3B", style: style, perCharacter: true, up: true)
        #expect(view.boxCount == 5)
        #expect(view.glyphCount == 5)
        _ = window
    }
}
