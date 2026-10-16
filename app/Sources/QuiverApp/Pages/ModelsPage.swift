import AppKit
import QuiverCore
import SwiftUI

/// Benchmarks from Artificial Analysis crossed with the models the ledger
/// actually saw. Bars are provider-coloured, used models carry a light
/// outline, and every label hovers into the shared model card. The data
/// lands in `~/.quiver/data/artificial-analysis/`; without a key the page
/// points at Settings › Services.
struct ModelsPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.models {
            ModelsContent(model: model)
        } else {
            Page(title: "Models",
                 subtitle: "Benchmarks for the models you use and the leaders.") {
                ErrorCard(message: app.overviewError ?? "The usage ledger is unavailable.")
            }
        }
    }
}

private struct ModelsContent: View {
    @Bindable var model: ModelsModel
    @Environment(AppModel.self) private var app
    /// The logged name the link sheet is editing.
    @State private var linking: String?
    @State private var linkError: String?
    /// The toolbar row's width and its two fixed clusters' ideals — the
    /// "Updated …" stamp shows only when all three fit on one row.
    @State private var toolbarWidth: CGFloat = 0
    @State private var controlsWidth: CGFloat = 0
    @State private var trailingWidth: CGFloat = 0
    @AppStorage("settingsSection") private var settingsSection = ""

    static let attribution = URL(string: "https://artificialanalysis.ai")!

    var body: some View {
        Page(title: "Models",
             subtitle: "Benchmarks for the models you use and the leaders. Data from",
             subtitleLink: ("Artificial Analysis.", Self.attribution)) {
            toolbar
            content
        }
        .task { await model.load() }
        .task(id: app.overview?.completedScans) {
            await model.loadIfNeeded(usageStamp: app.overview?.completedScans)
        }
        .sheet(isPresented: Binding(get: { linking != nil },
                                    set: { if !$0 { linking = nil; linkError = nil } })) {
            if let linking {
                LinkSheet(model: model, logged: linking, error: $linkError) {
                    do {
                        try model.link(logged: linking, to: $0)
                        self.linking = nil
                    } catch {
                        linkError = "Couldn't save the link: \(error.localizedDescription)"
                    }
                }
            }
        }
    }

    // MARK: - Toolbar

