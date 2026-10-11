import QuiverCore
import SwiftUI
import WidgetKit

/// What Quiver shows in the menu bar and on the desktop, after
/// TokenTracker's Menu Bar & Widgets page: a live preview of the status
/// item with its switch and figures, then the four widgets, marked when
/// they're on the desktop. macOS has no way for an app to add a widget, so
/// the page says how.
struct MenuBarWidgetsPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Page(title: "Menu bar and widgets",
             subtitle: "What Quiver shows at the top of your screen and on your desktop.",
             maxWidth: 1024) {
            VStack(alignment: .leading, spacing: 32) {
                MenuBarSection()
                WidgetsSection()
            }
        }
    }
}

private struct SectionHeading: View {
    var title: String
    var detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(Theme.sectionTitle).tracking(-0.5).foregroundStyle(Theme.textStrong)
            Text(detail).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }
    }
}

// MARK: - Menu bar

private struct MenuBarSection: View {
    @Environment(AppModel.self) private var app

    private var limit: LimitReading? { app.menuBar.limit(in: app.limits.readings, now: .now) }
    private var today: UsageTotals? { MenuBarDisplay.today(in: app.overview?.spend) }

    var body: some View {
        @Bindable var app = app
        VStack(alignment: .leading, spacing: 16) {
            SectionHeading(title: "Menu bar", detail: "The item next to the clock, and the panel it opens.")
            SettingsCard {
                MenuBarPreview(display: app.menuBar, limit: limit, today: today)
                    .padding(.vertical, 16)
                SettingsDivider()
                SettingRow(title: "Show in menu bar",
                           detail: "Off hides the item. Open Quiver from the Dock instead.") {
                    SettingSwitch(title: "Show in menu bar", isOn: $app.menuBar.isVisible)
                }
                SettingsDivider()
                SettingRow(title: "Shows", detail: "The first figure next to the icon.") {
                    SelectMenu(options: MenuBarFigure.allCases.map { ($0, $0.title) },
                               selection: $app.menuBar.primary)
                }
                .disabled(!app.menuBar.isVisible)
                SettingsDivider()
                SettingRow(title: "Then", detail: "An optional second figure.") {
                    SelectMenu(options: MenuBarFigure.allCases.map { ($0, $0.title) },
                               selection: $app.menuBar.secondary)
                }
                .disabled(!app.menuBar.isVisible)
                SettingsDivider()
                SettingRow(title: "Limit to watch", detail: "The limit the icon's ring and the Limit figure follow.") {
                    SelectMenu(options: limitOptions, selection: $app.menuBar.watchedLimit, width: 220,
                               title: watchedIsMissing ? "Tightest of all" : nil)
                }
                .disabled(!app.menuBar.isVisible)
            }
        }
    }

    private var limitOptions: [(value: String?, title: String)] {
        [(nil, "Tightest of all")] + app.limits.readings.map { reading in
            (Optional(reading.id), reading.window.isEmpty ? reading.title : "\(reading.title) \(reading.window)")
        }
    }

    /// A chosen limit that's gone falls back to the tightest; say so.
    private var watchedIsMissing: Bool {
        guard let id = app.menuBar.watchedLimit else { return false }
        return !app.limits.readings.contains { $0.id == id }
    }
}

/// The status item as it would look: a strip of menu bar with the clock,
/// the ring and the chosen text, drawn by the same code as the real item.
private struct MenuBarPreview: View {
    var display: MenuBarDisplay
    var limit: LimitReading?
    var today: UsageTotals?

    var body: some View {
        let text = display.text(limit: limit, today: today)
        HStack(spacing: 16) {
            Spacer(minLength: 0)
            Image(systemName: "wifi").foregroundStyle(Theme.textSecondary)
            if display.isVisible {
                HStack(spacing: 4) {
                    icon
                    if !text.isEmpty {
                        Text(text).font(.system(size: 12, weight: .medium).monospacedDigit())
                            .foregroundStyle(Theme.textPrimary)
                    }
                }
                .padding(.horizontal, 8)
                .frame(height: 22)
                .background(Theme.selected, in: .rect(cornerRadius: 5))
            }
            Text(Date.now, format: .dateTime.weekday(.abbreviated).hour().minute())
                .font(.system(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
        }
        .padding(.horizontal, 14)
        .frame(height: 36)
        .background(Theme.wash, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(alignment: .bottom) {
            Text(display.isVisible ? "Preview" : "Hidden from the menu bar")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
                .offset(y: 22)
        }
        .padding(.bottom, 18)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(display.isVisible
            ? "Menu bar preview: \(text.isEmpty ? "icon only" : text)" : "Menu bar preview: hidden")
    }

    @ViewBuilder private var icon: some View {
        if let limit {
            Image(nsImage: LimitRing.image(fraction: limit.usedPercent / 100, critical: limit.percent >= 90))
                .renderingMode(limit.percent >= 90 ? .original : .template)
                .foregroundStyle(Theme.textPrimary)
        } else {
            Image(systemName: "gauge.with.dots.needle.67percent").foregroundStyle(Theme.textPrimary)
        }
    }
}

// MARK: - Widgets

private struct WidgetsSection: View {
    /// Kinds of Quiver's widgets on the desktop or in Notification Center.
    @State private var placed: Set<String> = []
    @State private var refreshed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                SectionHeading(title: "Desktop widgets",
                               detail: "Right-click the desktop, choose Edit widgets, and search for Quiver.")
                Spacer(minLength: 16)
                Button(refreshed ? "Refreshed" : "Refresh widgets", systemImage: "arrow.clockwise") {
                    WidgetCenter.shared.reloadAllTimelines()
                    refreshed = true
                    Task { await loadPlaced() }
                }
                .secondaryButton()
                .help("Redraw the widgets with the latest figures")
            }
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: Theme.gutter)], spacing: Theme.gutter) {
                ForEach(WidgetCatalog.all) { widget in
                    WidgetCard(widget: widget, isPlaced: placed.contains(widget.kind))
                }
            }
        }
        .task { await loadPlaced() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            Task { await loadPlaced() }
        }
    }

    private func loadPlaced() async {
        let kinds: Set<String> = await withCheckedContinuation { continuation in
            WidgetCenter.shared.getCurrentConfigurations { result in
                continuation.resume(returning: Set((try? result.get())?.map(\.kind) ?? []))
            }
        }
        placed = kinds
    }
}

private struct WidgetCard: View {
    var widget: WidgetCatalog
    var isPlaced: Bool

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Image(systemName: widget.symbol)
                        .font(.system(size: 14))
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 18)
                    Text(widget.title).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    Spacer(minLength: 8)
                    if isPlaced { Badge("On desktop") }
                }
                Text(widget.detail).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(widget.sizes).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .topLeading)
        }
    }
}
