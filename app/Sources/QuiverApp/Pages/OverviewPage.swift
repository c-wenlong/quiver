import QuiverCore
import SwiftUI

/// Tokens and spend across every harness on this Mac, from the ledger.
/// Laid out like TokenTracker's Tokens page: the period's total and split
/// on the right, quick figures, top models and the heatmap on the left.
struct OverviewPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        if let model = app.overview {
            OverviewContent(model: model)
        } else {
            Page(title: "Overview", subtitle: "Tokens and spend across every harness on this Mac.") {
                ErrorCard(message: app.overviewError ?? "The usage ledger is unavailable.")
            }
        }
    }
}

private struct OverviewContent: View {
    @Bindable var model: OverviewModel
    /// The content width, to pick between two columns and one.
    @State private var width: CGFloat = 0
    /// Kept here: crossing the column cutoff builds the cards again, which
    /// would put the breakdown back on Daily.
    @State private var breakdownTab: BreakdownCard.Tab = .daily

    /// A 300pt side column plus a main column of at least about 524pt, so
    /// the default window (about 868pt of content) gets two columns.
    static let twoColumnWidth: CGFloat = 840

    var body: some View {
        Page(title: "Overview", subtitle: "Tokens and spend across every harness on this Mac.", ) {
            if let updated = model.updatedAt {
                Text("Updated \(updated, format: .relative(presentation: .named))")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(height: Theme.iconButton)
            }
            Button("Rescan", systemImage: "arrow.clockwise") { Task { await model.load(scan: true) } }
                .iconButton()
                .disabled(model.isLoading)
                .keyboardShortcut("r", modifiers: .command)
                .help("Read new usage from the harness logs (⌘R)")
        } content: {
            if let error = model.error {
                ErrorCard(message: error)
            }
            if !model.scanNotes.isEmpty {
                Card {
                    ForEach(model.scanNotes, id: \.self) { note in
                        Label(note, systemImage: "info.circle")
                            .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
                    }
                }
            }
            if let progress = model.scanProgress, model.summary == nil || !model.fullScanFinished {
                ScanProgressCard(progress: progress, partial: model.summary != nil)
            }
            if let summary = model.summary {
                let current = summary.period == model.period
                Group {
                    if width >= Self.twoColumnWidth {
                        HStack(alignment: .top, spacing: Theme.gutter) {
                            side(summary).frame(width: 300)
                            main(summary)
                        }
                    } else {
                        VStack(spacing: Theme.gutter) {
                            main(summary)
                            side(summary)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
                // Still the previous period's figures while the new ones load.
                .opacity(current ? 1 : 0.45)
                .animation(.easeOut(duration: Theme.fadeDuration), value: current)
            } else if model.scanProgress == nil {
                LoadingCard(text: "Reading your usage logs…")
            }
        }
        .task(id: model.period) {
            if model.summary?.period != model.period { await model.reloadPeriod() }
        }
    }

    @ViewBuilder private func main(_ summary: OverviewSummary) -> some View {
        VStack(spacing: Theme.gutter) {
            TotalCard(summary: summary, period: $model.period)
            BreakdownCard(summary: summary, tab: $breakdownTab)
        }
    }

    @ViewBuilder private func side(_ summary: OverviewSummary) -> some View {
        VStack(spacing: Theme.gutter) {
            QuickFigures(summary: summary)
            ActivityCard(days: model.activity)
        }
    }
}

// MARK: - Scan progress

/// The first scan's files done out of total. Above the figures once there
/// are some, which until it finishes cover only the files read so far.
private struct ScanProgressCard: View {
    var progress: ScanProgress
    var partial: Bool

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Reading your usage logs")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Text("\(progress.filesDone.formatted()) of \(progress.filesTotal.formatted()) files")
                        .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                        .monospacedDigit()
                }
                ProgressView(value: Double(min(progress.filesDone, progress.filesTotal)),
                             total: Double(max(progress.filesTotal, 1)))
                    .progressViewStyle(.linear)
                if partial {
                    Text("Figures below cover the files read so far.")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
        }
    }
}

// MARK: - Total

/// The period's totals over a pile of marbles, one per round amount of
/// spend (`MarbleScale`), in each harness's colour and logo. Hovering a
/// logo in the row under the figures shows only that harness's marbles and
/// rolls the figures over to its own.
private struct TotalCard: View {
    var summary: OverviewSummary
    @Binding var period: OverviewPeriod