    /// The controls row: view mode, metric, scope, harness and period on
    /// the left; the freshness stamp, warning and reload buttons on the
    /// right. When the row runs out of room the stamp hides and its text
    /// moves into the reload button's hover help — nothing else gives.
    private var toolbar: some View {
        HStack(spacing: 8) {
            HStack(spacing: 8) {
                Tabs(options: [(ModelsModel.ViewMode.chart, "Chart"),
                               (ModelsModel.ViewMode.table, "Table")],
                     selection: Binding(get: { model.viewMode },
                                        set: { model.set(viewMode: $0) }),
                     style: .segmented)
                SelectMenu(options: ModelsModel.Metric.allCases.map { ($0, $0.title) },
                           selection: Binding(get: { model.metric },
                                              set: { model.set(metric: $0) }),
                           width: nil)
                SelectMenu(options: ModelsModel.Scope.allCases.map { ($0, $0.title) },
                           selection: Binding(get: { model.scope },
                                              set: { model.set(scope: $0) }),
                           width: nil)
                SelectMenu(options: [(value: String?.none, title: "All harnesses", icon: nil)]
                           + model.harnessOptions.map {
                               (value: $0, title: HarnessMark($0).name, icon: $0)
                           },
                           selection: Binding(get: { model.harnessFilter },
                                              set: { model.set(harnessFilter: $0) }),
                           width: nil)
                SelectMenu(options: OverviewPeriod.allCases.map { ($0, $0.title) },
                           selection: Binding(get: { model.period },
                                              set: { value in
                                                  Task<Void, Never> { await model.set(period: value) }
                                              }),
                           width: nil)
            }
            .fixedSize()
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { controlsWidth = $0 }
            Spacer(minLength: 8)
            if let updatedAt = model.updatedAt, roomForStamp {
                Text("Updated \(updatedAt.formatted(.relative(presentation: .named)))")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1).fixedSize()
            }
            HStack(spacing: 8) {
                if !model.unmatched.isEmpty {
                    UnmatchedBadge(model: model) { linking = $0 }
                }
                reload
            }
            .fixedSize()
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { trailingWidth = $0 }
        }
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { toolbarWidth = $0 }
    }

    /// Whether the stamp fits beside the controls and the buttons. The
    /// clusters are fixedSize, so their rendered width is their ideal.
    private var roomForStamp: Bool {
        guard let updatedAt = model.updatedAt else { return false }
        let text = "Updated \(updatedAt.formatted(.relative(presentation: .named)))"
        let stamp = (text as NSString)
            .size(withAttributes: [.font: NSFont.systemFont(ofSize: 12)]).width
        return controlsWidth + 8 + stamp + 8 + trailingWidth <= toolbarWidth
    }

    /// The freshness stamp lives here too, for when the row drops it.
    private var reloadHelp: String {
        let base = model.refreshError
            ?? "Fetch the latest benchmarks from Artificial Analysis"
        guard let updatedAt = model.updatedAt else { return base }
        return "Updated \(updatedAt.formatted(.relative(presentation: .named))). \(base)"
    }

    private var reload: some View {
        Button { Task { await model.refresh() } } label: {
            Image(systemName: "arrow.clockwise")
                .rotationEffect(.degrees(model.isRefreshing ? 360 : 0))
                .animation(model.isRefreshing
                           ? .linear(duration: 1).repeatForever(autoreverses: false)
                           : .default,
                           value: model.isRefreshing)
        }
        .iconButton()
        .disabled(model.isRefreshing || !model.hasKey)
        .help(reloadHelp)
    }

    // MARK: - Content

    @ViewBuilder private var content: some View {
        // Cached benchmarks still show after the key is removed.
        if !model.hasKey && !model.isLoading && model.allModels.isEmpty {
            noKey
        } else if model.allModels.isEmpty {
            if model.isLoading || model.isRefreshing {
                LoadingCard(text: "Fetching benchmarks…")
            } else if let error = model.refreshError {
                ErrorCard(message: "Couldn't fetch benchmarks: \(error)")
            } else {
                EmptyCard(text: "No benchmark data yet.", icon: "cube.transparent")
            }
        } else if model.rows.isEmpty {
            EmptyCard(text: "No usage in this period.", icon: "chart.bar")
        } else {
            switch model.viewMode {
            case .chart: ChartCard(model: model)
            case .table: TableCard(model: model)
            }
        }
    }

    /// No key: point at Settings › Services instead of an empty chart.
    private var noKey: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                Text("Add an Artificial Analysis key in Settings › Services")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Text("The Models page needs a free Artificial Analysis API key to download benchmarks. 1,000 requests a day are free.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Button("Open Settings › Services") {
                    settingsSection = SettingsSection.services.rawValue
                    app.selection = .settings
                }
                .secondaryButton()
            }
        }
    }
}

// MARK: - Warning popover

/// The ⚠︎ button: used models the matcher couldn't place, each opening
/// the link sheet on click.
private struct UnmatchedBadge: View {
    var model: ModelsModel
    var open: (String) -> Void

    @State private var show = false
    @State private var rest: Task<Void, Never>?

    var body: some View {
        Button { show.toggle() } label: {
            Image(systemName: "exclamationmark.triangle")
                .foregroundStyle(Theme.warningText)
        }
        .iconButton()
        .help(model.unmatched.count == 1
              ? "1 model you used isn't on Artificial Analysis"
              : "\(model.unmatched.count) models you used aren't on Artificial Analysis")
        .onHover { inside in
            if inside {
                rest = Task {
                    try? await Task.sleep(for: .milliseconds(150))
                    guard !Task.isCancelled else { return }
                    show = true
                }
            } else {
                rest?.cancel()
            }
        }
        .popover(isPresented: $show, arrowEdge: .bottom) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(model.unmatched) { row in
                    Button { show = false; open(row.model) } label: {
                        HStack(spacing: 6) {
                            Text(row.model).font(Theme.caption).foregroundStyle(Theme.textPrimary)
                                .lineLimit(1).truncationMode(.middle)
                            Spacer()
                            Text(UsageFormat.tokens(row.tokens))
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
                        }
                        .padding(.horizontal, 10).padding(.vertical, 7)
                        .frame(width: 300)
                        .contentShape(.rect)
                    }
                    .plainButton(cornerRadius: 0)
                    .modifier(RowHover())
                }
            }
            .padding(.vertical, 6)
        }
    }
}

