import AppKit
import Testing
@testable import SWEApp

/// ⌘W/⌘Q must not die on a popup: sheets and panels own key status
/// while they're up, so the menu's Close/Quit never reach the window.
@Suite @MainActor struct PopupKeyRoutingTests {
    private func panel() -> NSPanel {
        NSPanel(contentRect: .zero, styleMask: [.borderless],
                backing: .buffered, defer: true)
    }

    @Test func cmdWOnAPanelEscapes() {
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command],
                                     key: panel()) == .sendEscape)
    }

    @Test func cmdQOnAPanelQuits() {
        #expect(PopupKeyRouting.action(chars: "q", flags: [.command],
                                     key: panel()) == .quit)
    }

    /// The quit alert is itself an app-modal panel: quitting from inside its
    /// run would ask again and stack a second alert.
    @Test func cmdQDuringAModalRunPassesThrough() {
        #expect(PopupKeyRouting.action(chars: "q", flags: [.command],
                                     key: panel(), inModal: true) == .passThrough)
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command],
                                     key: panel(), inModal: true) == .sendEscape)
    }

    @Test func escOnTheMenuBarPanelCloses() {
        let bar = MenuBarPanel(contentViewController: NSViewController())
        var closed = false
        bar.onEscape = { closed = true }
        bar.keyDown(with: NSEvent.keyEvent(
            with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
            windowNumber: 0, context: nil, characters: "\u{1b}",
            charactersIgnoringModifiers: "\u{1b}", isARepeat: false,
            keyCode: 53)!)
        #expect(closed)
    }

    @Test func cmdWOnAChildWindowEscapes() {
        let parent = NSWindow(contentRect: .zero, styleMask: [.titled],
                              backing: .buffered, defer: true)
        let popover = NSWindow(contentRect: .zero, styleMask: [.borderless],
                               backing: .buffered, defer: true)
        parent.addChildWindow(popover, ordered: .above)
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command],
                                     key: popover) == .sendEscape)
    }

    @Test func cmdWOnARealWindowPassesThrough() {
        let window = NSWindow(contentRect: .zero, styleMask: [.titled],
                              backing: .buffered, defer: true)
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command],
                                     key: window) == .passThrough)
        #expect(PopupKeyRouting.action(chars: "q", flags: [.command],
                                     key: window) == .passThrough)
    }

    @Test func otherKeysAndModifiersPassThrough() {
        let key = panel()
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command, .shift],
                                     key: key) == .passThrough)
        #expect(PopupKeyRouting.action(chars: "r", flags: [.command],
                                     key: key) == .passThrough)
        #expect(PopupKeyRouting.action(chars: "w", flags: [],
                                     key: key) == .passThrough)
        #expect(PopupKeyRouting.action(chars: "w", flags: [.command],
                                     key: nil) == .passThrough)
    }
}
