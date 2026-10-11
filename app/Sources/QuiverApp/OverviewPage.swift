import Charts
import QuiverCore
import SwiftUI

/// Tokens and spend across every harness on this Mac, from the ledger.
struct OverviewPage: View {
    @Environment(AppModel.self) private var app

    var body: some View {
        Page(title: "Overview", subtitle: "Tokens and spend across every harness on this Mac.") {
            if let model = app.overview {
                OverviewContent(model: model)
            } else {
                Card { Label(app.overviewError ?? "The usage ledger is unavailable.", systemImage: "exclamationmark.triangle") }
            }
        }
    }
}

private struct OverviewContent: View {
    @Bindable var model: OverviewModel

    var body: some View {
        VStack(alignment: .leading, spacing: Theme.gutter) {
            HStack(spacing: 12) {
                Picker("Period", selection: $model.period) {
                    ForEach(OverviewPeriod.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(maxWidth: 380)
                Spacer()
                if let updated = model.updatedAt {
                    Text("Updated \(updated, format: .relative(presentation: .named))")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Button("Rescan", systemImage: "arrow.clockwise") {
                    Task { await model.load(scan: true) }
                }
                .disabled(model.isLoading)
            }
            if let error = model.error {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(Theme.warning)
            }
            if let summary = model.summary {
                Group {
                    Totals(summary: summary)
                    HarnessCard(summary: summary)
                    HStack(alignment: .top, spacing: Theme.gutter) {
                        DailyCard(summary: summary, period: summary.period)
                        AccountsCard(accounts: summary.accounts)
                            .frame(maxWidth: 360)
                    }
                }
                // Still the previous period's figures while the new ones load.
                .opacity(summary.period == model.period ? 1 : 0.4)
                .animation(.easeOut(duration: 0.15), value: summary.period == model.period)
                ActivityCard(days: model.activity)
            } else {
                Card { ProgressView("Reading your usage logs…").frame(maxWidth: .infinity) }
            }
        }
        .task(id: model.period) {
            if model.summary?.period != model.period { await model.reloadPeriod() }
        }
    }
}

// MARK: - Headline numbers

private struct Totals: View {
    var summary: OverviewSummary

    var busiest: DayRow? { summary.days.max { $0.totals.totalTokens < $1.totals.totalTokens } }

    var body: some View {
        HStack(spacing: Theme.gutter) {
            StatTile(title: "Tokens", value: UsageFormat.tokens(summary.totals.totalTokens),
                     detail: "\(UsageFormat.tokens(summary.totals.inputTokens + summary.totals.cacheReadTokens + summary.totals.cacheWriteTokens)) in · \(UsageFormat.tokens(summary.totals.outputTokens)) out")
            StatTile(title: "Spend", value: UsageFormat.dollars(summary.totals.costUSD),
                     detail: summary.totals.unpricedTokens > 0
                        ? "+ \(UsageFormat.tokens(summary.totals.unpricedTokens)) tokens with no list price"
                        : "At API list prices")
            StatTile(title: "Busiest day",
                     value: busiest.map { $0.day.formatted(.dateTime.month(.abbreviated).day()) } ?? "None",
                     detail: busiest.map { "\(UsageFormat.tokens($0.totals.totalTokens)) tokens · \(UsageFormat.dollars($0.totals.costUSD))" } ?? "No usage in this period")
        }
    }
}

private struct StatTile: View {
    var title: String
    var value: String
    var detail: String

    var body: some View {
        Card(title: title) {
            Text(value)
                .font(.system(size: 34, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .contentTransition(.numericText())
            Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
    }
}

// MARK: - Harness split

private struct HarnessCard: View {
    var summary: OverviewSummary

    var shares: [HarnessShare] { summary.harnesses.filter { $0.totals.totalTokens > 0 } }

    var body: some View {
        Card(title: "By harness") {
            if shares.isEmpty {
                Text("No usage in this period.").foregroundStyle(.secondary)
            } else {
                ShareBar(shares: shares)
                Grid(alignment: .leading, horizontalSpacing: 18, verticalSpacing: 8) {
                    GridRow {
                        Text("Harness"); Text("Share"); Text("Tokens"); Text("Spend")
                    }
                    .font(.caption).foregroundStyle(.secondary)
                    ForEach(shares) { share in
                        GridRow {
                            HStack(spacing: 8) {
                                RoundedRectangle(cornerRadius: 3).fill(Theme.harness(share.harness)).frame(width: 10, height: 10)
                                Text(UsageFormat.harness(share.harness))
                            }
                            Text(share.share, format: .percent.precision(.fractionLength(share.share < 0.1 ? 1 : 0)))
                            Text(UsageFormat.tokens(share.totals.totalTokens))
                            HStack(spacing: 4) {
                                Text(UsageFormat.dollars(share.totals.costUSD))
                                if share.totals.unpricedTokens > 0 {
                                    Image(systemName: "questionmark.circle")
                                        .foregroundStyle(.secondary)
                                        .help("\(UsageFormat.tokens(share.totals.unpricedTokens)) tokens from models with no list price: \(share.totals.unpricedModels.sorted().joined(separator: ", "))")
                                }
                            }
                        }
                        .monospacedDigit()
                    }
                }
            }
        }
    }
}

/// One bar split by harness, with a 2pt gap between segments.
private struct ShareBar: View {
    var shares: [HarnessShare]

    var body: some View {
        GeometryReader { geo in
            let gaps = CGFloat(shares.count - 1) * 2
            HStack(spacing: 2) {
                ForEach(shares) { share in
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Theme.harness(share.harness))
                        .frame(width: max(3, (geo.size.width - gaps) * share.share))
                        .help("\(UsageFormat.harness(share.harness)): \(UsageFormat.tokens(share.totals.totalTokens)) tokens, \(UsageFormat.dollars(share.totals.costUSD))")
                }
            }
        }
        .frame(height: 14)
    }
}

// MARK: - Daily

private struct DailyCard: View {
    var summary: OverviewSummary
    var period: OverviewPeriod
    @State private var hovered: Date?

    var hoveredRow: DayRow? { summary.days.first { $0.day == hovered } }

    var body: some View {
        Card(title: "Daily") {
            if summary.days.isEmpty {
                Text("No usage in this period.").foregroundStyle(.secondary)
            } else {
                if period != .today {
                    Chart(summary.days) { row in
                        BarMark(x: .value("Day", row.day, unit: .day),
                                y: .value("Tokens", row.totals.totalTokens))
                            .foregroundStyle(Theme.accent.opacity(hovered == nil || hovered == row.day ? 1 : 0.45))
                            .clipShape(UnevenRoundedRectangle(topLeadingRadius: 4, topTrailingRadius: 4))
                    }
                    .chartYAxis {
                        AxisMarks(position: .leading) { value in
                            AxisGridLine().foregroundStyle(.separator)
                            AxisValueLabel { if let n = value.as(Int.self) { Text(UsageFormat.tokens(n)) } }
                        }
                    }
                    .chartXAxis { AxisMarks(values: .stride(by: .day, count: period == .week ? 1 : 7)) { _ in
                        AxisValueLabel(format: .dateTime.month(.abbreviated).day())
                    } }
                    .chartXSelection(value: $hovered)
                    .overlay(alignment: .topTrailing) {
                        if let row = hoveredRow {
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(row.day, format: .dateTime.weekday(.abbreviated).month(.abbreviated).day())
                                    .font(.caption.weight(.medium))
                                Text("\(UsageFormat.tokens(row.totals.totalTokens)) tokens · \(UsageFormat.dollars(row.totals.costUSD))")
                                    .font(.caption).monospacedDigit()
                            }
                            .padding(8)
                            .background(.regularMaterial, in: .rect(cornerRadius: 8))
                        }
                    }
                    .frame(height: 160)
                }
                Grid(alignment: .trailing, horizontalSpacing: 18, verticalSpacing: 6) {
                    GridRow {
                        Text("Day").gridColumnAlignment(.leading); Text("Tokens"); Text("Spend")
                    }
                    .font(.caption).foregroundStyle(.secondary)
                    ForEach(summary.days.prefix(14)) { row in
                        GridRow {
                            Text(row.day, format: .dateTime.weekday(.abbreviated).month(.abbreviated).day())
                            Text(UsageFormat.tokens(row.totals.totalTokens))
                            Text(UsageFormat.dollars(row.totals.costUSD))
                        }
                        .monospacedDigit()
                    }
                }
                if summary.days.count > 14 {
                    Text("Showing the latest 14 of \(summary.days.count) days.").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}

// MARK: - Accounts

private struct AccountsCard: View {
    var accounts: [AccountRow]

    var shown: [AccountRow] { accounts.filter { $0.totals.totalTokens > 0 } }

    var body: some View {
        Card(title: "By account") {
            if shown.isEmpty {
                Text("No usage in this period.").foregroundStyle(.secondary)
            }
            ForEach(shown) { row in
                HStack {
                    Text(row.label).lineLimit(1).truncationMode(.middle).help(row.home)
                    Spacer()
                    Text(UsageFormat.tokens(row.totals.totalTokens)).foregroundStyle(.secondary)
                    Text(UsageFormat.dollars(row.totals.costUSD)).frame(minWidth: 64, alignment: .trailing)
                }
                .monospacedDigit()
            }
        }
    }
}

// MARK: - Activity

private struct ActivityCard: View {
    var days: [DayRow]

    var body: some View {
        let map = Heatmap(days: days, weeks: 26, now: Date(), calendar: .current)
        Card(title: "Last 26 weeks") {
            HStack(alignment: .bottom, spacing: 3) {
                ForEach(map.columns.indices, id: \.self) { week in
                    Text(Self.monthLabel(map.columns, week) ?? "")
                        .font(.caption2).foregroundStyle(.secondary)
                        .fixedSize()
                        .frame(width: 13, alignment: .leading)
                }
            }
            HStack(alignment: .top, spacing: 3) {
                ForEach(map.columns.indices, id: \.self) { week in
                    VStack(spacing: 3) {
                        ForEach(0..<7, id: \.self) { weekday in
                            if let cell = map.columns[week][weekday] {
                                RoundedRectangle(cornerRadius: 3)
                                    .fill(Self.fill(cell.level))
                                    .frame(width: 13, height: 13)
                                    .help("\(cell.day.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())): \(UsageFormat.tokens(cell.tokens)) tokens")
                            } else {
                                Color.clear.frame(width: 13, height: 13)
                            }
                        }
                    }
                }
            }
            HStack(spacing: 4) {
                Text("Less").font(.caption).foregroundStyle(.secondary)
                ForEach(0..<5, id: \.self) { level in
                    RoundedRectangle(cornerRadius: 3).fill(Self.fill(level)).frame(width: 11, height: 11)
                }
                Text("More").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    /// The month's short name over the first week that starts in it.
    static func monthLabel(_ columns: [[Heatmap.Cell?]], _ week: Int) -> String? {
        guard let day = columns[week].first??.day else { return nil }
        let calendar = Calendar.current
        if week > 0, let previous = columns[week - 1].first??.day,
           calendar.component(.month, from: previous) == calendar.component(.month, from: day) { return nil }
        return day.formatted(.dateTime.month(.abbreviated))
    }

    /// One hue, light to dark by level; an empty day is a quiet outline fill.
    static func fill(_ level: Int) -> Color {
        level == 0 ? Color.primary.opacity(0.07) : Theme.accent.opacity([0, 0.3, 0.5, 0.75, 1][level])
    }
}
