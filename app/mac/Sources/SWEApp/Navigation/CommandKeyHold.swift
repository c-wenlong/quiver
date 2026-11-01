import AppKit
import Observation
import SwiftUI

/// Whether ⌘ alone is held down, for the sidebar's ⌘1 to ⌘9 caps. The caps
/// wait a moment before showing so a quick ⌘C or ⌘R never flashes them,
/// and go as soon as ⌘ lifts, another modifier joins, or the app loses
/// focus (⌘Tab away sends the key-up to the other app).
@MainActor @Observable
final class CommandKeyHold {
    static let shared = CommandKeyHold()
    private(set) var isHeld = false
    @ObservationIgnored private var showTask: Task<Void, Never>?

    /// Starts watching the modifier keys; call once at launch.
    static func start() { _ = shared }

    private init() {
        NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { event in
            // Caps Lock is a latched state, not a held key: it must not
            // hide the caps.
            let commandOnly = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
                .subtracting(.capsLock) == .command
            MainActor.assumeIsolated { CommandKeyHold.shared.update(commandOnly) }
            return event
        }
        NotificationCenter.default.addObserver(
            forName: NSApplication.didResignActiveNotification, object: nil, queue: .main
        ) { _ in
            MainActor.assumeIsolated { CommandKeyHold.shared.update(false) }
        }
    }

    private func update(_ down: Bool) {
        showTask?.cancel()
        guard down else {
            if isHeld { withAnimation(.easeOut(duration: 0.1)) { isHeld = false } }
            return
        }
        showTask = Task {
            try? await Task.sleep(for: .milliseconds(200))
            guard !Task.isCancelled else { return }
            withAnimation(.easeOut(duration: 0.1)) { isHeld = true }
        }
    }
}