    @Environment(AppModel.self) private var app
    /// The harness whose logo is under the pointer.
    @State private var focus: String?
    @State private var size: CGSize = .zero
    /// The figures' frame in the card, for the marbles to fade behind.
    @State private var figuresFrame: CGRect = .zero

    static let space = "total-card"
    /// Tall enough that the pile has room under the figures.
    static let minHeight: CGFloat = 440

    /// In the order and with the providers chosen in Settings.
    var shares: [HarnessShare] {
        let shown = summary.harnesses.filter { $0.totals.totalTokens > 0 && !app.providers.isHidden($0.harness) }
        let order = app.providers.ordered(shown.map(\.harness))
        return order.compactMap { id in shown.first { $0.harness == id } }
    }

    /// Marbles for about half the card. Rounded to tens so a window
    /// resize doesn't add and drop a marble at every step, and capped so a
    /// very large window doesn't mean hundreds of physics bodies.
    var scale: MarbleScale {
        let r = MarbleScene.radius
        let fit = 0.5 * size.width * size.height / (.pi * r * r / 0.82)
        let capacity = min(max(Int(fit / 10) * 10, 20), 220)
        return MarbleScale(shares: shares, capacity: capacity)
    }

    var body: some View {
        VStack(spacing: 14) {
            // Seven periods no longer fit a narrow card: the row scrolls.
            ScrollView(.horizontal, showsIndicators: false) {
                Tabs(options: OverviewPeriod.allCases.map { ($0, $0.title) }, selection: $period, style: .period)
            }
            figures
                .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(Self.space)) } action: { figuresFrame = $0 }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, minHeight: Self.minHeight, alignment: .top)
        .coordinateSpace(.named(Self.space))
        .onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }
        .background {
            if !shares.isEmpty, size != .zero {
                MarblePile(scale: scale, focus: focus, clearRect: figuresFrame)
            }
        }
        .clipShape(.rect(cornerRadius: Theme.cardRadius))
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
        .onChange(of: summary.period) { focus = nil }
    }

    private var figures: some View {
        let focused = shares.first { $0.harness == focus }
        let totals = focused?.totals ?? summary.totals
        return VStack(spacing: 6) {
            Text(focused.map { "\(UsageFormat.harness($0.harness)) tokens" } ?? "Total tokens")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                .contentTransition(.numericText())
            Text(UsageFormat.tokens(totals.totalTokens))
                .font(Theme.display).tracking(-1.8).monospacedDigit()
                .foregroundStyle(Theme.textPrimary)
                .contentTransition(.numericText(countsDown: focused != nil))
            HStack(spacing: 6) {
                Text(UsageFormat.dollars(totals.costUSD))
                    .font(Theme.figure).monospacedDigit()
                    .foregroundStyle(Theme.accent)
                    .contentTransition(.numericText(countsDown: focused != nil))
                if totals.unpricedTokens > 0 {
                    Button { app.selection = .pricing } label: {
                        Image(systemName: "info.circle")
                            .foregroundStyle(Theme.textSecondary)
                            .hoverTip("\(UsageFormat.tokens(totals.unpricedTokens)) tokens from models with no list price aren't in this total: \(totals.unpricedModels.sorted().joined(separator: ", "))")
                    }
                    .buttonStyle(.plain)
                    .help("See unpriced models on the Pricing page")
                }
            }
            Text(focused.map(detail) ?? summaryLine)
                .font(Theme.caption).foregroundStyle(Theme.textMuted).monospacedDigit()
                .contentTransition(.numericText())
            if shares.isEmpty {
                Text(summary.totals.totalTokens > 0
                     ? "Every provider with usage this period is hidden in Settings."
                     : "No usage in this period.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                    .padding(.top, 6)
            } else {
                logos.padding(.top, 6)
            }
        }
        .padding(.vertical, 12)
        .animation(.snappy(duration: 0.35), value: focus)
    }

    /// One logo per harness: the pointer on one focuses it, and so does a
    /// click or Space for the keyboard. One centred row while they fit,
    /// wrapping once there are too many for the card.
    private var logos: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 6) { logoButtons }
            FlowLayout(spacing: 6, lineSpacing: 6) { logoButtons }
        }
    }

    private var logoButtons: some View {
        ForEach(shares) { share in
            let on = focus == share.harness
            Button { focus = on ? nil : share.harness } label: {
                HarnessIcon(harness: share.harness, size: 16)
                    .padding(7)
                    .background(Theme.card.opacity(0.85), in: .circle)
                    .overlay(Circle().strokeBorder(on ? Theme.harness(share.harness) : Theme.borderStrong, lineWidth: 1))
                    .contentShape(.circle)
            }
            .buttonStyle(.plain)
            .opacity(focus == nil || on ? 1 : 0.45)
            .onHover { inside in
                if inside { focus = share.harness } else if on { focus = nil }
            }
            .accessibilityLabel(UsageFormat.harness(share.harness))
            .accessibilityValue("\(UsageFormat.tokens(share.totals.totalTokens)) tokens, \(UsageFormat.dollars(share.totals.costUSD)), \(detail(share))")
            .accessibilityAddTraits(on ? .isSelected : [])
        }
    }

    /// A focused harness's share and models.
    private func detail(_ share: HarnessShare) -> String {
        let total = summary.totals.costUSD
        let part = total > 0 ? share.totals.costUSD / total : share.share
        let percent = part > 0 && part < 0.001 ? "<0.1%"
            : part.formatted(.percent.precision(.fractionLength(part < 0.1 ? 1 : 0)))
        let models = share.modelCount == 1 ? "1 model" : "\(share.modelCount) models"
        return "\(percent) of \(total > 0 ? "spend" : "tokens") · \(models)"
    }

    private var summaryLine: String {
        shares.isEmpty ? rangeText : "\(rangeText) · 1 marble = \(Self.unitText(scale.unit))"
    }

    static func unitText(_ unit: MarbleScale.Unit) -> String {
        switch unit {
        case .dollars(let amount):
            amount >= 1 ? "$" + Int(amount.rounded()).formatted() : String(format: "$%.2f", amount)
        case .tokens(let count):
            "\(UsageFormat.tokens(count)) tokens"
        }
    }

    var rangeText: String {
        if summary.period == .allTime {
            guard let first = summary.days.last(where: { $0.totals.totalTokens > 0 }) else {
                return "No usage yet"
            }
            return "Since \(first.day.formatted(.dateTime.month(.abbreviated).day().year()))"
        }
        let range = summary.range
        let crossYear = Calendar.current.component(.year, from: range.start)
            != Calendar.current.component(.year, from: range.end)
        let start = range.start.formatted(crossYear
            ? .dateTime.month(.abbreviated).day().year()
            : .dateTime.month(.abbreviated).day())
        let end = range.end.formatted(crossYear
            ? .dateTime.month(.abbreviated).day().year()
            : .dateTime.month(.abbreviated).day())
        return start == end ? start : "\(start) to \(end)"
    }
}

