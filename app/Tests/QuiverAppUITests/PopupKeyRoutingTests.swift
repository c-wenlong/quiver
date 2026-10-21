import AppKit
import Testing
@testable import QuiverApp

/// ⌘W/⌘Q must not die on a popup: sheets and panels own key status
/// while they're up, so the menu's Close/Quit never reach the window.
@Suite @MainActor struct PopupKeyRoutingTests {
    private func key(_ chars: String, _ modifiers: NSEvent.ModifierFlags = [.command]) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero,
                         modifierFlags: modifiers, timestamp: 0,
                         windowNumber: 0, context: nil,
                         characters: chars,
                         charactersIgnoringModifiers: chars,
                         isARepeat: false, keyCode: 0)!
    }

    @Test func cmdWOnAPanelDismissesIt() {
        let panel = NSPanel(contentRect: .zero, styleMask: [.borderless],
                            backing: .buffered, defer: true)
        #expect(PopupKeyRouting.action(for: key("w"), key: panel) == .dismissPopup)
    }

    @Test func cmdQOnAPanelQuits() {
        let panel = NSPanel(contentRect: .zero, styleMask: [.borderless],
                            backing: .buffered, defer: true)
        #expect(PopupKeyRouting.action(for: key("q"), key: panel) == .quit)
    }

    @Test func cmdWOnAChildWindowDismissesIt() {
        let parent = NSWindow(contentRect: .zero, styleMask: [.titled],
                              backing: .buffered, defer: true)
        let popover = NSWindow(contentRect: .zero, styleMask: [.borderless],
                               backing: .buffered, defer: true)
        parent.addChildWindow(popover, ordered: .above)
        #expect(PopupKeyRouting.action(for: key("w"), key: popover) == .dismissPopup)
    }

    @Test func cmdWOnARealWindowPassesThrough() {
        let window = NSWindow(contentRect: .zero, styleMask: [.titled],
                              backing: .buffered, defer: true)
        #expect(PopupKeyRouting.action(for: key("w"), key: window) == .passThrough)
        #expect(PopupKeyRouting.action(for: key("q"), key: window) == .passThrough)
    }

    @Test func otherKeysAndModifiersPassThrough() {
        let panel = NSPanel(contentRect: .zero, styleMask: [.borderless],
                            backing: .buffered, defer: true)
        #expect(PopupKeyRouting.action(for: key("w", [.command, .shift]), key: panel) == .passThrough)
        #expect(PopupKeyRouting.action(for: key("r"), key: panel) == .passThrough)
        #expect(PopupKeyRouting.action(for: key("w", []), key: panel) == .passThrough)
        #expect(PopupKeyRouting.action(for: key("w"), key: nil) == .passThrough)
    }
}
