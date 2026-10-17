import QuiverCore
import SwiftUI

/// The usage history at the foot of every Accounts card, shown while the
/// page's "Show history" is on: six weeks or months of the tokens Quiver
/// logged for the account. A Codex account adds OpenAI's account-wide
/// total beside each bar and says how much ran elsewhere; no other
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
            if let bars {
                HStack {
                    Text(title).font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Text(UsageFormat.tokens(bars.reduce(0) { $0 + ($1.account ?? $1.mac) }))
                        .font(Theme.caption).monospacedDigit().foregroundStyle(Theme.textPrimary)
                }
                if bars.allSatisfy({ $0.mac == 0 && ($0.account ?? 0) == 0 }) {
                    Text(monthly ? "No usage in the last 6 months" : "No usage in the last 6 weeks")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                } else {
                    HistoryChart(bars: bars, monthly: monthly)
                }
            } else {
                Text("Reading usage…").font(Theme.caption).foregroundStyle(Theme.textMuted)
            }
            footer
        }
    }

    /// The bars to draw: Codex's paired account and Mac figures once
    /// OpenAI answered, else Quiver's figures alone.
    private var bars: [HistoryBar]? {
        if let codex, codex.kind == .data {
            return (monthly ? codex.months : codex.weeks).map {
                HistoryBar(start: $0.start, account: $0.account, mac: $0.mac, inProgress: $0.inProgress)
            }
        }
        guard let history else { return nil }
        return (monthly ? history.months : history.weeks).map {
            HistoryBar(start: $0.start, account: nil, mac: $0.tokens, inProgress: $0.inProgress)
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
                note("Couldn't read the account total").help(codex.failure ?? "")
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
                .help("Account total from OpenAI, minus what this Mac logged")
        } else if chips.difference < 0 {
            note("\(UsageFormat.tokens(-chips.difference)) more on this Mac than the account "
                 + "in the last 4 weeks\(stale)")
                .help("Possible causes: sessions from another account in a shared "
                      + "conversation folder, or OpenAI's day boundary, which isn't your midnight.")
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
struct HistoryBar: Equatable {
    var start: Date
    var account: Int?
    var mac: Int
    var inProgress: Bool
}

/// Six bar groups with no axis: hovering one reads out its figures.
private struct HistoryChart: View {
    var bars: [HistoryBar]
    var monthly: Bool
    private static let height: CGFloat = 56

    var body: some View {
        let peak = max(1, bars.map { max($0.account ?? 0, $0.mac) }.max() ?? 1)
        HStack(alignment: .bottom, spacing: 8) {
            ForEach(bars, id: \.start) { bar in
                HStack(alignment: .bottom, spacing: 2) {
                    if let account = bar.account {
                        column(account, peak: peak, color: AccountHistorySection.accountColor,
                               inProgress: bar.inProgress)
                    }
                    column(bar.mac, peak: peak, color: AccountHistorySection.macColor,
                           inProgress: bar.inProgress)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
                .contentShape(Rectangle())
                .help(help(bar))
            }
        }
        .frame(height: Self.height)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Theme.border).frame(height: Theme.hairline)
        }
    }

    private func column(_ tokens: Int, peak: Int, color: Color, inProgress: Bool) -> some View {
        let height = max(2, Self.height * Double(tokens) / Double(peak))
        return UnevenRoundedRectangle(topLeadingRadius: 2, topTrailingRadius: 2)
            .fill(color.opacity(inProgress ? 0.5 : 1))
            .frame(maxWidth: .infinity)
            .frame(height: height)
    }

    /// "Week of Oct 5 (so far): account 1.1B, this Mac 973.5M".
    private func help(_ bar: HistoryBar) -> String {
        let when = monthly
            ? bar.start.formatted(.dateTime.month(.wide))
            : "Week of " + bar.start.formatted(.dateTime.month(.abbreviated).day())
        let soFar = bar.inProgress ? " (so far)" : ""
        if let account = bar.account {
            return "\(when)\(soFar): account \(UsageFormat.tokens(account)), "
                + "this Mac \(UsageFormat.tokens(bar.mac))"
        }
        return "\(when)\(soFar): \(UsageFormat.tokens(bar.mac))"
    }
}