// MARK: - Breakdown

private struct BreakdownCard: View {
    enum Tab: Hashable { case daily, accounts }
    var summary: OverviewSummary
    @Binding var tab: Tab

    var body: some View {
        Card {
            Tabs(options: [(Tab.daily, "Daily breakdown"), (Tab.accounts, "By account")], selection: $tab, style: .table)
            switch tab {
            case .daily: daily
            case .accounts: accounts
            }
        }
    }

    @ViewBuilder private var daily: some View {
        if summary.days.isEmpty {
            Text("No usage in this period.").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        } else {
            VStack(spacing: 0) {
                DailyRow(cells: ["Date", "Total", "Input", "Output", "Cached", "Spend"])
                    .font(Theme.tableHeader).foregroundStyle(Theme.tableHeaderText)
                    .padding(.horizontal, 8)
                    .padding(.bottom, 8)
                ForEach(summary.days) { row in
                    Rectangle().fill(Theme.rowDivider).frame(height: Theme.hairline)
                    DailyRow(cells: [
                        row.day.formatted(Date.ISO8601FormatStyle(timeZone: .current).year().month().day()),
                        UsageFormat.tokens(row.totals.totalTokens),
                        UsageFormat.tokens(row.totals.inputTokens),
                        UsageFormat.tokens(row.totals.outputTokens),
                        UsageFormat.tokens(row.totals.cacheReadTokens + row.totals.cacheWriteTokens),
                        UsageFormat.dollars(row.totals.costUSD),
                    ], moneyLast: true)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).monospacedDigit()
                    .padding(.horizontal, 8)
                    .padding(.vertical, 9)
                    .modifier(RowHover())
                }
            }
        }
    }

    @ViewBuilder private var accounts: some View {
        let shown = summary.accounts.filter { $0.totals.totalTokens > 0 }
        let withLogos = shown.contains { $0.harness.map(HarnessIcon.exists) ?? false }
        if shown.isEmpty {
            Text("No usage in this period.").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }
        VStack(spacing: 0) {
            ForEach(shown) { row in
                HStack {
                    // An account with no logo keeps the square, so the names line up.
                    if withLogos {
                        HarnessIcon(harness: row.harness ?? "", size: 16)
                            .help(row.harness.map { HarnessMark($0).name } ?? row.label)
                    }
                    Text(row.label).lineLimit(1).truncationMode(.middle).help(row.home)
                    Spacer()
                    Text(UsageFormat.tokens(row.totals.totalTokens)).foregroundStyle(Theme.textSecondary)
                    Text(UsageFormat.dollars(row.totals.costUSD)).foregroundStyle(Theme.accent)
                        .frame(minWidth: 72, alignment: .trailing)
                }
                .font(Theme.bodySmall).monospacedDigit()
                .padding(.vertical, 10)
                if row != shown.last { Divider().overlay(Theme.border) }
            }
        }
    }
}

