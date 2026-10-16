import QuiverCore
import SwiftUI

/// The account-vs-Mac comparison inside a Codex account card: the last
/// four weeks as chips, then six weeks or six months of grouped bars
/// and a table, from `CodexAccountUsage`.
struct CodexCompareSection: View {
    var section: CodexAccountSection
    /// The card's existing Sign in action, for the auth-required state.
    var signIn: () -> Void
    @State private var tab = Tab.weekly

    private enum Tab: Hashable { case weekly, monthly }

    /// The mockup's account blue; This Mac stays a quiet grey.
    static let accountColor = Color(light: "#3565c9", dark: "#5b8def")
    static let macColor = Color(light: "#a1a1aa", dark: "#8a8a92")

    var body: some View {
        switch section.kind {
        case .loading:
            if !section.stale {
                Text("Reading account usage…")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        case .authRequired:
            VStack(spacing: 8) {
                Text("Codex couldn't read this account's usage: it isn't signed in with ChatGPT.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.center)
                Button("Sign in…", action: signIn).secondaryButton()
            }
            .frame(maxWidth: .infinity)
            .padding(14)
            .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius)
                .strokeBorder(Theme.border, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
        case .unsupported:
            Text("Update Codex to read account totals")
                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
        case .data:
            data
        }
    }

    @ViewBuilder private var data: some View {
        VStack(alignment: .leading, spacing: 10) {
            caption("Last 4 weeks")
            HStack(spacing: 8) {
                CompareChip(value: UsageFormat.tokens(section.chips.account),
                            label: "Account total",
                            help: "All devices and Codex Cloud, from OpenAI")
                CompareChip(value: UsageFormat.tokens(section.chips.mac),
                            label: "This Mac",
                            help: "This Mac's Codex logs")
                CompareChip(
                    value: signed(section.chips.difference),
                    label: section.chips.difference >= 0
                        ? "Elsewhere (\(percent(section.chips.fraction)))"
                        : "More on this Mac",
                    help: "Account total minus this Mac",
                    warn: section.chips.difference < 0)
            }
            HStack {
                caption(tab == .weekly ? "Weekly tokens" : "Monthly tokens")
                Spacer()
                Tabs(options: [(Tab.weekly, "Weekly"), (Tab.monthly, "Monthly")],
                     selection: $tab, style: .table)
            }
            let periods = tab == .weekly ? section.weeks : section.months
            legend
            CompareChart(periods: periods, weekly: tab == .weekly)
            CompareTable(periods: periods, weekly: tab == .weekly)
            if (tab == .weekly ? section.hasNegativeWeek : section.hasNegativeMonth) {
                Text("This Mac shows more than the account here. Possible causes: "
                     + "sessions from another account in a shared conversation folder, "
                     + "or OpenAI's day boundary, which isn't your midnight.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if section.stale {
                Text("Couldn't refresh — showing the last reading.")
                    .font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
        }
    }

    private var legend: some View {
        HStack(spacing: 12) {
            Label { Text("Account total") } icon: {
                RoundedRectangle(cornerRadius: 2).fill(Self.accountColor).frame(width: 9, height: 9)
            }
            Label { Text("This Mac") } icon: {
                RoundedRectangle(cornerRadius: 2).fill(Self.macColor).frame(width: 9, height: 9)
            }
            Label { Text("Still in progress") } icon: {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Self.macColor.opacity(0.35))
                    .overlay(Hatch(color: .white.opacity(0.5)))
                    .clipShape(RoundedRectangle(cornerRadius: 2))
                    .frame(width: 9, height: 9)
            }
        }
        .font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
    }

    private func caption(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.system(size: 10)).tracking(0.4)
            .foregroundStyle(Theme.textMuted)
    }

    private func signed(_ tokens: Int) -> String {
        (tokens < 0 ? "−" : "+") + UsageFormat.tokens(abs(tokens))
    }

    private func percent(_ fraction: Double) -> String {
        "\(Int((abs(fraction) * 100).rounded()))%"
    }
}

/// One of the three figures over the comparison window.
private struct CompareChip: View {
    var value: String
    var label: String
    var help = ""
    var warn = false

    var body: some View {
        VStack(spacing: 2) {
            Text(value).font(Theme.rowTitle).monospacedDigit()
                .foregroundStyle(warn ? Theme.warningText : Theme.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.85)
            Text(label).font(.system(size: 10)).foregroundStyle(Theme.textSecondary)
                .lineLimit(1).minimumScaleFactor(0.85)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Theme.subtle, in: .rect(cornerRadius: Theme.blockRadius))
        .help(help)
    }
}

/// Paired bars, one per week or month; the running period is hatched.
private struct CompareChart: View {
    var periods: [CodexComparePeriod]
    var weekly: Bool

    var body: some View {
        let peak = periods.map { max($0.account, $0.mac) }.max() ?? 1
        HStack(alignment: .bottom, spacing: 14) {
            ForEach(periods, id: \.start) { period in
                VStack(spacing: 4) {
                    HStack(alignment: .bottom, spacing: 3) {
                        bar(period.account, peak: peak,
                            color: CodexCompareSection.accountColor,
                            inProgress: period.inProgress)
                        bar(period.mac, peak: peak,
                            color: CodexCompareSection.macColor,
                            inProgress: period.inProgress)
                    }
                    .frame(height: 110, alignment: .bottom)
                    Text(label(period))
                        .font(.system(size: 9)).foregroundStyle(Theme.textMuted)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: 1)
                .offset(y: -22)
        }
    }

    private func bar(_ tokens: Int, peak: Int, color: Color, inProgress: Bool) -> some View {
        let height = peak > 0 ? max(2, 110 * Double(tokens) / Double(peak)) : 2
        return RoundedRectangle(cornerRadius: 3)
            .fill(color.opacity(inProgress ? 0.5 : 1))
            .overlay {
                if inProgress { Hatch(color: .white.opacity(0.35)) }
            }
            .clipShape(RoundedRectangle(cornerRadius: 3))
            .frame(width: 16, height: height)
            .help(UsageFormat.tokens(tokens))
    }

    private func label(_ period: CodexComparePeriod) -> String {
        if weekly {
            return period.start.formatted(.dateTime.month(.abbreviated).day())
        }
        return period.start.formatted(.dateTime.month(.abbreviated))
    }
}

/// Diagonal stripes drawn inside whatever shape clips the overlay.
private struct Hatch: View {
    var color: Color

    var body: some View {
        Canvas { context, size in
            var path = Path()
            let spacing: CGFloat = 6
            var x = -size.height
            while x < size.width {
                path.move(to: CGPoint(x: x, y: size.height))
                path.addLine(to: CGPoint(x: x + size.height, y: 0))
                x += spacing
            }
            context.stroke(path, with: .color(color), lineWidth: 1.5)
        }
    }
}

/// The figures under the chart; a negative difference is flagged.
private struct CompareTable: View {
    var periods: [CodexComparePeriod]
    var weekly: Bool

    var body: some View {
        VStack(spacing: 0) {
            row(period: nil, cells: [weekly ? "Week of" : "Month",
                                   "Account", "This Mac", "Elsewhere"])
                .font(Theme.tableHeader).foregroundStyle(Theme.tableHeaderText)
                .padding(.vertical, 4)
            ForEach(periods, id: \.start) { period in
                Rectangle().fill(Theme.rowDivider).frame(height: Theme.hairline)
                row(period: period, cells: [
                    title(period),
                    UsageFormat.tokens(period.account),
                    UsageFormat.tokens(period.mac),
                    diff(period),
                ])
                .font(Theme.bodySmall).foregroundStyle(Theme.textPrimary).monospacedDigit()
                .padding(.vertical, 7)
            }
        }
    }

    private func row(period: CodexComparePeriod?, cells: [String]) -> some View {
        HStack(spacing: 10) {
            Text(cells[0]).lineLimit(1)
                .help(period?.inProgress == true ? "(so far)" : "")
                .frame(maxWidth: .infinity, alignment: .leading)
            ForEach(1..<cells.count, id: \.self) { index in
                Text(cells[index])
                    .foregroundStyle(
                        index == 3 && (period?.difference ?? 0) < 0
                            ? AnyShapeStyle(Theme.warningText) : AnyShapeStyle(.foreground))
                    .lineLimit(1).minimumScaleFactor(0.85)
                    .frame(width: 68, alignment: .trailing)
            }
        }
    }

    private func title(_ period: CodexComparePeriod) -> String {
        weekly
            ? period.start.formatted(.dateTime.month(.abbreviated).day())
            : period.start.formatted(.dateTime.month(.wide))
    }

    private func diff(_ period: CodexComparePeriod) -> String {
        let sign = period.difference < 0 ? "−" : "+"
        return sign + UsageFormat.tokens(abs(period.difference))
    }
}
