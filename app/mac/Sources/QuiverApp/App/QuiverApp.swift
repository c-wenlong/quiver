import AppKit
import QuiverCore
import SwiftUI
import UserNotifications

struct QuiverApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var model = AppModel.shared
    @State private var textZoom = TextZoomModel.shared

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
                Divider()
                // Text size, like a browser's zoom. AppKit also matches
                // ⌘= to the "+" item, so no shift is needed.
                Button("Actual Size") { textZoom.actualSize() }
                    .keyboardShortcut("0", modifiers: .command)
                    .disabled(textZoom.isActualSize)
                Button("Zoom In") { textZoom.zoomIn() }
                    .keyboardShortcut("+", modifiers: .command)
                    .disabled(!textZoom.canZoomIn)
                Button("Zoom Out") { textZoom.zoomOut() }
                    .keyboardShortcut("-", modifiers: .command)
                    .disabled(!textZoom.canZoomOut)
                Divider()
                ForEach(Array(model.sidebar.shortcutSections.enumerated()), id: \.element) { index, section in
                    Button(section.title) {
                        model.select(section)
                    }
                    .keyboardShortcut(KeyEquivalent(Character(String(index + 1))), modifiers: .command)
                    // A card over the window belongs to the page under it:
                    // switching page would drop it, unsaved edits and all,
                    // without its close check. Close the card first.
                    .disabled(SheetOverlayRegistry.shared.anyCovered)
                }
            }
            // macOS keeps its own search field at the top of Help; this
            // replaces only the "Quiver Help" item, which had no help book.
            CommandGroup(replacing: .help) {
                Button("Report a Bug…") { Feedback.openIssueForm(.bug, app: model) }
                Button("Request a Feature…") { Feedback.openIssueForm(.feature, app: model) }
                Divider()
                Button("Copy Diagnostics") { Feedback.copyDiagnostics(app: model) }
                    .disabled(model.doctor.isBusy)
                Divider()
                Button("Quiver Documentation") { NSWorkspace.shared.open(IssueReport.documentation) }
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: StatusItemController?
    private var dockBadgeTask: Task<Void, Never>?
    private var termSource: DispatchSourceSignal?

    /// Before launch finishes, so a hidden Dock icon never flashes in.
    func applicationWillFinishLaunching(_ notification: Notification) {
        if !AppModel.shared.dockIconShown { NSApp.setActivationPolicy(.accessory) }
        AppModel.shared.applyAppearance()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        quitOnSIGTERM()
        PopupKeyRouting.install()
        FocusVisibility.start()
        CommandKeyHold.start()
        statusItem = StatusItemController(model: AppModel.shared)
        AppModel.shared.applyAppIcon()
        AppModel.shared.start()
        // The Dock tile mirrors the unread inbox count — ambient even
        // when no window is open.
        let model = AppModel.shared
        dockBadgeTask = Task { @MainActor in
            for await unread in Observations({ model.github.unreadCount }) {
                NSApp.dockTile.badgeLabel = unread > 0 ? "\(unread)" : nil
            }
        }
        AppUpdater.shared.start()
    }

    /// Show limit notifications even while Quiver's window is in front.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    /// A GitHub banner click routes to the repo's board, or the Inbox
    /// when it isn't pinned. Limit banners carry no `gh` marker and are
    /// left to the OS default.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        // Only the click routes — dismissing a banner must not open a window.
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier else { return }
        let info = response.notification.request.content.userInfo
        guard info["gh"] as? Bool == true else { return }
        let repo = info["repo"] as? String ?? ""
        let type = info["type"] as? String ?? ""
        await MainActor.run {
            AppModel.shared.openGitHub(repo: repo, type: type)
        }
    }

    /// install-app.sh quits the installed copy with SIGTERM (by pid, so a
    /// dev build with the same bundle id is left alone). Left to the
    /// default action the signal ends the process on the spot, past
    /// applicationShouldTerminate and its unsaved-drafts check; here it
    /// quits the way the menu does.
    private func quitOnSIGTERM() {
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { MainActor.assumeIsolated { NSApp.terminate(nil) } }
        source.resume()
        termSource = source
    }

    /// The GitHub snapshot's debounced write may still be pending; the
    /// final flush makes quit cheap enough to be synchronous.
    func applicationWillTerminate(_ notification: Notification) {
        AppModel.shared.github.saveNow()
    }

    /// Both quit paths (the app menu and the panel's Quit) come through
    /// here; AGENTS.md and hook drafts live only in memory.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let app = AppModel.shared
        let agentsMdDirty = app.agentsMd.hasUnsavedChanges
        let hooksDirty = app.hooks.hasUnsavedChanges
        guard agentsMdDirty || hooksDirty else { return .terminateNow }
        let alert = NSAlert()
        alert.alertStyle = .warning
        switch (agentsMdDirty, hooksDirty) {
        case (true, true): alert.messageText = "Quit with unsaved AGENTS.md and hook edits?"
        case (true, false): alert.messageText = "Quit with unsaved AGENTS.md edits?"
        default: alert.messageText = "Quit with unsaved hook edits?"
        }
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