// MARK: - Chart

/// The metric chart: one provider-coloured bar per model, best first, the
/// value inside the bar and the logo plus angled name under it. Used
/// models keep a light inset outline; bars scroll before they shrink past
/// 28pt. Hovering a bar (the bar itself, not its column or label) shows
/// its model card beside it: one card for the whole chart, moved and
/// refilled as the pointer crosses bars, so neighbouring bars never open
/// cards of their own.
struct ChartCard: View {
    @Bindable var model: ModelsModel

    /// Bars never get narrower than this; the row scrolls instead.
    static let minColumnWidth: CGFloat = 36
    /// The bar zone; every bar bottoms out on the same baseline.
    static let barHeight: CGFloat = 280
    /// The provider logo and the zone it sits in.
    static let logoSize: CGFloat = 18
    static let logoHeight: CGFloat = 28
    /// The label zone: each name ends under its bar's middle and runs
    /// down-left at `labelAngle`, Artificial Analysis style.
    static let labelHeight: CGFloat = 150
    static let labelAngle: Double = 60
    static let labelFont: CGFloat = 10.5
    /// A name's cap along its slant: as long as the zone is deep.
    static let labelRun: CGFloat = 160
    /// The strip's side padding and the gap between bars.
    static let inset: CGFloat = 6
    static let spacing: CGFloat = 8
    /// The space the hover card is laid out in.
    private static let space = "modelChart"

    @State private var width: CGFloat = 0
    /// The column whose card shows, by index into `model.rows`.
    @State private var hovered: Int?
    /// The bar the pointer is resting on before the first card shows;
    /// once one shows, the next bar's replaces it at once.
    @State private var pending: Int?
    @State private var rest: Task<Void, Never>?
    static let openDelay: Duration = .milliseconds(400)
    /// How long a card outlives its bar, so crossing the gap to the next
    /// bar switches the card instead of closing it.
    static let closeDelay: Duration = .milliseconds(180)
    /// The bar strip's frame in the chart's space; moves as it scrolls.
    @State private var strip: CGRect = .zero
    /// The chart area's size, for keeping the card inside it.
    @State private var area: CGSize = .zero
    @State private var cardHeight: CGFloat = 0

    private var columnWidth: CGFloat {
        guard !model.rows.isEmpty else { return Self.minColumnWidth }
        return max(Self.minColumnWidth, (width - 2 * Self.inset - leadingRoom) / CGFloat(model.rows.count))
    }

    /// Extra space before the first bar so its slanted name, which runs
    /// left of the bar's middle, isn't cut off by the strip's edge.
    private var leadingRoom: CGFloat {
        Self.leadingRoom(reach: model.rows.first.map(BarColumn.labelReach) ?? 0,
                         width: width, count: model.rows.count)
    }

    /// The padding P that puts the first bar's middle at least `reach`
    /// from the strip's edge, against the column width that results once
    /// P itself is taken out: P + inset + (column − spacing) / 2 ≥ reach,
    /// with column = (width − 2·inset − P) / count, or the minimum width
    /// once that floors.
    static func leadingRoom(reach: CGFloat, width: CGFloat, count: Int) -> CGFloat {
        guard count > 0, reach > 0 else { return 0 }
        let n = CGFloat(count)
        let flexible = (reach - inset + spacing / 2 - (width - 2 * inset) / (2 * n)) / (1 - 1 / (2 * n))
        var room = max(0, flexible)
        if (width - 2 * inset - room) / n < minColumnWidth {
            room = max(0, reach - inset - (minColumnWidth - spacing) / 2)
        }
        return room.rounded(.up)
    }

    /// The tallest bar: every height is a fraction of it.
    private var peak: Double {
        model.rows.compactMap(\.value).max() ?? 1
    }