/// `rowHover` under the pointer.
private struct RowHover: ViewModifier {
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .background(hovering ? Theme.rowHover : .clear)
            .onHover { hovering = $0 }
    }
}

/// The date takes the slack; the figures sit in equal right-aligned columns.
private struct DailyRow: View {
    var cells: [String]
    var moneyLast = false

    var body: some View {
        HStack(spacing: 10) {
            Text(cells[0]).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
            ForEach(1..<cells.count, id: \.self) { index in
                Text(cells[index])
                    .foregroundStyle(moneyLast && index == cells.count - 1 ? AnyShapeStyle(Theme.accent) : AnyShapeStyle(.foreground))
                    .lineLimit(1)
                    .frame(width: 68, alignment: .trailing)
            }
        }
    }
}

// MARK: - Side column

private struct QuickFigures: View {
    var summary: OverviewSummary
    @Environment(AppModel.self) private var app

    private func modelName(_ name: String) -> some View {
        Text(name).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
            .lineLimit(1).truncationMode(.middle)
    }

    @ViewBuilder private func harnessLogos(_ harnesses: [String]) -> some View {
        if !harnesses.isEmpty {
            HStack(spacing: 2) {
                ForEach(Array(harnesses.prefix(3)), id: \.self) { harness in
                    HarnessIcon(harness: harness, size: 12)
                        .accessibilityLabel(UsageFormat.harness(harness))
                }
                if harnesses.count > 3 {
                    Text("+\(harnesses.count - 3)")
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
            }
            .fixedSize()
        }
    }

    var busiest: DayRow? { summary.days.max { $0.totals.totalTokens < $1.totals.totalTokens } }

    var body: some View {
        Card {
            HStack(spacing: 8) {
                Chip(value: "\(summary.activeDays)", label: summary.activeDays == 1 ? "active day" : "active days")
                Chip(value: UsageFormat.tokens(summary.totals.totalTokens / max(summary.activeDays, 1)), label: "a day")
                Chip(value: busiest.map { $0.day.formatted(.dateTime.month(.abbreviated).day()) } ?? "None",
                     label: "busiest")
            }
            if !summary.models.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(summary.models.prefix(5).enumerated()), id: \.element.id) { index, model in
                        HStack(spacing: 10) {
                            Text("\(index + 1)")
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                .frame(width: 20, height: 20)
                                .background(Theme.subtle, in: .circle)
                            BrandLogo(model: model.model, size: 16)
                            // The name comes first: the tools that ran it
                            // follow only while the whole name still fits.
                            ViewThatFits(in: .horizontal) {
                                HStack(spacing: 8) {
                                    modelName(model.model)
                                    harnessLogos(model.harnesses)
                                }
                                modelName(model.model)
                            }
                            Spacer(minLength: 8)
                            Text(model.share, format: .percent.precision(.fractionLength(1)))
                                .font(Theme.rowTitle).monospacedDigit().foregroundStyle(Theme.textPrimary)
                        }
                        .padding(.vertical, 9)
                        if index < min(summary.models.count, 5) - 1 { Divider().overlay(Theme.border) }
                    }
                }
            }
        }
    }
}

