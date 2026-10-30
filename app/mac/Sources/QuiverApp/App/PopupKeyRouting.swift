import AppKit

/// System sheets, alerts and panels own key-window status while they're up, so
/// ⌘W/⌘Q delivered to them never reaches the real window's Close or the
/// app menu's Quit — a popup looked like it blocked both. The monitor
/// answers the same way the menu would: ⌘W hands the popup a real Escape
/// (each sheet's own cancel semantics — dirty editors still get to ask
/// first), and ⌘Q still terminates.
@MainActor enum PopupKeyRouting {
    enum Action {
        case passThrough, sendEscape, quit
    }

    /// One local monitor for the app's life, installed at launch.
    nonisolated static func install() {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            // Only Sendable values may cross into assumeIsolated; the
            // event stays outside and is returned there, as
            // FocusVisibility's monitor does.
            let chars = event.charactersIgnoringModifiers?.lowercased()
            let flags = event.modifierFlags
            let act = MainActor.assumeIsolated {
                action(chars: chars, flags: flags, key: NSApp.keyWindow,
                       inModal: NSApp.modalWindow != nil)
            }
            let consumed = MainActor.assumeIsolated { perform(act, key: NSApp.keyWindow) }
            return consumed ? nil : event
        }
    }

    /// Carries out `action` for the key window `key`; true when the key
    /// press is used up. Split out so a test can drive it with its own
    /// window, since a test process has no key window.
    @discardableResult
    static func perform(_ action: Action, key: NSWindow?) -> Bool {
        switch action {
        case .passThrough:
            return false
        case .sendEscape:
            if let key { NSApp.sendEvent(escapeEvent(onto: key)) }
            return true
        case .quit:
            NSApp.terminate(nil)
            return true
        }
    }

    /// Pure ⌘ + w/q on a popup window routes here; everything else —
    /// other modifiers, other keys, a normal key window — stays with the
    /// system's responder chain. ⌘Q during an app-modal run (the app's own
    /// "Quit with unsaved edits?" alert is one) is left alone too: quitting
    /// from inside `runModal` asks `applicationShouldTerminate` again and
    /// stacks a second alert.
    static func action(chars: String?, flags: NSEvent.ModifierFlags,
                       key: NSWindow?, inModal: Bool = false) -> Action {
        guard flags.intersection(.deviceIndependentFlagsMask) == .command,
              let key, isPopupWindow(key) else { return .passThrough }
        switch chars {
        case "w": return .sendEscape
        case "q": return inModal ? .passThrough : .quit
        default: return .passThrough
        }
    }

    /// Sheets have a `sheetParent`, popovers are child windows, and
    /// panels (menu-bar panel, app-modal alerts) are `NSPanel`s — the
    /// only key-capable windows left are real ones. A real window with a
    /// `quiverSheet` card up counts too: ⌘W closes the card, as it would
    /// a system sheet, instead of the window under it.
    static func isPopupWindow(_ window: NSWindow) -> Bool {
        window.sheetParent != nil || window.parent != nil || window is NSPanel
            || SheetOverlayRegistry.isCovered(window)
    }

    /// A physical Escape, window-numbered so it lands on the popup's own
    /// responder chain — `.onExitCommand`, alert-cancel, panel `keyDown`.
    private static func escapeEvent(onto window: NSWindow) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero,
                         modifierFlags: [],
                         timestamp: ProcessInfo.processInfo.systemUptime,
                         windowNumber: window.windowNumber, context: nil,
                         characters: "\u{1b}", charactersIgnoringModifiers: "\u{1b}",
                         isARepeat: false, keyCode: 53)!
    }
}