    var body: some View {
        Card(title: model.metric.chartTitle, subtitle: model.metric.detail) {
            VStack(alignment: .leading, spacing: 10) {
                Color.clear.frame(maxWidth: .infinity, maxHeight: 0)
                    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
                ScrollView(.horizontal) {
                    HStack(alignment: .top, spacing: Self.spacing) {
                        ForEach(Array(model.rows.enumerated()), id: \.element.id) { index, row in
                            BarColumn(row: row, metric: model.metric, peak: peak) { inside in
                                if inside { rest(on: index) } else { leave(index) }
                            }
                            .frame(width: columnWidth - Self.spacing)
                        }
                    }
                    .padding(.horizontal, Self.inset)
                    .padding(.leading, leadingRoom)
                    .onGeometryChange(for: CGRect.self) {
                        $0.frame(in: .named(Self.space))
                    } action: { strip = $0 }
                }
                .scrollIndicators(.hidden)
                // Page enables selectable text. Rotated chart labels must stay SwiftUI
                // text: AppKit's selectable-text layout aborts in _nsis_frameInEngine.
                .textSelection(.disabled)
                legend
                HStack {
                    Text("Hover a bar for the model card")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    Spacer()
                    Link("Data from Artificial Analysis", destination: ModelsContent.attribution)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            .coordinateSpace(.named(Self.space))
            .onGeometryChange(for: CGSize.self) { $0.size } action: { area = $0 }
            .overlay(alignment: .topLeading) { hoverCard }
        }
    }

    /// Shows `index`'s card once the pointer has stayed on it for the
    /// open delay, so sweeping across the chart never flashes cards. With
    /// a card already up, moving to another bar swaps it in at once.
    private func rest(on index: Int?) {
        if index == hovered {
            rest?.cancel()
            pending = nil
            return
        }
        // A card is already showing: the next bar's card replaces it at
        // once, in its new place, rather than sliding over.
        if hovered != nil, let index {
            rest?.cancel()
            pending = nil
            hovered = index
            return
        }
        guard index != pending else { return }
        pending = index
        rest?.cancel()
        rest = Task { @MainActor in
            try? await Task.sleep(for: Self.openDelay)
            guard !Task.isCancelled else { return }
            hovered = index
            pending = nil
        }
    }

    /// The pointer left `index`'s bar. Its card closes after the switch
    /// delay, so crossing the gap to the next bar hands the card over
    /// instead of closing and reopening it. Leaving a bar that is neither
    /// showing nor about to show changes nothing: the enter on the next
    /// bar can arrive before the leave on this one.
    private func leave(_ index: Int) {
        guard (pending ?? hovered) == index else { return }
        pending = nil
        rest?.cancel()
        rest = Task { @MainActor in
            try? await Task.sleep(for: Self.closeDelay)
            guard !Task.isCancelled else { return }
            hovered = nil
        }
    }

    /// The card beside the hovered bar: to its right, or to its left
    /// when the right would run out of the chart, top-aligned with the
    /// tallest bar and kept inside the chart area.
    @ViewBuilder private var hoverCard: some View {
        if let index = hovered, model.rows.indices.contains(index) {
            let left = strip.minX + Self.inset + leadingRoom + CGFloat(index) * columnWidth
            let right = left + columnWidth - Self.spacing
            let gap: CGFloat = 10
            let x = right + gap + ModelCard.width <= area.width
                ? right + gap
                : max(0, left - gap - ModelCard.width)
            let y = min(max(strip.minY, 0), max(0, area.height - cardHeight))
            ModelCard(row: model.rows[index], period: model.period)
                .background(Theme.card, in: .rect(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.border, lineWidth: 1))
                .shadow(color: .black.opacity(0.25), radius: 16, y: 6)
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { cardHeight = $0 }
                .textSelection(.disabled)
                .allowsHitTesting(false)
                .offset(x: x, y: y)
        }
    }

    /// One swatch per provider, ordered by first appearance in the chart.
    private var legend: some View {
        var order: [ModelVendor] = []
        var seen: Set<String> = []
        for row in model.rows {
            guard let aa = row.aa else { continue }
            let vendor = ModelVendor.of(aa)
            if seen.insert(vendor.id).inserted { order.append(vendor) }
        }
        return FlowLayout(spacing: 14, lineSpacing: 6) {
            ForEach(order, id: \.id) { vendor in
                HStack(spacing: 5) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color(rgb: vendor.color))
                        .frame(width: 10, height: 10)
                    Text(vendor.name)
                        .font(.system(size: 10.5)).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }
}

private struct BarColumn: View {
    var row: ModelsModel.Row
    var metric: ModelsModel.Metric
    /// The chart's largest value; heights are fractions of it.
    var peak: Double
    /// The pointer entered (true) or left (false) the bar itself.
    var hover: (Bool) -> Void

    private var vendor: ModelVendor {
        row.aa.map { ModelVendor.of($0) } ?? ModelVendor.of(logged: row.logged)
    }

    /// Bar text reads dark on light fills (OpenAI's grey) — a fixed
    /// near-black, not the theme's dark-mode white.
    private var valueColor: Color {
        vendor.color.luminance > 0.45 ? Color(hex: "#0a0a0a") : .white
    }

    /// The name cut at `labelRun` (head ellipsis), so the end under the
    /// bar always shows. Measured at the label font.
    static func label(_ row: ModelsModel.Row) -> String {
        let name = row.aa.map(ModelMatcher.displayName) ?? row.logged
        var cut = Substring(name)
        while !cut.isEmpty, width(of: "…" + cut) > ChartCard.labelRun {
            cut = cut.dropFirst()
        }
        return cut.count == name.count ? name : "…" + cut
    }

    /// How far left of the bar's middle the slanted name reaches: its
    /// length along the slant plus the half line height its far corner
    /// sticks out sideways.
    static func labelReach(_ row: ModelsModel.Row) -> CGFloat {
        let angle = ChartCard.labelAngle * .pi / 180
        let lineHeight = NSFont.systemFont(ofSize: ChartCard.labelFont).boundingRectForFont.height
        return width(of: label(row)) * cos(angle) + lineHeight / 2 * sin(angle)
    }

    private static func width(of text: String) -> CGFloat {
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: ChartCard.labelFont)]
        return (text as NSString).size(withAttributes: attrs).width
    }

    var body: some View {
        VStack(spacing: 0) {
            // Zone 1: the bar, bottom-aligned on the shared baseline.
            let fraction = row.value.map { max($0 / max(peak, .leastNonzeroMagnitude), 0) } ?? 0
            RoundedRectangle(cornerRadius: 4)
                .fill(row.value == nil ? AnyShapeStyle(Theme.subtle) : AnyShapeStyle(Color(rgb: vendor.color)))
                .frame(height: row.value == nil ? 8 : max(fraction * ChartCard.barHeight, 20))
                .overlay(alignment: .top) {
                    if let value = row.value {
                        Text(metric.format(value))
                            .font(.system(size: 10, weight: .medium)).monospacedDigit()
                            .foregroundStyle(valueColor)
                            .lineLimit(1).minimumScaleFactor(0.6)
                            .padding(.top, 4)
                    }
                }
                .overlay {
                    if row.used {
                        RoundedRectangle(cornerRadius: 4)
                            .strokeBorder(.white.opacity(0.9), lineWidth: 1.5)
                            .padding(1)
                    }
                }
                // Only the bar opens the card, not the empty space above
                // it or the logo and name below.
                .contentShape(.rect)
                .onHover(perform: hover)
                .frame(height: ChartCard.barHeight, alignment: .bottom)
            // Zone 2: the provider logo.
            Group {
                if let icon = vendor.icon, HarnessIcon.exists(for: icon) {
                    HarnessIcon(harness: icon, size: ChartCard.logoSize)
                } else {
                    Color.clear.frame(width: ChartCard.logoSize, height: ChartCard.logoSize)
                }
            }
            .frame(height: ChartCard.logoHeight)
            // Zone 3: the name. Its end sits on a point under the bar's
            // middle and it runs down-left at `labelAngle`, so every name
            // lands on its own bar whatever its length.
            Color.clear
                .frame(height: ChartCard.labelHeight)
                .overlay(alignment: .top) {
                    Color.clear.frame(width: 0, height: 0)
                        .overlay(alignment: .trailing) {
                            Text(Self.label(row))
                                .font(.system(size: ChartCard.labelFont))
                                .foregroundStyle(row.used ? Theme.textPrimary : Theme.textSecondary)
                                .lineLimit(1)
                                .fixedSize()
                                .rotationEffect(.degrees(-ChartCard.labelAngle), anchor: .trailing)
                        }
                        .padding(.top, 6)
                }
        }
    }
}

// MARK: - Table

/// The same rows as a table: who ran it, the headline benchmarks, cost
/// rank and the user's tokens.
private struct TableCard: View {
    @Bindable var model: ModelsModel

