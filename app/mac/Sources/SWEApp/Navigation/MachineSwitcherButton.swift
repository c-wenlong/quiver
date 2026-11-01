import AppKit
import SWECore
import SwiftUI

/// The sidebar's first row: which machine the window shows. "This Mac" is
/// the native pages; another machine's hub shows its web app in the panel
/// (`HubWebPage`). The menu also holds that page's Reload and Open in
/// Browser, since the title bar strip above it takes no clicks.
struct MachineSwitcherButton: View {
    /// The icon rail: the icon alone, at the nav icons' x.
    var collapsed: Bool
    @Environment(AppModel.self) private var app
    @State private var hovering = false

    private var machines: MachineSwitcher { app.machines }

    var body: some View {
        Menu {
            Button { machines.showThisMac() } label: {
                checked("This Mac", on: machines.selected == nil)
            }
            ForEach(machines.hubs) { hub in
                Button { machines.select(hub.origin) } label: {
                    checked(hub.origin.name, subtitle: hub.origin.address, on: machines.selected == hub.origin)
                }
            }
            Divider()
            if let hub = machines.selected {
                Button("Reload \(hub.name)", systemImage: "arrow.clockwise") {
                    app.hubWebViews.reload(hub)
                }
                Button("Open in Browser", systemImage: "safari") {
                    NSWorkspace.shared.open(hub.url)
                }
                Divider()
            }
            Button("Add a Hub…", systemImage: "plus") {
                // Settings › Integrations holds the Remote card's hub list.
                UserDefaults.standard.set(SettingsSection.integrations.rawValue, forKey: "settingsSection")
                app.select(.settings)
            }
        } label: {
            label
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize(horizontal: false, vertical: true)
        .onHover { hovering = $0 }
        .hoverTip(collapsed ? "Showing \(title)" : "Choose the machine this window shows")
        .accessibilityLabel("Machine: \(title)")
    }

    private var title: String { machines.selected?.name ?? "This Mac" }

    private var label: some View {
        HStack(spacing: 10) {
            Image(systemName: machines.showsHub ? "server.rack" : "laptopcomputer")
                .font(Theme.font(size: 15, weight: .regular))
                .frame(width: 18)
            Text(title)
                .font(Theme.nav.weight(.medium))
                .lineLimit(1)
                .opacity(collapsed ? 0 : 1)
            Spacer(minLength: 0)
            Image(systemName: "chevron.up.chevron.down")
                .font(Theme.font(size: 10, weight: .medium))
                .foregroundStyle(Theme.textMuted)
                .opacity(collapsed ? 0 : 1)
        }
        .foregroundStyle(machines.showsHub || hovering ? Theme.textPrimary : Theme.textNav)
        .padding(.horizontal, 8)
        .frame(maxWidth: collapsed ? Theme.navIconPillWidth : .infinity, alignment: .leading)
        .frame(height: Theme.navRowHeight)
        .clipped()
        // A hub on screen keeps the row filled, so it is plain which
        // machine the panel belongs to.
        .background(machines.showsHub ? Theme.navActive : hovering ? Theme.hover : .clear,
                    in: .rect(cornerRadius: Theme.controlRadius))
        .contentShape(.rect(cornerRadius: Theme.controlRadius))
    }

    @ViewBuilder
    private func checked(_ title: String, subtitle: String? = nil, on: Bool) -> some View {
        if on { Image(systemName: "checkmark") }
        Text(title)
        if let subtitle { Text(subtitle) }
    }
}
