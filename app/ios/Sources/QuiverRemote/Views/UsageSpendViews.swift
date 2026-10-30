import Charts
import SwiftUI

/// The Spend half of the Usage page for one period: cost and tokens, a
/// daily chart for the multi-day periods, each harness's share, the
/// quick figures and top models, the activity heatmap (the last weeks,
/// whatever the period) and the daily table.
struct SpendSection: View {
    var overview: OverviewResponse
    var period: UsagePeriod
    var harnessName: (String) -> String

    var body: some View {
        let totals = overview.totals
        let rows = (overview.harnesses ?? []).sorted { ($0.costUsd ?? 0) > ($1.costUsd ?? 0) }
        if (totals?.tokens ?? 0) == 0 {
            StateCard(icon: "chart.bar", title: period == .today ? "Nothing yet today" : "No usage in this period",
                      message: "Tokens and cost come from the agent logs on the hub machine.")
        } else {
            Card {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 16) {
                        Figure(label: "Cost", value: UsageFormat.usd(totals?.costUsd))
                        Figure(label: "Tokens", value: UsageFormat.tokens(totals?.tokens))
                    }
                    if period != .today {
                        let span = OverviewFormat.chartRange(overview)
                        DailyChart(days: UsageFormat.dailySeries(overview.days ?? [], start: span.start, end: span.end))
                    }
                    footnote
                }
            }
            if !rows.isEmpty {
                GroupLabel(text: "By harness", trailing: "Cost and tokens")
                let total = max(rows.reduce(0) { $0 + ($1.costUsd ?? 0) }, 0.000_001)
                CardList(data: rows) { row in
                    SpendRow(row: row, name: harnessName(row.harness), share: (row.costUsd ?? 0) / total)
                }
            }
            GroupLabel(text: "Quick figures")
            QuickFiguresCard(overview: overview, harnessName: harnessName)
        }
        if let heatmap = overview.heatmap, !(heatmap.days ?? []).isEmpty {
            GroupLabel(text: "Activity")
            ActivityHeatmapCard(heatmap: heatmap)
        }
        if let days = overview.days, !days.isEmpty {
            GroupLabel(text: "Daily breakdown", trailing: "\(days.count) active day\(days.count == 1 ? "" : "s")")
            DailyTable(days: days).id(period)
        }
    }

    /// Where the figures come from, and what the cost leaves out.
    @ViewBuilder private var footnote: some View {
        let unpriced = overview.totals?.unpricedTokens ?? 0
        let source = overview.machine.map { "From the logs on \(UsageMachineInfo(id: nil, name: $0, online: nil).displayName)" }
        if source != nil || unpriced > 0 {
            VStack(alignment: .leading, spacing: 2) {
                if let source { Text(source) }
                if unpriced > 0 { Text("\(UsageFormat.tokens(unpriced)) tokens from unpriced models aren't in the cost") }
            }
            .font(Theme.caption).foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// A big figure under a quiet label.
private struct Figure: View {
    var label: String
    var value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(Theme.caption).foregroundStyle(Theme.textSecondary)
            Text(value)
                .font(.system(size: 28, weight: .semibold).monospacedDigit())
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Cost per day as small ink bars, with days off as gaps.
struct DailyChart: View {
    var days: [DailySpend]

    var body: some View {
        let top = max(days.map(\.cost).max() ?? 0, 1)
        Chart(days) { day in
            BarMark(x: .value("Day", day.date, unit: .day), y: .value("Cost", day.cost))
                .foregroundStyle(Theme.textPrimary.opacity(0.85))
                .cornerRadius(2)
        }
        .chartYScale(domain: 0...top * 1.08)
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
                AxisGridLine().foregroundStyle(Theme.border)
                AxisValueLabel {
                    if let v = value.as(Double.self) {
                        Text(UsageFormat.usdAxis(v)).font(.system(size: 11).monospacedDigit()).foregroundStyle(Theme.textMuted)
                    }
                }
            }
        }
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: days.count > 10 ? 4 : days.count)) { _ in
                AxisValueLabel(format: days.count > 10 ? .dateTime.month(.abbreviated).day() : .dateTime.day(),
                               centered: true)
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.textMuted)
            }
        }
        .frame(height: 120)
        .accessibilityLabel("Cost per day")
    }
}

/// One harness: logo and name, its cost, a share bar in its colour and
/// its token count.
private struct SpendRow: View {
    var row: OverviewResponse.Harness
    var name: String
    var share: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                HarnessTitle(id: row.harness, name: name, size: 16, font: Theme.rowTitle)
                Spacer(minLength: 8)
                Text(UsageFormat.usd(row.costUsd))
                    .font(Theme.rowTitle.monospacedDigit()).foregroundStyle(Theme.textPrimary)
            }
            HStack(spacing: 10) {
                UsageTrack(fraction: share, color: Theme.harness(row.harness))
                Text("\(UsageFormat.tokens(row.tokens)) tokens")
                    .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary)
                    .lineLimit(1)
                    .frame(minWidth: 84, alignment: .trailing)
            }
        }
        .padding(.vertical, Theme.rowVertical)
        .padding(.horizontal, Theme.rowHorizontal)
        .accessibilityElement(children: .combine)
    }
}
