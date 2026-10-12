import QuiverCore
import SwiftUI
import WidgetKit

/// What Quiver shows in the menu bar and on the desktop, after
/// TokenTracker's Menu Bar & Widgets page, in three tabs: the status item
/// (a live preview, its switch and figures), the popup it opens (incidents,
/// spend, and which harnesses and limits), and the four desktop widgets,
/// marked when they're placed. macOS has no way for an app to add a widget,
/// so that tab says how.
struct MenuBarWidgetsPage: View {
    enum Tab: String { case menuBar, popup, widgets }

    @AppStorage("menuBarPageTab") private var tab = Tab.menuBar

    var body: some View {
        Page(title: "Menu bar and widgets",
             subtitle: "What Quiver shows at the top of your screen and on your desktop.",
             maxWidth: 1024) {
            VStack(alignment: .leading, spacing: 24) {
                Tabs(options: [(Tab.menuBar, "Menu bar"), (.popup, "Menu bar popup"), (.widgets, "Desktop widgets")],
                     selection: $tab, style: .underline)
                switch tab {
                case .menuBar: MenuBarSection()
                case .popup: PopupSection()
                case .widgets: WidgetsSection()
                }
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
            (Optional(reading.id), reading.window.isEmpty ? reading.title : "\(reading.title) \(UsageFormat.window(reading.window))")
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

// MARK: - Popup

private struct PopupSection: View {
    @Environment(AppModel.self) private var app

    /// Accounts' harnesses in account order, then other harnesses in swe's
    /// order, so the rows stay put as usage changes, until the user drags
    /// them; then the dragged order wins. An account whose limits
    /// haven't come in yet is listed too, so it can be hidden before they do.
    private var harnesses: [MenuBarPopup.Harness] {
        let order = app.starredAccountCards.map(\.profile.vendor.rawValue)
            + app.others.rows.filter { $0.limit.status == .ok }.map(\.name)
        return app.popup.orderedHarnesses(in: app.limits.readings, order: order)
    }

    var body: some View {
        @Bindable var app = app
        VStack(alignment: .leading, spacing: 32) {
            VStack(alignment: .leading, spacing: 16) {
                SectionHeading(title: "Menu bar popup", detail: "What opens when you click the menu bar item.")
                SettingsCard {
                    SettingRow(title: "Show incidents",
                               detail: "A line at the top when a service your harnesses use has a problem.") {
                        SettingSwitch(title: "Show incidents", isOn: $app.popup.showIncidents)
                    }
                    SettingsDivider()
                    SettingRow(title: "Show spend",
                               detail: "Spend by harness for today, yesterday or the last 30 days.") {
                        SettingSwitch(title: "Show spend", isOn: $app.popup.showStats)
                    }
                }
            }
            VStack(alignment: .leading, spacing: 16) {
                SectionHeading(title: "Harnesses and limits",
                               detail: "Which harnesses the popup lists, which of their limits, and in what order. Drag to reorder.")
                SettingsCard {
                    let harnesses = harnesses
                    let ids = harnesses.map(\.id)
                    if harnesses.isEmpty {
                        Text("No limits yet. Add an account on the Accounts page, or star a harness on Harnesses.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .padding(.vertical, 14)
                    }
                    ReorderableRows(harnesses, showsDividers: true) { id, before in
                        if let before {
                            app.popup.move(id, before: before, in: ids)
                        } else {
                            app.popup.moveToEnd(id, in: ids)
                        }
                    } row: { harness in
                        PopupHarnessRow(harness: harness)
                    }
                }
            }
        }
    }
}

/// A harness's switch, then a chip per limit window it reports.
private struct PopupHarnessRow: View {
    @Environment(AppModel.self) private var app
    var harness: MenuBarPopup.Harness

    var body: some View {
        let shown = app.popup.shows(harness: harness.harness)
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                GripDots()
                HarnessIcon(harness: harness.harness, size: 16)
                Text(UsageFormat.harness(harness.harness)).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                Spacer(minLength: 16)
                SettingSwitch(title: "Show \(UsageFormat.harness(harness.harness))",
                              isOn: Binding(get: { shown },
                                            set: { app.popup.setShown($0, harness: harness.harness) }))
            }
            .padding(.vertical, 12)
            if !harness.windows.isEmpty {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(harness.windows, id: \.self) { window in
                        WindowChip(title: UsageFormat.window(window),
                                   isOn: Binding(get: { app.popup.shows(window: window, of: harness.harness) },
                                                 set: { app.popup.setShown($0, window: window, of: harness.harness) }))
                    }
                }
                .padding(.leading, 38)
                .padding(.bottom, 12)
                .disabled(!shown)
            }
        }
        .accessibilityHint("Drag to reorder")
    }
}

/// A limit window that toggles: a check and full text when shown, muted
/// when hidden. 28pt, like the other chips.
private struct WindowChip: View {
    var title: String
    @Binding var isOn: Bool
    @State private var hovering = false

    var body: some View {
        Button { isOn.toggle() } label: {
            HStack(spacing: 5) {
                Image(systemName: isOn ? "checkmark" : "minus").font(.system(size: 10, weight: .semibold))
                Text(title).font(Theme.caption)
            }
            .foregroundStyle(isOn ? Theme.textPrimary : Theme.textMuted)
            .padding(.horizontal, 10)
            .frame(height: 28)
            .background(isOn ? Theme.selected : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.selectRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.selectRadius)
                .strokeBorder(isOn ? Theme.borderStrong : Theme.border, lineWidth: 1))
            .contentShape(.rect(cornerRadius: Theme.selectRadius))
        }
        .plainButton(cornerRadius: Theme.selectRadius)
        .onHover { hovering = $0 }
        .accessibilityLabel(title)
        .accessibilityValue(isOn ? "shown" : "hidden")
        .help(isOn ? "Hide \(title) in the popup" : "Show \(title) in the popup")
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