    var body: some View {
        Card {
            // Narrow windows scroll sideways instead of crushing the
            // model names under the fixed columns.
            ScrollView(.horizontal) {
                VStack(spacing: 0) {
                    header
                    Divider().overlay(Theme.border)
                    ForEach(model.rows) { row in
                        TableRow(row: row, model: model)
                        Divider().overlay(Theme.rowDivider)
                    }
                }
                .containerRelativeFrame(.horizontal) { width, _ in max(width, Self.minWidth) }
            }
            .scrollIndicators(.automatic)
        }
    }

    /// The fixed columns plus room for a model name.
    static let minWidth: CGFloat = 760

    private var header: some View {
        HStack(spacing: 0) {
            Text("Model").frame(maxWidth: .infinity, alignment: .leading)
            Text("Provider").frame(width: 90, alignment: .leading)
            Text("Intelligence").frame(width: 80, alignment: .trailing)
            Text("Coding").frame(width: 64, alignment: .trailing)
            Text("Speed").frame(width: 64, alignment: .trailing)
            // The picked metric shows when it has no column of its own.
            if model.metric == .maths || model.metric == .ttft {
                Text(model.metric == .ttft ? "TTFT" : model.metric.title)
                    .frame(width: 64, alignment: .trailing)
            }
            Text("Cost").frame(width: 120, alignment: .leading).padding(.leading, 16)
            Text("Your tokens").frame(width: 80, alignment: .trailing)
        }
        .font(Theme.caption).foregroundStyle(Theme.tableHeaderText)
        .padding(.vertical, 6)
    }
}

private struct TableRow: View {
    var row: ModelsModel.Row
    var model: ModelsModel

