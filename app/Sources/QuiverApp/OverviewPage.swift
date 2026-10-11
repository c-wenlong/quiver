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
                Card { Label(app.overviewError ?? "The usage ledger is unavailable.", systemImage: "exclamationmark.triangle") }
            }
        }
    }
}

private struct OverviewContent: View {
    @Bindable var model: OverviewModel

    var body: some View {
        Page(title: "Overview", subtitle: "Tokens and spend across every harness on this Mac.", maxWidth: 1280) {
            if let updated = model.updatedAt {
                Text("Updated \(updated, format: .relative(presentation: .named))")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(height: Theme.iconButton)
            }
            Button("Rescan", systemImage: "arrow.clockwise") { Task { await model.load(scan: true) } }
                .buttonStyle(IconButtonStyle())
                .disabled(model.isLoading)
                .help("Read new usage from the harness logs")
        } content: {
            if let error = model.error {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(Theme.bodySmall).foregroundStyle(Theme.warning)
            }
            if let summary = model.summary {
                let current = summary.period == model.period
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: Theme.gutter) {
                        side(summary).frame(width: 360)
                        main(summary)
                    }
                    .frame(minWidth: 940)
                    VStack(spacing: Theme.gutter) {
                        main(summary)
                        side(summary)
                    }
                }
                // Still the previous period's figures while the new ones load.
                .opacity(current ? 1 : 0.45)
                .animation(.easeOut(duration: 0.15), value: current)
            } else {
                Card { ProgressView("Reading your usage logs…").frame(maxWidth: .infinity) }
            }
        }
        .task(id: model.period) {
            if model.summary?.period != model.period { await model.reloadPeriod() }
        }
    }

    @ViewBuilder private func main(_ summary: OverviewSummary) -> some View {
        VStack(spacing: Theme.gutter) {
            TotalCard(summary: summary, period: $model.period)
            BreakdownCard(summary: summary)
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
            Tabs(options: OverviewPeriod.allCases.map { ($0, $0.title) }, selection: $period)
            VStack(spacing: 6) {
                Text("Total tokens").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
                Text(UsageFormat.tokens(summary.totals.totalTokens))
                    .font(Theme.display).tracking(-1.8).monospacedDigit()
                    .foregroundStyle(Theme.textPrimary)
                    .contentTransition(.numericText())
                HStack(spacing: 6) {
                    Text(UsageFormat.dollars(summary.totals.costUSD))
                        .font(.system(size: 28, weight: .semibold)).monospacedDigit()
                        .foregroundStyle(Theme.accent)
                    if summary.totals.unpricedTokens > 0 {
                        Image(systemName: "info.circle")
                            .foregroundStyle(Theme.textSecondary)
                            .help("Plus \(UsageFormat.tokens(summary.totals.unpricedTokens)) tokens from models with no list price: \(summary.totals.unpricedModels.sorted().joined(separator: ", "))")
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
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 12)], spacing: 12) {
                    ForEach(shares) { HarnessTile(share: $0) }
                }
            }
        }
    }

    var rangeText: String {
        let range = summary.range
        let start = range.start.formatted(.dateTime.month(.abbreviated).day())
        let end = range.end.formatted(.dateTime.month(.abbreviated).day())
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
                        .help("\(UsageFormat.harness(share.harness)): \(UsageFormat.tokens(share.totals.totalTokens)) tokens, \(UsageFormat.dollars(share.totals.costUSD))")
                }
            }
        }
        .frame(height: Theme.usageTrackHeight)
    }
}

/// A harness's share: the legend for the bar, with its numbers.
private struct HarnessTile: View {
    var share: HarnessShare

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                HarnessIcon(harness: share.harness, size: 16)
                Text(UsageFormat.harness(share.harness)).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                Spacer(minLength: 4)
                ChartSwatch(harness: share.harness)
            }
            Text(share.share > 0 && share.share < 0.001 ? "<0.1%"
                 : share.share.formatted(.percent.precision(.fractionLength(share.share < 0.1 ? 1 : 0))))
                .font(Theme.figure).monospacedDigit().foregroundStyle(Theme.textPrimary)
            HStack(spacing: 4) {
                Text(share.modelCount == 1 ? "1 model" : "\(share.modelCount) models")
                Text("·")
                Text(UsageFormat.dollars(share.totals.costUSD)).foregroundStyle(Theme.accent)
                if share.totals.unpricedTokens > 0 {
                    Image(systemName: "questionmark.circle")
                        .help("\(UsageFormat.tokens(share.totals.unpricedTokens)) tokens with no list price: \(share.totals.unpricedModels.sorted().joined(separator: ", "))")
                }
            }
            .font(Theme.caption).foregroundStyle(Theme.textSecondary).monospacedDigit()
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.borderStrong.opacity(0.7), lineWidth: 1))
    }
}

// MARK: - Breakdown

private struct BreakdownCard: View {
    enum Tab: Hashable { case daily, accounts }
    var summary: OverviewSummary
    @State private var tab: Tab = .daily

    var body: some View {
        Card {
            Tabs(options: [(Tab.daily, "Daily breakdown"), (Tab.accounts, "By account")], selection: $tab)
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
                    .font(Theme.tableHeader).foregroundStyle(Theme.textSecondary)
                    .padding(.bottom, 8)
                ForEach(summary.days) { row in
                    Divider().overlay(Theme.border)
                    DailyRow(cells: [
                        row.day.formatted(Date.ISO8601FormatStyle(timeZone: .current).year().month().day()),
                        UsageFormat.tokens(row.totals.totalTokens),
                        UsageFormat.tokens(row.totals.inputTokens),
                        UsageFormat.tokens(row.totals.outputTokens),
                        UsageFormat.tokens(row.totals.cacheReadTokens + row.totals.cacheWriteTokens),
                        UsageFormat.dollars(row.totals.costUSD),
                    ], moneyLast: true)
                    .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).monospacedDigit()
                    .padding(.vertical, 9)
                }
            }
        }
    }

    @ViewBuilder private var accounts: some View {
        let shown = summary.accounts.filter { $0.totals.totalTokens > 0 }
        if shown.isEmpty {
            Text("No usage in this period.").font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }
        VStack(spacing: 0) {
            ForEach(shown) { row in
                HStack {
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

/// The date takes the slack; the figures sit in equal right-aligned columns.
private struct DailyRow: View {
    var cells: [String]
    var moneyLast = false

    var body: some View {
        HStack(spacing: 12) {
            Text(cells[0]).frame(maxWidth: .infinity, alignment: .leading)
            ForEach(1..<cells.count, id: \.self) { index in
                Text(cells[index])
                    .foregroundStyle(moneyLast && index == cells.count - 1 ? AnyShapeStyle(Theme.accent) : AnyShapeStyle(.foreground))
                    .frame(width: 76, alignment: .trailing)
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

    var body: some View {
        let map = Heatmap(days: days, weeks: 26, now: Date(), calendar: .current)
        let weeks = map.columns.suffix(18)
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
                                    .help("\(cell.day.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())): \(UsageFormat.tokens(cell.tokens)) tokens")
                            } else {
                                Color.clear.frame(width: Theme.heatmapCell, height: Theme.heatmapCell)
                            }
                        }
                    }
                }
            }
            HStack(spacing: 4) {
                Text("Less").font(Theme.caption).foregroundStyle(Theme.textSecondary)
                ForEach(0..<5, id: \.self) { level in
                    RoundedRectangle(cornerRadius: 2).fill(Theme.heatmap[level]).frame(width: 10, height: 10)
                }
                Text("More").font(Theme.caption).foregroundStyle(Theme.textSecondary)
            }
        }
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
