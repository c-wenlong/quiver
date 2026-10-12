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
        Page(title: "Overview", subtitle: "Tokens and spend across every harness on this Mac.", maxWidth: 1280) {
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
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
            }
            ForEach(model.scanNotes, id: \.self) { note in
                Label(note, systemImage: "info.circle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warningText)
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
                .animation(.easeOut(duration: 0.15), value: current)
            } else {
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

// MARK: - Total

private struct TotalCard: View {
    var summary: OverviewSummary
    @Binding var period: OverviewPeriod

    @Environment(AppModel.self) private var app

    /// In the order and with the providers chosen in Settings.
    var shares: [HarnessShare] {
        let shown = summary.harnesses.filter { $0.totals.totalTokens > 0 && !app.providers.isHidden($0.harness) }
        let order = app.providers.ordered(shown.map(\.harness))
        return order.compactMap { id in shown.first { $0.harness == id } }
    }

    var body: some View {
        Card {
            // Seven periods no longer fit a narrow card: the row scrolls.
            ScrollView(.horizontal, showsIndicators: false) {
                Tabs(options: OverviewPeriod.allCases.map { ($0, $0.title) }, selection: $period, style: .period)
            }
            VStack(spacing: 6) {
                Text("Total tokens").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                Text(UsageFormat.tokens(summary.totals.totalTokens))
                    .font(Theme.display).tracking(-1.8).monospacedDigit()
                    .foregroundStyle(Theme.textPrimary)
                    .contentTransition(.numericText())
                HStack(spacing: 6) {
                    Text(UsageFormat.dollars(summary.totals.costUSD))
                        .font(Theme.figure).monospacedDigit()
                        .foregroundStyle(Theme.accent)
                    if summary.totals.unpricedTokens > 0 {
                        Image(systemName: "info.circle")
                            .foregroundStyle(Theme.textSecondary)
                            .hoverTip("\(UsageFormat.tokens(summary.totals.unpricedTokens)) tokens from models with no list price aren't in this total: \(summary.totals.unpricedModels.sorted().joined(separator: ", "))")
                    }
                }
                Text(rangeText).font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            if shares.isEmpty {
                Text(summary.totals.totalTokens > 0
                     ? "Every provider with usage this period is hidden in Settings."
                     : "No usage in this period.")
                    .font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            } else {
                ShareBar(shares: shares)
                HarnessTiles(shares: shares)
            }
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

/// One thin bar split by harness, 2pt surface gaps between segments.
private struct ShareBar: View {
    var shares: [HarnessShare]

    var body: some View {
        GeometryReader { geo in
            let gaps = CGFloat(shares.count - 1) * 2
            HStack(spacing: 2) {
                ForEach(shares) { share in
                    Capsule()
                        .fill(Theme.harness(share.harness))
                        .frame(width: max(3, (geo.size.width - gaps) * share.share))
                        .hoverTip("\(UsageFormat.harness(share.harness)): \(UsageFormat.tokens(share.totals.totalTokens)) tokens, \(UsageFormat.dollars(share.totals.costUSD))")
                }
            }
        }
        .frame(height: Theme.usageTrackHeight)
    }
}

/// Up to four equal columns, one per harness, every tile the same height.
/// Four that would each get under 140pt (the two-column layout at the
/// default window) go two by two, so no name or figure truncates.
private struct HarnessTiles: View {
    var shares: [HarnessShare]
    @State private var width: CGFloat = 0

    static let minTileWidth: CGFloat = 140

    var columns: Int {
        var count = min(shares.count, 4)
        while count > 1, width > 0, (width - CGFloat(count - 1) * 12) / CGFloat(count) < Self.minTileWidth {
            count = count == 4 ? 2 : count - 1
        }
        return max(count, 1)
    }

    var body: some View {
        let columns = columns
        let rows = stride(from: 0, to: shares.count, by: columns).map { Array(shares[$0..<min($0 + columns, shares.count)]) }
        Grid(horizontalSpacing: 12, verticalSpacing: 12) {
            ForEach(rows, id: \.first?.id) { row in
                GridRow {
                    ForEach(row) { HarnessTile(share: $0) }
                    // Keeps a short last row on the same column widths.
                    ForEach(row.count..<columns, id: \.self) { _ in Color.clear.gridCellUnsizedAxes([.horizontal, .vertical]) }
                }
            }
        }
        // Rows as tall as their tallest tile, and no taller.
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }
}

/// A harness's share: the legend for the bar, with its numbers.
private struct HarnessTile: View {
    var share: HarnessShare

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                HarnessIcon(harness: share.harness, size: 16)
                Text(UsageFormat.harness(share.harness)).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary)
                    .lineLimit(1).truncationMode(.tail)
            }
            .hoverTip(UsageFormat.harness(share.harness))
            Text(share.share > 0 && share.share < 0.001 ? "<0.1%"
                 : share.share.formatted(.percent.precision(.fractionLength(share.share < 0.1 ? 1 : 0))))
                .font(Theme.figure).monospacedDigit().foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
            HStack(spacing: 4) {
                Text(share.modelCount == 1 ? "1 model" : "\(share.modelCount) models")
                Text("·")
                Text(UsageFormat.dollars(share.totals.costUSD)).foregroundStyle(Theme.accent)
                if share.totals.unpricedTokens > 0 {
                    Image(systemName: "questionmark.circle")
                        .hoverTip("\(UsageFormat.tokens(share.totals.unpricedTokens)) of these tokens have no list price, so they aren't costed: \(share.totals.unpricedModels.sorted().joined(separator: ", "))")
                }
            }
            .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
            .lineLimit(1)
        }
        .padding(12)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.borderStrong, lineWidth: 1))
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
                    Rectangle().fill(Theme.rowDivider).frame(height: 1)
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
                            if !model.harnesses.isEmpty {
                                HStack(spacing: 2) {
                                    ForEach(Array(model.harnesses.prefix(3)), id: \.self) { harness in
                                        HarnessIcon(harness: harness, size: 14)
                                            .accessibilityLabel(UsageFormat.harness(harness))
                                    }
                                    if model.harnesses.count > 3 {
                                        Text("+\(model.harnesses.count - 3)")
                                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                                    }
                                }
                                .fixedSize()
                            }
                            Text(model.model).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                                .lineLimit(1).truncationMode(.middle)
                            Spacer()
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
        .background(Theme.subtle, in: .rect(cornerRadius: 12))
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
