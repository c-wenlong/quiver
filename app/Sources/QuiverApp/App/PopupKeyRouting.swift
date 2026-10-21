import AppKit

/// Sheets, alerts and panels own key-window status while they're up, so
/// ⌘W/⌘Q delivered to them never reaches the real window's Close or the
/// app menu's Quit — a popup looked like it blocked both. The monitor
/// answers the same way the menu would: ⌘W dismisses the popup (the next
/// ⌘W then closes the window), ⌘Q still terminates.
enum PopupKeyRouting {
    enum Action {
        case passThrough, dismissPopup, quit
    }

    /// One local monitor for the app's life, installed at launch.
    static func install() {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            switch action(for: event, key: NSApp.keyWindow) {
            case .passThrough:
                return event
            case .dismissPopup:
                if let key = NSApp.keyWindow { dismiss(key) }
                return nil
            case .quit:
                NSApp.terminate(nil)
                return nil
            }
        }
    }

    /// Pure ⌘ + w/q on a popup window routes here; everything else —
    /// other modifiers, other keys, a normal key window — stays with the
    /// system's responder chain.
    static func action(for event: NSEvent, key: NSWindow?) -> Action {
        guard event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .command,
              let key, isPopupWindow(key) else { return .passThrough }
        switch event.charactersIgnoringModifiers?.lowercased() {
        case "w": return .dismissPopup
        case "q": return .quit
        default: return .passThrough
        }
    }

    /// Sheets have a `sheetParent`, popovers are child windows, and
    /// panels (menu-bar panel, app-modal alerts) are `NSPanel`s — the
    /// only key-capable windows left are real ones.
    static func isPopupWindow(_ window: NSWindow) -> Bool {
        window.sheetParent != nil || window.parent != nil || window is NSPanel
    }

    private static func dismiss(_ window: NSWindow) {
        if let parent = window.sheetParent {
            // Ends the sheet session; SwiftUI watches the sheet end and
            // drops its isPresented binding itself.
            parent.endSheet(window)
        } else if NSApp.modalWindow === window {
            NSApp.abortModal()
        } else {
            window.close()
        }
    }
}
