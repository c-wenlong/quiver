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
    /// the left (wrapping under narrow windows), and the freshness stamp
    /// plus the warning and reload buttons on the right.
    private var toolbar: some View {
        HStack(alignment: .top, spacing: 8) {
            FlowLayout(spacing: 8, lineSpacing: 8) {
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
            Spacer(minLength: 8)
            HStack(spacing: 8) {
                if let updatedAt = model.updatedAt {
                    Text("Updated \(updatedAt.formatted(.relative(presentation: .named)))")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        .lineLimit(1).fixedSize()
                }
                if !model.unmatched.isEmpty {
                    UnmatchedBadge(model: model) { linking = $0 }
                }
                reload
            }
        }
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
        .help(model.refreshError
              ?? "Fetch the latest benchmarks from Artificial Analysis")
    }

    // MARK: - Content

    @ViewBuilder private var content: some View {
        if !model.hasKey && !model.isLoading {
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
            case .chart: ChartCard(model: model, link: { linking = $0 })
            case .table: TableCard(model: model, link: { linking = $0 })
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
/// 28pt.
private struct ChartCard: View {
    @Bindable var model: ModelsModel
    var link: (String) -> Void

    /// Bars never get narrower than this; the row scrolls instead.
    static let minColumnWidth: CGFloat = 36
    static let barHeight: CGFloat = 280
    /// Room for the angled names under the bars.
    static let labelHeight: CGFloat = 130

    @State private var width: CGFloat = 0

    private var columnWidth: CGFloat {
        guard !model.rows.isEmpty else { return Self.minColumnWidth }
        return max(Self.minColumnWidth, (width - 12) / CGFloat(model.rows.count))
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
                    HStack(alignment: .bottom, spacing: 8) {
                        ForEach(model.rows) { row in
                            BarColumn(row: row, metric: model.metric, peak: peak)
                                .frame(width: columnWidth - 8)
                                .modelCard(model, for: row.logged,
                                           link: row.aa == nil ? { link(row.logged) } : nil)
                        }
                    }
                    .padding(.horizontal, 6)
                    .frame(height: Self.barHeight + Self.labelHeight, alignment: .bottom)
                }
                .scrollIndicators(.hidden)
                legend
                HStack {
                    Text("Hover a bar for the model card (cost scale, Intelligence, Coding, Speed)")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                    Spacer()
                    Link("Data from Artificial Analysis", destination: ModelsContent.attribution)
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
        }
    }

    /// One swatch per provider the shown models belong to.
    private var legend: some View {
        var seen: [String: ModelVendor] = [:]
        for row in model.rows where row.aa != nil {
            let vendor = ModelVendor.of(row.aa!)
            seen[vendor.id] = seen[vendor.id] ?? vendor
        }
        return FlowLayout(spacing: 14, lineSpacing: 6) {
            ForEach(Array(seen.values.enumerated()), id: \.element.id) { _, vendor in
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

    private var vendor: ModelVendor {
        row.aa.map { ModelVendor.of($0) } ?? ModelVendor.of(logged: row.logged)
    }

    /// Bar text reads dark on light fills (OpenAI's grey).
    private var valueColor: Color {
        vendor.color.luminance > 0.45 ? Theme.textPrimary : .white
    }

    var body: some View {
        VStack(spacing: 0) {
            // Value inside the bar's top, or a stub when AA has none.
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
            // The logo and the angled name.
            if let icon = vendor.icon, HarnessIcon.exists(for: icon) {
                HarnessIcon(harness: icon, size: 12)
                    .padding(.top, 6)
            } else {
                Color.clear.frame(width: 12, height: 12).padding(.top, 6)
            }
            Text(row.aa?.name ?? row.logged)
                .font(.system(size: 9.5))
                .foregroundStyle(row.used ? Theme.textPrimary : Theme.textSecondary)
                .lineLimit(1)
                .fixedSize()
                .rotationEffect(.degrees(-60), anchor: .topLeading)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding(.leading, 4)
                .padding(.top, 4)
        }
    }
}

// MARK: - Table

/// The same rows as a table: who ran it, the headline benchmarks, cost
/// rank and the user's tokens.
private struct TableCard: View {
    @Bindable var model: ModelsModel
    var link: (String) -> Void

    var body: some View {
        Card {
            VStack(spacing: 0) {
                header
                Divider().overlay(Theme.border)
                ForEach(model.rows) { row in
                    TableRow(row: row, model: model, link: link)
                    Divider().overlay(Theme.rowDivider)
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 0) {
            Text("Model").frame(maxWidth: .infinity, alignment: .leading)
            Text("Provider").frame(width: 90, alignment: .leading)
            Text("Intelligence").frame(width: 80, alignment: .trailing)
            Text("Coding").frame(width: 64, alignment: .trailing)
            Text("Speed").frame(width: 64, alignment: .trailing)
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
    var link: (String) -> Void

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
                Text(row.aa?.name ?? row.logged)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                if let qualifier = row.qualifier {
                    Badge(qualifier)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(vendor.name).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                .frame(width: 90, alignment: .leading)
            metric(row.aa?.intelligence).frame(width: 80, alignment: .trailing)
            metric(row.aa?.coding).frame(width: 64, alignment: .trailing)
            metric(row.aa?.speed, unit: "t/s").frame(width: 64, alignment: .trailing)
            CostScale(model: row.aa, rank: row.costPercentile)
                .frame(width: 120, alignment: .leading).padding(.leading, 16)
            Text(row.tokens > 0 ? UsageFormat.tokens(row.tokens) : "—")
                .font(Theme.bodySmall).monospacedDigit().foregroundStyle(Theme.textSecondary)
                .frame(width: 80, alignment: .trailing)
        }
        .padding(.vertical, 8)
        .contentShape(.rect)
        .modelCard(model, for: row.logged,
                   link: row.aa == nil ? { link(row.logged) } : nil)
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
/// as a white dot.
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
                            .overlay(Circle().strokeBorder(Theme.borderStrong, lineWidth: 1))
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