    private var vendor: ModelVendor {
        row.aa.map { ModelVendor.of($0) } ?? ModelVendor.of(logged: row.logged)
    }

    var body: some View {
        HStack(spacing: 0) {
            HStack(spacing: 6) {
                ForEach(row.harnesses.prefix(3), id: \.self) { harness in
                    HarnessIcon(harness: harness, size: 14)
                        .accessibilityLabel(UsageFormat.harness(harness))
                }
                if row.harnesses.count > 3 {
                    Text("+\(row.harnesses.count - 3)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                ModelName(name: row.aa.map(ModelMatcher.displayName) ?? row.logged,
                          qualifier: row.qualifier)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(vendor.name).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .frame(width: 90, alignment: .leading)
            metric(row.aa?.intelligence).frame(width: 80, alignment: .trailing)
            metric(row.aa?.coding).frame(width: 64, alignment: .trailing)
            metric(row.aa?.speed, unit: "t/s").frame(width: 64, alignment: .trailing)
            if model.metric == .maths || model.metric == .ttft {
                Text(row.value.map(model.metric.format) ?? "—")
                    .font(Theme.bodySmall).monospacedDigit()
                    .foregroundStyle(row.value == nil ? Theme.textMuted : Theme.textPrimary)
                    .frame(width: 64, alignment: .trailing)
            }
            CostScale(model: row.aa, rank: row.costPercentile)
                .frame(width: 120, alignment: .leading).padding(.leading, 16)
            Text(row.tokens > 0 ? UsageFormat.tokens(row.tokens) : "—")
                .font(Theme.bodySmall).monospacedDigit().foregroundStyle(Theme.textSecondary)
                .frame(width: 80, alignment: .trailing)
        }
        .padding(.vertical, 8)
        .contentShape(.rect)
        .modelCard(model, for: row.logged, row: row)
    }

    @ViewBuilder private func metric(_ value: Double?, unit: String? = nil) -> some View {
        if let value {
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                Text("\(Int(value.rounded()))")
                    .font(Theme.bodySmall).monospacedDigit().foregroundStyle(Theme.textPrimary)
                if let unit {
                    Text(unit).font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
        } else {
            Text("—").font(Theme.bodySmall).foregroundStyle(Theme.textMuted)
        }
    }
}

/// The cost column: a mini green→purple gradient with the model's rank
/// as a white dot centred on it.
private struct CostScale: View {
    var model: AALanguageModel?
    var rank: Double?

    var body: some View {
        HStack(spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(LinearGradient(colors: [Color(hex: "#2fa35a"), Color(hex: "#e8913a"),
                                                      Color(hex: "#a46ae0")],
                                             startPoint: .leading, endPoint: .trailing))
                        .frame(height: 6)
                    if let rank {
                        Circle().fill(.white).frame(width: 8, height: 8)
                            .shadow(color: .black.opacity(0.25), radius: 1, y: 0.5)
                            .position(x: min(max(rank * geo.size.width, 4), geo.size.width - 4),
                                      y: geo.size.height / 2)
                    }
                }
            }
            .frame(width: 64, height: 12)
            if let price = model?.priceBlended {
                Text(UsageFormat.dollars(price))
                    .font(Theme.caption).monospacedDigit().foregroundStyle(Theme.textSecondary)
            } else {
                Text("—").font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }
}

// MARK: - Link sheet

/// "Link to a model": a searchable list of every AA model, picked to
/// teach the matcher what a logged name means. Writes model-links.json.
private struct LinkSheet: View {
    var model: ModelsModel
    var logged: String
    @Binding var error: String?
    var pick: (String) -> Void

    @State private var query = ""

    private var candidates: [AALanguageModel] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        let all = model.allModels.sorted {
            ($0.intelligence ?? 0, $0.name) > ($1.intelligence ?? 0, $1.name)
        }
        guard !q.isEmpty else { return all }
        return all.filter {
            $0.name.lowercased().contains(q) || $0.slug.lowercased().contains(q)
                || $0.creatorName.lowercased().contains(q)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Link to a model").font(Theme.sectionTitle).tracking(-0.5)
                Text("“\(logged)” isn't on Artificial Analysis. Pick the model it should count as; the link is saved in model-links.json.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
            if let error {
                Text(error).font(Theme.caption).foregroundStyle(Theme.dangerText)
            }
            TextField("Search models…", text: $query)
                .quiverField()
            ScrollView {
                LazyVStack(spacing: 0) {
                    ForEach(candidates) { aa in
                        Button { pick(aa.id) } label: {
                            HStack(spacing: 8) {
                                let vendor = ModelVendor.of(aa)
                                if let icon = vendor.icon, HarnessIcon.exists(for: icon) {
                                    HarnessIcon(harness: icon, size: 16)
                                }
                                Text(aa.name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                                    .lineLimit(1).truncationMode(.tail)
                                Spacer()
                                Text(vendor.name)
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                if let intelligence = aa.intelligence {
                                    Text("\(Int(intelligence.rounded()))")
                                        .font(Theme.caption).monospacedDigit()
                                        .foregroundStyle(Theme.textSecondary)
                                }
                            }
                            .padding(.horizontal, 10).padding(.vertical, 8)
                            .contentShape(.rect)
                        }
                        .plainButton(cornerRadius: 6)
                        .modifier(RowHover())
                    }
                }
            }
            .frame(maxHeight: .infinity)
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
        }
        .padding(24)
        .frame(width: 460, height: 480)
        .background(Theme.panel)
    }
}

/// The row-hover fill, as Overview and Pricing rows get.
private struct RowHover: ViewModifier {
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .background(hovering ? Theme.rowHover : .clear)
            .onHover { hovering = $0 }
    }
}

private extension Color {
    init(rgb: HarnessRGB) { self.init(red: rgb.red, green: rgb.green, blue: rgb.blue) }
}