private struct Chip: View {
    var value: String
    var label: String

    var body: some View {
        VStack(spacing: 2) {
            Text(value).font(Theme.rowTitle).monospacedDigit().foregroundStyle(Theme.textPrimary).lineLimit(1)
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary).lineLimit(1)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Theme.subtle, in: .rect(cornerRadius: Theme.blockRadius))
    }
}

private struct ActivityCard: View {
    var days: [DayRow]
    @State private var width: CGFloat = 0
    /// The day under the pointer; kept when it sits in a gap between cells.
    /// The bubble reads the live cell for it, so a rescan updates the figures.
    @State private var hovered: Date?
    /// The pointer is inside the grid at all: the bubble survives the gaps
    /// but goes when the grid is left.
    @State private var inGrid = false
    /// Every drawn cell's frame in the grid's own coordinate space.
    @State private var frames: [Date: CGRect] = [:]
    @State private var gridSize: CGSize = .zero
    @State private var bubbleSize: CGSize = .zero

    /// As many 12pt weeks (3pt apart, after the 24pt day labels) as fit.
    var weekCount: Int { min(26, max(8, Int((width - 24) / (Theme.heatmapCell + 3)))) }

    var body: some View {
        let map = Heatmap(days: days, weeks: 26, now: Date(), calendar: .current)
        let weeks = map.columns.suffix(weekCount)
        Card(title: "Activity", subtitle: "Tokens a day, last \(weeks.count) weeks") {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 3) {
                    Color.clear.frame(width: 24, height: 1)
                    ForEach(Array(weeks.enumerated()), id: \.offset) { index, column in
                        Text(Self.monthLabel(Array(weeks), index) ?? "")
                            .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                            .fixedSize()
                            .frame(width: Theme.heatmapCell, alignment: .leading)
                    }
                }
                // Measures the card's inner width without taking any height.
                Color.clear.frame(maxWidth: .infinity, maxHeight: 0)
                    .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
                ForEach(0..<7, id: \.self) { weekday in
                    HStack(spacing: 3) {
                        Text(weekday % 2 == 1 ? Self.weekdayName(weekday) : "")
                            .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                            .frame(width: 24, alignment: .leading)
                        ForEach(Array(weeks.enumerated()), id: \.offset) { _, column in
                            if let cell = column[weekday] {
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(Theme.heatmap[cell.level])
                                    .frame(width: Theme.heatmapCell, height: Theme.heatmapCell)
                                    // A new day at this slot (the week window
                                    // slides when the column count settles)
                                    // must report its frame again: geometry
                                    // alone would not have changed.
                                    .id(cell.day)
                                    .onGeometryChange(for: CGRect.self) {
                                        $0.frame(in: .named("activityGrid"))
                                    } action: { frames[cell.day] = $0 }
                                    .onHover { inside in if inside { hovered = cell.day } }
                            } else {
                                Color.clear.frame(width: Theme.heatmapCell, height: Theme.heatmapCell)
                            }
                        }
                    }
                }
            }
            .coordinateSpace(name: "activityGrid")
            .onGeometryChange(for: CGSize.self) { $0.size } action: { gridSize = $0 }
            .onHover { inGrid = $0 }
            // One bubble for the whole grid, drawn above it so no cell clips
            // it and nothing flickers between cells.
            .overlay {
                if inGrid, let hovered,
                   let resolved = HeatmapHover.resolve(
                    day: hovered, cells: weeks.joined().compactMap { $0 },
                    frames: frames, gridSize: gridSize, bubbleSize: bubbleSize) {
                    bubble(for: resolved.cell, at: resolved.origin).transition(.opacity)
                }
            }
            .animation(.easeIn(duration: 0.1), value: inGrid)
            HStack(spacing: 4) {
                Text("Less").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                ForEach(0..<5, id: \.self) { level in
                    RoundedRectangle(cornerRadius: 2).fill(Theme.heatmap[level]).frame(width: 10, height: 10)
                }
                Text("More").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
    }

