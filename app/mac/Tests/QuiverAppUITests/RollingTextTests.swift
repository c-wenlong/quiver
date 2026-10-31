import AppKit
import Foundation
import Testing
@testable import QuiverApp

/// `RollingTextView` rolls on Core Animation and tidies up on a timer of
/// the roll's length; the tests fire those timers by hand. The view sits
/// in a window: without one it doesn't animate.
@Suite(.serialized)
@MainActor
struct RollingTextTests {
    let style = RollingTextView.Style(font: .monospacedDigitSystemFont(ofSize: 72, weight: .bold),
                                      color: .white, kern: -1.8)
    let timers = ManualTimers()

    func hosted() -> (NSWindow, RollingTextView) {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 100),
                              styleMask: [.titled], backing: .buffered, defer: false)
        let view = RollingTextView(frame: NSRect(x: 0, y: 0, width: 400, height: 90))
        view.schedule = timers.timer
        window.contentView?.addSubview(view)
        return (window, view)
    }

    @Test func longerToShorterEndsWithOneBoxPerCharacter() {
        let (window, view) = hosted()
        view.show("35.3B", style: style, perCharacter: true, up: false)
        view.show("7.0B", style: style, perCharacter: true, up: false)
        #expect(view.shownText == "7.0B")
        timers.fireAll()
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
        timers.fireAll()
        #expect(view.shownText == "35.3B")
        #expect(view.boxCount == 5)
        #expect(view.glyphCount == 5)
        _ = window
    }

    @Test func aChangeMidRollWinsAndTheOlderCleanupLeavesItAlone() {
        // Reduce Motion turns rolls off, and these count the rolls' timers.
        guard !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        let (window, view) = hosted()
        view.show("35.3B", style: style, perCharacter: true, up: false)
        view.show("22.8B", style: style, perCharacter: true, up: false)
        view.show("7.0B", style: style, perCharacter: true, up: false)
        // The first roll's tidy comes due too; it must not tidy away the
        // second roll's glyphs.
        #expect(timers.count == 2)
        timers.fireAll()
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
        timers.fireAll()
        #expect(view.shownText == "Claude Code tokens")
        #expect(view.boxCount == 1)
        #expect(view.glyphCount == 1)
        _ = window
    }

    @Test func theSameTextAgainChangesNothing() {
        // Reduce Motion turns rolls off, and these count the rolls' timers.
        guard !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        let (window, view) = hosted()
        view.show("35.3B", style: style, perCharacter: true, up: true)
        view.show("7.0B", style: style, perCharacter: true, up: true)
        #expect(timers.count == 1)
        timers.fireAll()
        // The same text again starts no roll: no tidy is set, and every
        // glyph stays put with no animation on it.
        view.show("7.0B", style: style, perCharacter: true, up: true)
        #expect(timers.count == 0)
        #expect(view.boxCount == 4)
        #expect(view.glyphCount == 4)
        #expect(view.glyphOpacities.allSatisfy { $0 == 1 })
        _ = window
    }
}
