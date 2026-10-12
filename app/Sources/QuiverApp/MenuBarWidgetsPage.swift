import QuiverCore
import QuiverWidgetsUI
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
    @State private var width: CGFloat = 0

    /// Below this the 340pt preview no longer leaves the settings room.
    static let twoColumnWidth: CGFloat = 760

    /// The same rows the Accounts page lists: every starred account, even
    /// before its usage loads, each its own row so two accounts of one
    /// harness hide and order apart; then the starred other harnesses.
    /// Rows stay put as usage changes, until the user drags them; then
    /// the dragged order wins.
    private var rows: [MenuBarPopup.Row] {
        let sources = app.starredAccountCards.map {
            (source: $0.profile.id, harness: $0.profile.vendor.rawValue,
             title: "\($0.profile.vendor.title): \($0.profile.name)")
        } + app.others.rows.map { (source: $0.name, harness: $0.name, title: $0.accountTitle) }
        return app.popup.orderedRows(in: app.limits.readings, sources: sources)
    }

    var body: some View {
        @Bindable var app = app
        Group {
            if width >= Self.twoColumnWidth {
                HStack(alignment: .top, spacing: 24) {
                    settings(app)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    preview
                }
            } else {
                VStack(spacing: 32) {
                    preview
                    settings(app)
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
            MenuBarContent(openMain: {})
                .allowsHitTesting(false)
                .accessibilityHidden(true)
                .shadow(color: .black.opacity(0.35), radius: 18, y: 8)
            Text("Preview")
                .font(Theme.caption).foregroundStyle(Theme.textMuted)
        }
    }

    /// The settings column: both cards and their headings, unchanged.
    private func settings(_ app: AppModel) -> some View {
        @Bindable var app = app
        return VStack(alignment: .leading, spacing: 32) {
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
                               detail: "Which accounts and harnesses the popup lists, which of their limits, and in what order. Drag to reorder.")
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

/// A source's switch, then a chip per limit window it reports. The popup's
/// rows get a drag grip; each widget's settings the same row without one.
private struct SourceSettingsRow: View {
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
                              isOn: Binding(get: { shown }, set: setShown))
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
    @Environment(AppModel.self) private var app
    /// Kinds of Quiver's widgets on the desktop or in Notification Center.
    @State private var placed: Set<String> = []
    @State private var refreshed = false

    /// The snapshot a widget on the desktop would draw right now: what the
    /// app would write to the shared file, settings applied.
    private var snapshot: WidgetSnapshot {
        app.limits.widgetSnapshot(overview: app.overview, selection: app.widgetSelection)
    }

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
                .keyboardShortcut("r", modifiers: .command)
                .help("Redraw the widgets with the latest figures (⌘R)")
            }
            VStack(spacing: Theme.gutter) {
                ForEach(WidgetCatalog.all) { widget in
                    WidgetCard(widget: widget, isPlaced: placed.contains(widget.kind),
                               snapshot: snapshot)
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

/// A widget drawn as it would sit on the desktop: the system's size and
/// corner radius, the widget surface colour.
private struct WidgetSurface<Content: View>: View {
    var size: CGSize
    var snapshot: WidgetSnapshot
    @ViewBuilder var content: (WidgetSnapshot) -> Content

    var body: some View {
        content(snapshot)
            .padding(12)
            .frame(width: size.width, height: size.height)
            .background(Palette.card, in: .rect(cornerRadius: 20))
            .foregroundStyle(Palette.textPrimary)
            .overlay(RoundedRectangle(cornerRadius: 20).strokeBorder(Theme.border, lineWidth: 1))
    }
}

private struct WidgetCard: View {
    @Environment(AppModel.self) private var app
    var widget: WidgetCatalog
    var isPlaced: Bool
    var snapshot: WidgetSnapshot

    /// macOS desktop widget sizes: small, medium and the large only the
    /// limits widget ships.
    static let small = CGSize(width: 170, height: 170)
    static let medium = CGSize(width: 364, height: 170)
    static let large = CGSize(width: 364, height: 382)

    /// The harness widget this card configures, nil for the limits one.
    private var harnessWidget: WidgetSelection.Widget? {
        WidgetSelection.Widget(rawValue: widget.kind)
    }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
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
                previews
                settings
                Text(widget.sizes).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }

    /// The real widget view over the current snapshot, at the sizes it ships.
    @ViewBuilder private var previews: some View {
        let now = Date.now
        switch widget.kind {
        case "limits":
            FlowLayout(spacing: 16, lineSpacing: 16) {
                WidgetSurface(size: Self.small, snapshot: snapshot) {
                    LimitsView(snapshot: $0, now: now, family: .systemSmall)
                }
                WidgetSurface(size: Self.medium, snapshot: snapshot) {
                    LimitsView(snapshot: $0, now: now, family: .systemMedium)
                }
                WidgetSurface(size: Self.large, snapshot: snapshot) {
                    LimitsView(snapshot: $0, now: now, family: .systemLarge)
                }
            }
        case "summary":
            WidgetSurface(size: Self.medium, snapshot: snapshot) {
                SummaryView(snapshot: $0, now: now)
            }
        case "heatmap":
            WidgetSurface(size: Self.medium, snapshot: snapshot) {
                HeatmapView(snapshot: $0, now: now)
            }
        case "models":
            WidgetSurface(size: Self.medium, snapshot: snapshot) {
                ModelsView(snapshot: $0)
            }
        default: EmptyView()
        }
    }

    /// The widget's own settings: which sources and windows limits lists,
    /// or which harnesses the usage widgets count.
    @ViewBuilder private var settings: some View {
        if widget.kind == "limits" {
            let rows = MenuBarPopup.rows(in: app.limits.readings, sources: sources)
            if !rows.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Shown").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                    ForEach(rows) { row in
                        SourceSettingsRow(
                            row: row,
                            shown: app.widgetSelection.limits.shows(source: row.source),
                            setShown: { app.widgetSelection.limits.setShown($0, source: row.source) },
                            showsWindow: { app.widgetSelection.limits.shows(window: $0, source: row.source) },
                            setWindowShown: { app.widgetSelection.limits.setShown($1, window: $0, source: row.source) })
                    }
                }
            }
        } else if let harnessWidget {
            let harnesses = usageHarnesses
            if !harnesses.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Counted").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                    ForEach(harnesses, id: \.self) { harness in
                        HarnessPickRow(harness: harness, isOn: Binding(
                            get: { app.widgetSelection.shows(harness: harness, in: harnessWidget) },
                            set: { app.widgetSelection.setShown($0, harness: harness, in: harnessWidget) }))
                    }
                }
            }
        }
    }

    /// The same sources the popup's settings list: every starred account
    /// and other harness, so a source can be hidden before limits arrive.
    private var sources: [(source: String, harness: String, title: String)] {
        app.starredAccountCards.map {
            (source: $0.profile.id, harness: $0.profile.vendor.rawValue,
             title: "\($0.profile.vendor.title): \($0.profile.name)")
        } + app.others.rows.map { (source: $0.name, harness: $0.name, title: $0.accountTitle) }
    }

    /// Every harness with usage in the Overview data, in the Settings order.
    private var usageHarnesses: [String] {
        var names = Set((app.overview?.activityRows ?? []).map(\.harness))
        names.formUnion((app.overview?.recentRows ?? []).map(\.harness))
        return app.providers.ordered(Array(names))
    }
}

/// A harness the widget counts, with a switch.
private struct HarnessPickRow: View {
    var harness: String
    @Binding var isOn: Bool

    var body: some View {
        HStack(spacing: 10) {
            HarnessIcon(harness: harness, size: 16)
            Text(UsageFormat.harness(harness)).font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
            Spacer(minLength: 16)
            SettingSwitch(title: "Count \(UsageFormat.harness(harness))", isOn: $isOn)
        }
        .padding(.vertical, 12)
    }
}