    /// The day's stats in a small card that floats over the grid, centred
    /// on the cell and clamped inside the card; it drops under the cell
    /// when the first row leaves no room above.
    private func bubble(for cell: Heatmap.Cell, at origin: CGPoint) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(cell.day.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day()))
                .foregroundStyle(Theme.textPrimary)
            Text(detail(for: cell)).foregroundStyle(Theme.textSecondary)
        }
        .font(Theme.caption)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
        .shadow(color: .black.opacity(0.12), radius: 4, y: 2)
        .fixedSize()
        .onGeometryChange(for: CGSize.self) { $0.size } action: { bubbleSize = $0 }
        .position(origin)
        .allowsHitTesting(false)
    }

    private func detail(for cell: Heatmap.Cell) -> String {
        if cell.tokens == 0 { return "No usage" }
        let tokens = "\(UsageFormat.tokens(cell.tokens)) tokens"
        if cell.costUSD > 0 {
            return "\(tokens) · \(UsageFormat.dollars(cell.costUSD))"
                + (cell.unpricedTokens > 0 ? " (some tokens unpriced)" : "")
        }
        return cell.unpricedTokens > 0 ? "\(tokens) · No list price" : "\(tokens) · $0.00"
    }

    static func weekdayName(_ index: Int) -> String {
        let calendar = Calendar.current
        let symbols = calendar.shortWeekdaySymbols
        return symbols[(index + calendar.firstWeekday - 1) % 7]
    }

    /// The month's short name over the first week that starts in it.
    static func monthLabel(_ columns: [[Heatmap.Cell?]], _ week: Int) -> String? {
        guard let day = columns[week].first??.day else { return nil }
        let calendar = Calendar.current
        if week > 0, let previous = columns[week - 1].first??.day,
           calendar.component(.month, from: previous) == calendar.component(.month, from: day) { return nil }
        // Skip a label squeezed against the next one.
        if week + 1 < columns.count, let next = columns[week + 1].first??.day,
           calendar.component(.month, from: next) != calendar.component(.month, from: day) { return nil }
        return day.formatted(.dateTime.month(.abbreviated))
    }
}
