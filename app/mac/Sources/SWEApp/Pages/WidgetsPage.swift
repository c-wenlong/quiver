import SWECore
import SWEWidgetsUI
import SwiftUI
import WidgetKit

/// The four desktop widgets, one card per kind marked when it's placed,
/// picked by the same underline tabs the Menu bar page uses. macOS has
/// no way for an app to add a widget, so the subtitle says how.
struct WidgetsPage: View {
    @Environment(AppModel.self) private var app
    /// Kinds of SWE's widgets on the desktop or in Notification Center.
    @State private var placed: Set<String> = []
    /// One widget's card at a time, picked by the tabs; remembered on this Mac.
    @AppStorage("desktopWidgetTab") private var widgetTab = "limits"

    /// The snapshot a widget on the desktop would draw right now: what the
    /// app would write to the shared file, settings applied.
    private var snapshot: WidgetSnapshot {
        let style = app.widgetHarnessStyle
        return app.limits.widgetSnapshot(overview: app.overview, selection: app.widgetSelection,
                                         harnessOrder: style.order, harnessColors: style.colors,
                                         compute: app.widgetCompute)
    }

    /// The tab's widget, falling back to the first for a stored kind that
    /// no longer ships.
    private var widget: WidgetCatalog {
        WidgetCatalog.all.first { $0.kind == widgetTab } ?? WidgetCatalog.all[0]
    }

    var body: some View {
        Page(title: "Widgets",
             subtitle: "What SWE's desktop widgets show. Right-click the desktop, choose Edit widgets, and search for SWE.") {
            Button("Refresh widgets", systemImage: "arrow.clockwise") {
                WidgetCenter.shared.reloadAllTimelines()
                Task { await loadPlaced() }
            }
            .iconButton()
            .keyboardShortcut("r", modifiers: .command)
            .help("Redraw the widgets with the latest figures (⌘R)")
        } content: {
            VStack(alignment: .leading, spacing: 24) {
                Tabs(options: WidgetCatalog.all.map { ($0.kind, $0.title) },
                     selection: $widgetTab, style: .underline)
                WidgetCard(widget: widget, isPlaced: placed.contains(widget.kind),
                           snapshot: snapshot)
            }
            .task { await loadPlaced() }
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                Task { await loadPlaced() }
            }
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

/// The WidgetKit corner radius, shared by a preview's background and hairline.
private let widgetSurfaceRadius: CGFloat = 20

/// A widget drawn as it would sit on the desktop: the system's size and
/// corner radius, the widget surface colour.
private struct WidgetSurface<Content: View>: View {
    var size: CGSize
    var snapshot: WidgetSnapshot
    @ViewBuilder var content: (WidgetSnapshot) -> Content

    var body: some View {
        // 16pt on each side matches the content margins WidgetKit gives a
        // macOS desktop widget, so the preview's content area is the real one.
        content(snapshot)
            .padding(16)
            .frame(width: size.width, height: size.height)
            .background(Palette.card, in: .rect(cornerRadius: widgetSurfaceRadius))
            .foregroundStyle(Palette.textPrimary)
            .overlay(RoundedRectangle(cornerRadius: widgetSurfaceRadius).strokeBorder(Theme.border, lineWidth: 1))
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
                        .font(Theme.font(size: 14))
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
            }
        }
    }

