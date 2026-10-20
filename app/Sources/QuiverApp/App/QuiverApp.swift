import AppKit
import QuiverCore
import SwiftUI
import UserNotifications

struct QuiverApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        Window("Quiver", id: "main") {
            MainWindow()
                .environment(model)
                .frame(minWidth: 900, minHeight: 600)
                // A `Window` scene's NSWindow ships .fullScreenNone, so
                // the green button's tiles and ^⌘F never offer fullscreen
                // on an otherwise resizable window. Opt the main window in.
                .windowFullScreenBehavior(.enabled)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1180, height: 800)
        .commands {
            CommandGroup(after: .sidebar) {
                Button("Toggle Sidebar") {
                    model.sidebarCollapsed.toggle()
                }
                .keyboardShortcut("s", modifiers: [.command, .control])
                Button("Widen Sidebar") {
                    model.adjustSidebarWidth(by: SidebarWidth.step)
                }
                .disabled(model.sidebarCollapsed)
                Button("Narrow Sidebar") {
                    model.adjustSidebarWidth(by: -SidebarWidth.step)
                }
                .disabled(model.sidebarCollapsed)
                Button("Reset Sidebar Width") {
                    model.resetSidebarWidth()
                }
                .disabled(model.sidebarCollapsed)
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: StatusItemController?

    /// Before launch finishes, so a hidden Dock icon never flashes in.
    func applicationWillFinishLaunching(_ notification: Notification) {
        if !AppModel.shared.dockIconShown { NSApp.setActivationPolicy(.accessory) }
        AppModel.shared.applyAppearance()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        FocusVisibility.start()
        statusItem = StatusItemController(model: AppModel.shared)
        AppModel.shared.applyAppIcon()
        AppModel.shared.start()
        AppUpdater.shared.start()
    }

    /// Show limit notifications even while Quiver's window is in front.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    /// Both quit paths (the app menu and the panel's Quit) come through
    /// here; AGENTS.md drafts live only in memory.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard AppModel.shared.agentsMd.hasUnsavedChanges else { return .terminateNow }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Quit with unsaved AGENTS.md edits?"
        alert.informativeText = "Your changes haven't been saved and will be lost."
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Quit anyway")
        NSApp.activate()
        return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        true
    }

    /// The menu bar item outlives the window. A single-`Window` SwiftUI app
    /// otherwise quits when that window closes, taking the menu bar with it.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}
