import Charts
import QuiverCore
import SwiftUI

/// The usage history at the foot of every Accounts card, shown while the
/// page's "Show history" is on: six weeks or months of the tokens Quiver
/// logged for the account. A Codex account adds OpenAI's account-wide
/// total as a second line and says how much ran elsewhere; no other
/// vendor shares one, so their cards say so instead.
struct AccountHistorySection: View {
    /// Quiver's own figures; nil until the first build lands.
    var history: AccountHistory.History?
    /// The account-wide side, Codex only.
    var codex: CodexAccountSection?
    /// The harness's name, for the note on cards without a total.
    var harnessName: String
    var monthly: Bool
    /// The card's Sign in action, for a Codex account that isn't signed in.
    var signIn: (() -> Void)?

    static let accountColor = Color(light: "#3565c9", dark: "#5b8def")
    static let macColor = Color(light: "#a1a1aa", dark: "#8a8a92")

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Rectangle().fill(Theme.border).frame(height: Theme.hairline)
            if let periods {
                HStack {
                    Text(title).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Text(UsageFormat.tokens(periods.reduce(0) { $0 + ($1.account ?? $1.mac) }))
                        .font(Theme.caption).monospacedDigit().foregroundStyle(Theme.textPrimary)
                }
                if periods.allSatisfy({ $0.mac == 0 && ($0.account ?? 0) == 0 }) {
                    Text(monthly ? "No usage in the last 6 months" : "No usage in the last 6 weeks")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                } else {
                    HistoryChart(periods: periods, monthly: monthly)
                }
            } else {
                Text("Reading usage…").font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            footer
        }
    }

    /// The points to draw: Codex's paired account and Mac figures once
    /// OpenAI answered, else Quiver's figures alone.
    private var periods: [HistoryPoint]? {
        if let codex, codex.kind == .data {
            return (monthly ? codex.months : codex.weeks).map {
                HistoryPoint(start: $0.start, account: $0.account, mac: $0.mac, inProgress: $0.inProgress)
            }
        }
        guard let history else { return nil }
        return (monthly ? history.months : history.weeks).map {
            HistoryPoint(start: $0.start, account: nil, mac: $0.tokens, inProgress: $0.inProgress)
        }
    }

    private var title: String {
        let span = monthly ? "Last 6 months" : "Last 6 weeks"
        return codex?.kind == .data ? "\(span): account and this Mac" : "\(span) on this Mac"
    }

    @ViewBuilder private var footer: some View {
        if let codex {
            switch codex.kind {
            case .data: elsewhere(codex)
            case .loading: note("Reading the account total…")
            case .authRequired:
                HStack {
                    note("Sign in with ChatGPT to see the account total")
                    Spacer()
                    if let signIn { Button("Sign in…", action: signIn).secondaryButton() }
                }
            case .unsupported: note("Update Codex to read the account total")
            case .failed:
                if let failure = codex.failure {
                    note("Couldn't read the account total").hoverTip(failure, replacesLabel: false)
                } else {
                    note("Couldn't read the account total")
                }
            }
        } else {
            note("\(harnessName) doesn't share account-wide totals")
        }
    }

    /// How much of the last four weeks ran off this Mac.
    @ViewBuilder private func elsewhere(_ codex: CodexAccountSection) -> some View {
        let chips = codex.chips
        let stale = codex.stale ? " · couldn't refresh" : ""
        if chips.difference > 0 {
            note("\(UsageFormat.tokens(chips.difference)) elsewhere "
                 + "(\(Int((chips.fraction * 100).rounded()))%) in the last 4 weeks\(stale)")
                .hoverTip("Account total from OpenAI, minus what this Mac logged", replacesLabel: false)
        } else if chips.difference < 0 {
            note("\(UsageFormat.tokens(-chips.difference)) more on this Mac than the account "
                 + "in the last 4 weeks\(stale)")
                .hoverTip("Possible causes: sessions from another account in a shared "
                      + "conversation folder, or OpenAI's day boundary, which isn't your midnight.",
                      replacesLabel: false)
        } else {
            note("All on this Mac in the last 4 weeks\(stale)")
        }
    }

    private func note(_ text: String) -> some View {
        Text(text).font(Theme.caption).foregroundStyle(Theme.textMuted)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// One week or month: the account-wide total where the vendor shares one.
struct HistoryPoint: Equatable {
    var start: Date
    var account: Int?
    var mac: Int
    var inProgress: Bool
}

/// One line per series with no axis: the account total in blue where the
/// vendor shares one, this Mac in grey. The running week or month is the
/// dashed last stretch, and hovering a period reads out its figures.
private struct HistoryChart: View {
    var periods: [HistoryPoint]
    var monthly: Bool
    private static let height: CGFloat = 56

    /// One plotted point. `series` keeps lines apart; a dashed point
    /// belongs to the running period's stretch, drawn as its own series.
    private struct Point: Identifiable {
        var series: String
        var color: Color
        var index: Int
        var tokens: Int
        var id: String { "\(series)|\(index)" }
    }

    /// Account total (where shared) then this Mac, as plain token arrays.
    private var lines: [(name: String, color: Color, values: [Int])] {
        var lines: [(name: String, color: Color, values: [Int])] = []
        if periods.contains(where: { $0.account != nil }) {
            lines.append(("Account", AccountHistorySection.accountColor, periods.map { $0.account ?? 0 }))
        }
        lines.append(("This Mac", AccountHistorySection.macColor, periods.map(\.mac)))
        return lines
    }

    /// Index of the running period, or `periods.count` when none is running.
    private var running: Int {
        periods.last?.inProgress == true ? periods.count - 1 : periods.count
    }

    private var points: (all: [Point], solid: [Point], dashed: [Point], ends: [Point]) {
        var all: [Point] = [], solid: [Point] = [], dashed: [Point] = [], ends: [Point] = []
        let running = running
        for line in lines {
            for (index, tokens) in line.values.enumerated() {
                let point = Point(series: line.name, color: line.color, index: index, tokens: tokens)
                all.append(point)
                if index < running { solid.append(point) }
                if running < periods.count, index >= running - 1 {
                    var stretch = point
                    stretch.series += " so far"
                    dashed.append(stretch)
                }
                if index == line.values.count - 1 { ends.append(point) }
            }
        }
        return (all, solid, dashed, ends)
    }

    var body: some View {
        let points = points
        let peak = max(1, points.all.map(\.tokens).max() ?? 1)
        Chart {
            areas(points.all)
            lines(points.solid, dash: [])
            // The running period: a dashed stretch from the last finished
            // point, so a part week never reads as a drop.
            lines(points.dashed, dash: [3, 3])
            ends(points.ends)
        }
        .chartXScale(domain: -0.5...Double(periods.count) - 0.5)
        .chartYScale(domain: 0...Double(peak) * 1.1)
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
        .frame(height: Self.height)
        // One hover region per period, centred on its point.
        .overlay {
            HStack(spacing: 0) {
                ForEach(periods, id: \.start) { period in
                    Color.clear.contentShape(Rectangle()).hoverTip(help(period))
                }
            }
        }
    }

    @ChartContentBuilder private func areas(_ points: [Point]) -> some ChartContent {
        ForEach(points) { point in
            // Unstacked: each fill follows its own line, not the sum.
            AreaMark(x: .value("Period", point.index), y: .value("Tokens", point.tokens),
                     series: .value("Series", point.series), stacking: .unstacked)
                .interpolationMethod(.monotone)
                .foregroundStyle(LinearGradient(colors: [point.color.opacity(0.18), .clear],
                                                startPoint: .top, endPoint: .bottom))
        }
    }

    @ChartContentBuilder private func lines(_ points: [Point], dash: [CGFloat]) -> some ChartContent {
        ForEach(points) { point in
            LineMark(x: .value("Period", point.index), y: .value("Tokens", point.tokens),
                     series: .value("Series", point.series))
                .interpolationMethod(.monotone)
                .foregroundStyle(point.color)
                .lineStyle(StrokeStyle(lineWidth: 2, lineCap: .round, dash: dash))
        }
    }

    @ChartContentBuilder private func ends(_ points: [Point]) -> some ChartContent {
        ForEach(points) { point in
            PointMark(x: .value("Period", point.index), y: .value("Tokens", point.tokens))
                .foregroundStyle(point.color)
                .symbolSize(18)
        }
    }

    /// "Week of Oct 5 (so far): account 1.1B, this Mac 973.5M".
    private func help(_ period: HistoryPoint) -> String {
        let when = monthly
            ? period.start.formatted(.dateTime.month(.wide))
            : "Week of " + period.start.formatted(.dateTime.month(.abbreviated).day())
        let soFar = period.inProgress ? " (so far)" : ""
        if let account = period.account {
            return "\(when)\(soFar): account \(UsageFormat.tokens(account)), "
                + "this Mac \(UsageFormat.tokens(period.mac))"
        }
        return "\(when)\(soFar): \(UsageFormat.tokens(period.mac))"
    }
}
