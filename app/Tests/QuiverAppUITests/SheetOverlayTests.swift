import AppKit
import Testing
@testable import QuiverApp

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

    @Test func cmdWOnACoveredWindowClosesTheCard() {
        let main = window()
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command], key: main) == .passThrough)
        SheetOverlayRegistry.cover(main)
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command], key: main) == .sendEscape)
        // A nested card keeps the window covered until the last one closes.
        SheetOverlayRegistry.cover(main)
        SheetOverlayRegistry.uncover(main)
        #expect(SheetOverlayRegistry.isCovered(main))
        SheetOverlayRegistry.uncover(main)
        #expect(!SheetOverlayRegistry.isCovered(main))
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command], key: main) == .passThrough)
    }
}
