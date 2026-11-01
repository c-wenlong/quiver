import AppKit
import SwiftUI
import Testing
@testable import SWEApp

/// A `quiverSheet` card lives in the main window, so Esc and ⌘W have to
/// reach it the way they reached a system sheet.
@Suite @MainActor struct SheetOverlayTests {
    private func window() -> NSWindow {
        NSWindow(contentRect: .zero, styleMask: [.titled], backing: .buffered, defer: true)
    }

    @Test func escClosesTheTopCard() {
        #expect(SheetKeys.closesOnEscape(keyCode: 53, flags: [], isTop: true, modal: false, composing: false))
        // Caps Lock and the fn flag arrow keys carry are not modifiers here.
        #expect(SheetKeys.closesOnEscape(keyCode: 53, flags: [.capsLock], isTop: true, modal: false, composing: false))
    }

    @Test func escLeavesOtherCardsAndKeysAlone() {
        // A card under another card waits for the top one.
        #expect(!SheetKeys.closesOnEscape(keyCode: 53, flags: [], isTop: false, modal: false, composing: false))
        // A modal card (first-run setup) closes only from its own buttons.
        #expect(!SheetKeys.closesOnEscape(keyCode: 53, flags: [], isTop: true, modal: true, composing: false))
        // Esc mid-composition belongs to the input method.
        #expect(!SheetKeys.closesOnEscape(keyCode: 53, flags: [], isTop: true, modal: false, composing: true))
        #expect(!SheetKeys.closesOnEscape(keyCode: 53, flags: [.command], isTop: true, modal: false, composing: false))
        #expect(!SheetKeys.closesOnEscape(keyCode: 36, flags: [], isTop: true, modal: false, composing: false))
    }

    @Test func aNestedCardKeepsTheWindowCoveredUntilTheLastCloses() {
        let main = window()
        SheetOverlayRegistry.cover(main)
        SheetOverlayRegistry.cover(main)
        SheetOverlayRegistry.uncover(main)
        #expect(SheetOverlayRegistry.isCovered(main))
        SheetOverlayRegistry.uncover(main)
        #expect(!SheetOverlayRegistry.isCovered(main))
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command], key: main) == .passThrough)
    }

    /// A real card in a real window: ⌘W goes through PopupKeyRouting, its
    /// Esc reaches the card's key monitor, the presentation binding goes
    /// false, and the window stays open. The test process has no key
    /// window, so the routing gets the window directly; the rest is the
    /// path the app runs.
    @Test func cmdWClosesAHostedCardAndKeepsTheWindow() {
        _ = NSApplication.shared
        let flag = Flag()
        let main = OffscreenWindow(size: CGSize(width: 600, height: 400),
                                   styleMask: [.titled, .closable])
        main.isReleasedWhenClosed = false
        main.contentView = NSHostingView(rootView: Host(flag: flag))
        main.orderFront(nil)
        defer { main.orderOut(nil) }
        #expect(main.screen == nil)

        #expect(spin { SheetOverlayRegistry.isCovered(main) })
        let action = PopupKeyRouting.action(chars: "w", flags: [.command], key: main)
        #expect(action == .sendEscape)
        #expect(PopupKeyRouting.perform(action, key: main))

        #expect(spin { !flag.isPresented && !SheetOverlayRegistry.isCovered(main) })
        #expect(!flag.isPresented)
        #expect(!SheetOverlayRegistry.isCovered(main))
        #expect(main.isVisible)
    }

    /// Runs the main run loop until `done` holds, true if it did. SwiftUI
    /// mounts the card and AppKit delivers the Esc only from run loop
    /// turns, and a synchronous test on the main thread has no other way
    /// to give it those turns, so this loop stays. Each turn returns as
    /// soon as the run loop handles a source, and the bound is sized for a
    /// starved machine, like the core tests' `gateTimeout`: a passing test
    /// never comes near it.
    private func spin(timeout: TimeInterval = 60, until done: () -> Bool) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while !done() && Date() < deadline {
            RunLoop.main.run(mode: .default, before: Date().addingTimeInterval(0.02))
        }
        return done()
    }
}

@MainActor @Observable private final class Flag {
    var isPresented = true
}

private struct Host: View {
    @Bindable var flag: Flag

    var body: some View {
        Color.clear
            .frame(width: 600, height: 400)
            .quiverSheet(isPresented: $flag.isPresented) {
                Text("A card").frame(width: 200, height: 100)
            }
            .sheetHost()
    }
}