    /// The real widget view over the current snapshot, at the sizes it ships.
    @ViewBuilder private var previews: some View {
        let now = Date.now
        switch widget.kind {
        case "limits":
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 16) {
                    WidgetSurface(size: Self.large, snapshot: snapshot) {
                        LimitsView(snapshot: $0, now: now, family: .systemLarge)
                    }
                    VStack(alignment: .leading, spacing: 16) {
                        WidgetSurface(size: Self.medium, snapshot: snapshot) {
                            LimitsView(snapshot: $0, now: now, family: .systemMedium)
                        }
                        WidgetSurface(size: Self.small, snapshot: snapshot) {
                            LimitsView(snapshot: $0, now: now, family: .systemSmall)
                        }
                    }
                }
                FlowLayout(spacing: 16, lineSpacing: 16) {
                    WidgetSurface(size: Self.large, snapshot: snapshot) {
                        LimitsView(snapshot: $0, now: now, family: .systemLarge)
                    }
                    WidgetSurface(size: Self.medium, snapshot: snapshot) {
                        LimitsView(snapshot: $0, now: now, family: .systemMedium)
                    }
                    WidgetSurface(size: Self.small, snapshot: snapshot) {
                        LimitsView(snapshot: $0, now: now, family: .systemSmall)
                    }
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
        case "compute":
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 16) {
                    WidgetSurface(size: Self.medium, snapshot: snapshot) {
                        ComputeView(snapshot: $0, now: now, family: .systemMedium)
                    }
                    WidgetSurface(size: Self.small, snapshot: snapshot) {
                        ComputeView(snapshot: $0, now: now, family: .systemSmall)
                    }
                }
                FlowLayout(spacing: 16, lineSpacing: 16) {
                    WidgetSurface(size: Self.medium, snapshot: snapshot) {
                        ComputeView(snapshot: $0, now: now, family: .systemMedium)
                    }
                    WidgetSurface(size: Self.small, snapshot: snapshot) {
                        ComputeView(snapshot: $0, now: now, family: .systemSmall)
                    }
                }
            }
        default: EmptyView()
        }
    }

    /// The widget's own settings: which sources and windows limits lists,
    /// or which harnesses the usage widgets count.
    @ViewBuilder private var settings: some View {
        if widget.kind == "limits" {
            let limits = limitRows
            if !limits.rows.isEmpty {
                let shown = limits.shown
                VStack(alignment: .leading, spacing: 0) {
                    Text("Shown").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                    Text("Drag to reorder.").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .padding(.bottom, 4)
                    ReorderableRows(limits.rows, showsDividers: true) { id, before in
                        if let before {
                            app.widgetSelection.limits.move(id, before: before, in: shown)
                        } else {
                            app.widgetSelection.limits.moveToEnd(id, in: shown)
                        }
                    } row: { row in
                        SourceSettingsRow(
                            row: row, grip: true,
                            shown: app.widgetSelection.limits.shows(source: row.source),
                            setShown: { app.widgetSelection.limits.setShown($0, source: row.source) },
                            showsWindow: { app.widgetSelection.limits.shows(window: $0, source: row.source) },
                            setWindowShown: { app.widgetSelection.limits.setShown($1, window: $0, source: row.source) })
                    }
                }
            }
        } else if let harnessWidget {
            let harnesses = usageHarnesses(for: harnessWidget)
            if harnessWidget == .summary || !harnesses.isEmpty {
                VStack(alignment: .leading, spacing: 0) {
                    Text("Counted").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
                    if harnessWidget == .summary {
                        TotalPickRow(isOn: Binding(
                            get: { app.widgetSelection.summaryShowsTotal },
                            set: { app.widgetSelection.summaryShowsTotal = $0 }))
                        if !harnesses.isEmpty { SettingsDivider() }
                    }
                    ForEach(harnesses, id: \.self) { harness in
                        HarnessPickRow(harness: harness, isOn: Binding(
                            get: { app.widgetSelection.shows(harness: harness, in: harnessWidget) },
                            set: { app.widgetSelection.setShown($0, harness: harness, in: harnessWidget) }))
                    }
                }
            }
        }
    }

    /// The limits sources in display order: the dragged order over
    /// `defaultRank` of the live (byPressure) readings, matching what
    /// WidgetSnapshot.make draws; a source with no reading yet goes last,
    /// in row order. Hiding a window or source never moves its row.
    /// `shown` is the ids the ReorderableRows callbacks reorder within.
    private var limitRows: (rows: [MenuBarPopup.Row], shown: [String]) {
        let rows = MenuBarPopup.rows(in: app.limits.readings, sources: sources)
        let withReadings = app.widgetSelection.limits.defaultRank(app.limits.readings)
        let ranked = withReadings + rows.map(\.source).filter { !withReadings.contains($0) }
        let shown = app.widgetSelection.limits.ordered(ranked)
        let bySource = Dictionary(uniqueKeysWithValues: rows.map { ($0.source, $0) })
        return (rows: shown.compactMap { bySource[$0] }, shown: shown)
    }

    /// The same sources the popup's settings list: every starred account
    /// and other harness, so a source can be hidden before limits arrive.
    private var sources: [(source: String, harness: String, title: String)] {
        app.starredAccountCards.map {
            (source: $0.profile.id, harness: $0.profile.vendor.rawValue,
             title: "\($0.profile.vendor.title): \($0.profile.name)")
        } + app.others.rows.map { (source: $0.id, harness: $0.name, title: $0.accountTitle) }
    }

    /// Every harness the widget's switch list offers: ones with usage in
    /// the Overview data plus any its hidden set still names, in the
    /// Settings order. The Compute widget's list also takes the starred
    /// harnesses swe knows, since a running one may have no usage yet.
    private func usageHarnesses(for widget: WidgetSelection.Widget) -> [String] {
        var names = Set((app.overview?.activityRows ?? []).map(\.harness))
        names.formUnion((app.overview?.recentRows ?? []).map(\.harness))
        if widget == .compute { names.formUnion(app.compute.matchers.map(\.harness)) }
        let listed = app.widgetSelection.listedHarnesses(withUsage: names, for: widget)
        return app.providers.ordered(Array(listed))
    }
}

/// The summary widget's aggregate line, switched on its own.
private struct TotalPickRow: View {
    @Binding var isOn: Bool

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "sum")
                .font(Theme.font(size: 12))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 16, height: 16)
            Text("Total").font(Theme.bodySmall).foregroundStyle(Theme.textLabel)
            Spacer(minLength: 16)
            SettingSwitch(title: "Show the total line", isOn: $isOn)
        }
        .padding(.vertical, 12)
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
