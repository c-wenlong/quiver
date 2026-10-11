import AppKit
import QuiverCore
import SwiftUI

struct QuiverApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared

    var body: some Scene {
        Window("Quiver", id: "main") {
            MainWindow()
                .environment(model)
                .frame(minWidth: 900, minHeight: 600)
        }
        .windowStyle(.hiddenTitleBar)
        .defaultSize(width: 1180, height: 800)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: StatusItemController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = StatusItemController(model: AppModel.shared)
        AppModel.shared.start()
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
