import QuiverCore
import SwiftUI

/// What Quiver shows in the menu bar, after TokenTracker's Menu Bar &
/// Widgets page, in two tabs: the status item (a live preview, its
/// switch and figures) and the popup it opens (its tabs, then each
/// tab's own settings). The desktop widgets have their own page. A stored "widgets" tab from the old three-tab page reads as an
/// unknown raw value and falls back to the first tab.
struct MenuBarPage: View {
    enum Tab: String { case menuBar, popup }

    @AppStorage("menuBarPageTab") private var tab = Tab.menuBar

    var body: some View {
        Page(title: "Menu bar",
             subtitle: "What Quiver shows at the top of your screen.") {
            VStack(alignment: .leading, spacing: 24) {
                Tabs(options: [(Tab.menuBar, "Menu bar"), (.popup, "Popup")],
                     selection: $tab, style: .underline)
                switch tab {
                case .menuBar: MenuBarSection()
                case .popup: PopupSection()
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
    /// One width for the Menu bar card's three pickers, so they line up;
    /// the limit names ("Claude: Personal · 7d Fable") need this much.
    private static let pickerWidth: CGFloat = 220
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
                               selection: $app.menuBar.primary, width: Self.pickerWidth)
                }
                .disabled(!app.menuBar.isVisible)
                SettingsDivider()
                SettingRow(title: "Then", detail: "An optional second figure.") {
                    SelectMenu(options: MenuBarFigure.allCases.map { ($0, $0.title) },
                               selection: $app.menuBar.secondary, width: Self.pickerWidth)
                }
                .disabled(!app.menuBar.isVisible)
                SettingsDivider()
                SettingRow(title: "Limit to watch", detail: "The limit the icon's ring and the Limit figure follow.") {
                    SelectMenu(options: limitOptions, selection: $app.menuBar.watchedLimit, width: Self.pickerWidth,
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
                        Text(text).font(Theme.font(size: 12, weight: .medium).monospacedDigit())
                            .foregroundStyle(Theme.textPrimary)
                    }
                }
                .padding(.horizontal, 8)
                .frame(height: 22)
                .background(Theme.selected, in: .rect(cornerRadius: 5))
            }
            Text(Date.now, format: .dateTime.weekday(.abbreviated).hour().minute())
                .font(Theme.font(size: 12, weight: .medium)).foregroundStyle(Theme.textSecondary)
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

/// The popup's tabs, then the settings of one tab beside a live preview
/// of that tab.
private struct PopupSection: View {
    @Environment(AppModel.self) private var app
    @State private var width: CGFloat = 0
    /// The tab whose settings show, and which the preview draws.
    @AppStorage("menuBarPageEditingTab") private var editing = PopupTab.usage

    /// Below this the 340pt preview no longer leaves the settings room.
    static let twoColumnWidth: CGFloat = 760

    var body: some View {
        Group {
            if width >= Self.twoColumnWidth {
                HStack(alignment: .top, spacing: 24) {
                    settings
                        .frame(maxWidth: .infinity, alignment: .leading)
                    preview
                }
            } else {
                VStack(spacing: Theme.sectionGap) {
                    preview
                    settings
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }

    /// The real popup the status item opens, live: it reads the same models
    /// these switches write, and takes no input (its Quit would terminate
    /// the app, its Refresh would kick off loads). The shadow stands in for
    /// the panel's window shadow.
    private var preview: some View {
        VStack(spacing: 10) {
            MenuBarContent(openMain: {}, previewTab: editing)
                .allowsHitTesting(false)
                .accessibilityHidden(true)
                .shadow(color: .black.opacity(0.35), radius: 18, y: 8)
            Text(app.popupTabs.shows(editing) ? "Preview" : "Preview, \(editing.title) is switched off")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
        }
    }

    private var settings: some View {
        VStack(alignment: .leading, spacing: Theme.sectionGap) {
            PopupTabsSettings()
            VStack(alignment: .leading, spacing: 16) {
                Tabs(options: PopupTab.allCases.map { ($0, $0.title) }, selection: $editing,
                     style: .underline)
                editing.settings
            }
        }
    }
}

/// One row of a draggable list with a switch: a grip, an optional icon,
/// the title, and the switch at the trailing edge.
private struct ToggleRow<Icon: View>: View {
    var title: String
    @Binding var isOn: Bool
    /// The last shown tab's switch can't turn it off.
    var locked = false
    @ViewBuilder var icon: Icon

    var body: some View {
        HStack(spacing: 10) {
            GripDots()
            icon
            Text(title).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
            Spacer(minLength: 16)
            SettingSwitch(title: "Show \(title)", isOn: $isOn)
                .disabled(locked)
                .help(locked ? "At least one tab stays on" : "")
        }
        .padding(.vertical, 12)
        .accessibilityHint("Drag to reorder")
    }
}

/// The tab bar's tabs: which show, and in what order.
private struct PopupTabsSettings: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionHeading(title: "Tabs",
                           detail: "The tabs across the top of the popup. Drag to reorder.")
            SettingsCard {
                ReorderableRows(app.popupTabs.all, showsDividers: true) { id, before in
                    if let before {
                        app.popupTabs.move(id, before: before)
                    } else {
                        app.popupTabs.moveToEnd(id)
                    }
                } row: { tab in
                    ToggleRow(title: tab.title,
                              isOn: Binding(get: { app.popupTabs.shows(tab) },
                                            set: { app.popupTabs.setShown($0, tab) }),
                              locked: !app.popupTabs.canHide(tab)) {
                        Image(systemName: tab.symbol)
                            .font(Theme.font(size: 13))
                            .foregroundStyle(Theme.textNav)
                            .frame(width: 16)
                    }
                }
            }
        }
    }
}

/// The Usage tab's settings: incidents, spend, and which harnesses and
/// limits it lists.
struct UsageTabSettings: View {
    @Environment(AppModel.self) private var app

    /// The same rows the Accounts page lists: every starred account, even
    /// before its usage loads, each its own row so two accounts of one
    /// harness hide and order apart; then the starred other harnesses.
    /// Rows stay put as usage changes, until the user drags them; then
    /// the dragged order wins.
    private var rows: [MenuBarPopup.Row] {
        let sources = app.starredAccountCards.map {
            (source: $0.profile.id, harness: $0.profile.vendor.rawValue,
             title: "\($0.profile.vendor.title): \($0.profile.name)")
        } + app.others.rows.map { (source: $0.id, harness: $0.name, title: $0.accountTitle) }
        return app.popup.orderedRows(in: app.limits.readings, sources: sources)
    }

    var body: some View {
        @Bindable var app = app
        VStack(alignment: .leading, spacing: Theme.sectionGap) {
            VStack(alignment: .leading, spacing: 16) {
                SectionHeading(title: "Usage tab", detail: "Limits, incidents and spend.")
                SettingsCard {
                    SettingRow(title: "Show incidents",
                               detail: "A line at the top when a service your harnesses use has a problem, or Cloudflare, Vercel or npm has a major outage.") {
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
                               detail: "Which accounts and harnesses the tab lists, which of their limits, and in what order. Drag to reorder.")
                SettingsCard {
                    let rows = rows
                    let sources = rows.map { (source: $0.source, harness: $0.harness) }
                    if rows.isEmpty {
                        Text("No limits yet. Add an account on the Accounts page, or star a harness on Harnesses.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .padding(.vertical, 14)
                    }
                    ReorderableRows(rows, showsDividers: true) { id, before in
                        if let before {
                            app.popup.move(id, before: before, in: sources)
                        } else {
                            app.popup.moveToEnd(id, in: sources)
                        }
                    } row: { row in
                        PopupSourceRow(row: row)
                    }
                }
            }
        }
    }
}

/// The Compute tab's settings: which charts in what order, how often
/// they take a point, and which harnesses get a line.
struct ComputeTabSettings: View {
    @Environment(AppModel.self) private var app

    private struct HarnessRow: Identifiable {
        var id: String
    }

    /// Every starred harness in swe's order, then any other harness the
    /// history has points for, in the dragged order.
    private var harnessIDs: [String] {
        let starred = app.compute.matchers.map(\.harness)
        let seen = app.compute.history.series.keys.filter { !starred.contains($0) }.sorted()
        return app.computePopup.harnesses.ordered(starred + seen)
    }

    private var interval: Binding<TrendInterval> {
        Binding(get: { app.compute.trendInterval }, set: { app.compute.trendInterval = $0 })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.sectionGap) {
            VStack(alignment: .leading, spacing: 16) {
                SectionHeading(title: "Charts",
                               detail: "The charts the tab stacks, top to bottom. Drag to reorder.")
                SettingsCard {
                    ReorderableRows(app.computePopup.allMetrics, showsDividers: true) { id, before in
                        app.computePopup.moveMetric(id, before: before)
                    } row: { metric in
                        ToggleRow(title: metric.title,
                                  isOn: Binding(get: { app.computePopup.metrics.shows(metric.id) },
                                                set: { app.computePopup.metrics.setShown($0, metric.id) })) {
                            EmptyView()
                        }
                    }
                    SettingsDivider()
                    SettingRow(title: "Take a point",
                               detail: "Also sets the Compute page's chart.") {
                        SelectMenu(options: TrendInterval.allCases.map { ($0, $0.title) },
                                   selection: interval, width: 150)
                    }
                }
            }
            VStack(alignment: .leading, spacing: 16) {
                SectionHeading(title: "Harnesses",
                               detail: "Which starred harnesses get a line, and the order the hover readout lists them in. Drag to reorder.")
                SettingsCard {
                    let ids = harnessIDs
                    if ids.isEmpty {
                        Text("No harnesses yet. Star a harness on Harnesses to chart it.")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            .padding(.vertical, 14)
                    }
                    ReorderableRows(ids.map(HarnessRow.init), showsDividers: true) { id, before in
                        if let before {
                            app.computePopup.harnesses.move(id, before: before, in: ids)
                        } else {
                            app.computePopup.harnesses.moveToEnd(id, in: ids)
                        }
                    } row: { row in
                        ToggleRow(title: UsageFormat.harness(row.id),
                                  isOn: Binding(get: { app.computePopup.harnesses.shows(row.id) },
                                                set: { app.computePopup.harnesses.setShown($0, row.id) })) {
                            HarnessIcon(harness: row.id, size: 16)
                        }
                    }
                }
            }
        }
    }
}

/// A source's switch, then a chip per limit window it reports. The popup's
/// rows get a drag grip; each widget's settings the same row without one.
struct SourceSettingsRow: View {
    var row: MenuBarPopup.Row
    var grip = false
    var shown: Bool
    var setShown: (Bool) -> Void
    var showsWindow: (String) -> Bool
    var setWindowShown: (String, Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                if grip { GripDots() }
                HarnessIcon(harness: row.harness, size: 16)
                Text(row.title).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                Spacer(minLength: 16)
                SettingSwitch(title: "Show \(row.title)",
                              isOn: Binding(get: { shown }, set: { setShown($0) }))
            }
            .padding(.vertical, 12)
            if !row.windows.isEmpty {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(row.windows, id: \.self) { window in
                        WindowChip(title: UsageFormat.window(window),
                                   isOn: Binding(get: { showsWindow(window) },
                                                 set: { setWindowShown(window, $0) }))
                    }
                }
                .padding(.leading, grip ? 38 : 26)
                .padding(.bottom, 12)
                .disabled(!shown)
            }
        }
        .accessibilityHint(grip ? "Drag to reorder" : "")
    }
}

/// An account's or harness's switch, then a chip per limit window it reports.
private struct PopupSourceRow: View {
    @Environment(AppModel.self) private var app
    var row: MenuBarPopup.Row

    var body: some View {
        SourceSettingsRow(
            row: row, grip: true,
            shown: app.popup.shows(source: row.source, harness: row.harness),
            setShown: { app.popup.setShown($0, source: row.source, harness: row.harness,
                                           allWindows: row.windows) },
            showsWindow: { app.popup.shows(window: $0, source: row.source, harness: row.harness,
                                           allWindows: row.windows) },
            setWindowShown: { app.popup.setShown($1, window: $0, source: row.source,
                                                 harness: row.harness, allWindows: row.windows) })
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
                Image(systemName: isOn ? "checkmark" : "minus").font(Theme.font(size: 10, weight: .semibold))
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
