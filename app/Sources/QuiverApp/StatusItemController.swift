import AppKit
import QuiverCore
import SwiftUI

/// The menu bar item and its panel.
///
/// A borderless, key-capable, non-activating NSPanel instead of NSPopover
/// or MenuBarExtra (the OpenUsage pattern): a popover in an app that is not
/// frontmost only takes key focus on a second click, so its controls feel
/// dead on the first one.
@MainActor
final class StatusItemController: NSObject {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private var panel: MenuBarPanel!
    private var outsideClickMonitor: Any?
    private var localClickMonitor: Any?
    private let model: AppModel

    init(model: AppModel) {
        self.model = model
        super.init()
        let content = MenuBarContent(openMain: { [weak self] in self?.openMainWindow() })
        let host = NSHostingController(rootView: content.environment(model))
        host.sizingOptions = [.preferredContentSize]
        panel = MenuBarPanel(contentViewController: host)
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "gauge.with.dots.needle.67percent",
                                   accessibilityDescription: "Quiver")
            button.action = #selector(toggle)
            button.target = self
        }
    }

    @objc private func toggle() {
        panel.isVisible ? close() : open()
    }

    private func open() {
        guard let button = item.button, let window = button.window else { return }
        let anchor = window.convertToScreen(button.frame)
        let size = panel.contentViewController?.preferredContentSize ?? CGSize(width: 340, height: 300)
        panel.setContentSize(size)
        if NSScreen.screens.contains(where: { $0.frame.intersects(anchor) }) {
            panel.setFrameTopLeftPoint(NSPoint(x: anchor.midX - size.width / 2, y: anchor.minY - 6))
        } else if let screen = NSScreen.main {
            // macOS hides a status item that overflows behind the notch or
            // that the user switched off, parking it far off-screen. Anchoring
            // to it would open the panel off-screen too, so use the top-right
            // corner, where the item would have been.
            let visible = screen.visibleFrame
            panel.setFrameTopLeftPoint(NSPoint(x: visible.maxX - size.width - 8, y: visible.maxY - 6))
        }
        panel.makeKeyAndOrderFront(nil)
        // Clicks in other apps arrive at the global monitor; clicks in
        // Quiver's own windows only at a local one. Either closes the panel,
        // except a click on the panel itself or on the status item (which
        // toggles it).
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            Task { @MainActor in self?.close() }
        }
        localClickMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] event in
            if let self, event.window !== self.panel, event.window !== self.item.button?.window {
                self.close()
            }
            return event
        }
    }

    private func close() {
        panel.orderOut(nil)
        for monitor in [outsideClickMonitor, localClickMonitor].compactMap({ $0 }) {
            NSEvent.removeMonitor(monitor)
        }
        outsideClickMonitor = nil
        localClickMonitor = nil
    }

    private func openMainWindow() {
        close()
        NSApp.activate()
        if let open = model.openMainWindow {
            open()
        } else if let window = NSApp.windows.first(where: { $0.identifier?.rawValue == "main" }) {
            window.makeKeyAndOrderFront(nil)
        }
    }
}

final class MenuBarPanel: NSPanel {
    init(contentViewController: NSViewController) {
        super.init(contentRect: .zero,
                   styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: true)
        self.contentViewController = contentViewController
        isFloatingPanel = true
        level = .statusBar
        hasShadow = true
        isOpaque = false
        backgroundColor = .clear
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
    }

    override var canBecomeKey: Bool { true }
}

struct MenuBarContent: View {
    @Environment(AppModel.self) private var app
    var openMain: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Quiver").font(.headline)
                Spacer()
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task { await app.accounts.load(force: true) }
                }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
                .disabled(app.accounts.isLoading)
            }
            ForEach(app.accounts.cards) { card in
                PanelAccountRow(card: card)
            }
            if app.accounts.cards.isEmpty {
                Text("No accounts yet.").foregroundStyle(.secondary)
            }
            Divider()
            HStack {
                Button("Open Quiver", action: openMain)
                Spacer()
                Button("Quit") { NSApp.terminate(nil) }
            }
        }
        .padding(16)
        .frame(width: 340)
        .glassEffect(in: .rect(cornerRadius: 18))
        .task { await app.accounts.keepFresh() }
    }
}

/// One account in the panel: its name and its most-used window.
private struct PanelAccountRow: View {
    var card: AccountsModel.Card

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle().fill(Color(hex: card.profile.color)).frame(width: 7, height: 7)
                Text("\(card.profile.vendor.title): \(card.profile.name)").font(.subheadline.weight(.medium))
                if let plan = card.usage?.plan { Badge(planTitle(plan)) }
            }
            if let window = card.usage?.windows.max(by: { $0.usedPercent < $1.usedPercent }) {
                UsageBar(window: window)
                    .opacity(card.usage?.isStale == true ? 0.55 : 1)
            } else {
                StatusLine(usage: card.usage)
            }
        }
    }
}
