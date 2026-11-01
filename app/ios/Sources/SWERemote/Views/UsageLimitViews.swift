import SwiftUI

/// The Limits half of the Usage page: one card per harness that reports a
/// quota, then the rest as session counts. Grouped by machine only when a
/// satellite has reported too.
struct LimitsSection: View {
    var usage: UsageResponse
    var now: Date

    var body: some View {
        let machines = usage.allMachines
        let grouped = machines.count > 1
        if !machines.contains(where: { $0.rows.contains { $0.rateLimit != nil } }) && !grouped {
            StateCard(icon: "gauge.with.dots.needle.0percent", title: "No limits to show",
                      message: "Star a harness on the hub with swe hs star <name> to track its quota.")
        }
        ForEach(machines) { machine in
            if grouped { MachineLabel(machine: machine, serverTime: usage.serverTime, now: now) }
            MachineLimits(machine: machine, serverTime: usage.serverTime, now: now, showEmpty: grouped)
        }
    }
}

/// A machine's name over its cards, with how old a satellite's report is.
private struct MachineLabel: View {
    var machine: UsageMachine
    var serverTime: Double?
    var now: Date

    var body: some View {
        let name = machine.machine?.displayName ?? "Unknown machine"
        if machine.hub == true {
            GroupLabel(text: name, trailing: "Hub")
        } else {
            let age = max(0, (serverTime ?? now.timeIntervalSince1970) - (machine.receivedAt ?? 0))
            let offline = machine.machine?.online == false
            let late = age > UsageFormat.staleReportS
            GroupLabel(text: name, trailing: (offline ? "Offline, reported " : "Reported ") + UsageFormat.ago(age))
                .opacity(late || offline ? 0.7 : 1)
        }
    }
}

private struct MachineLimits: View {
    var machine: UsageMachine
    var serverTime: Double?
    var now: Date
    var showEmpty: Bool

    var body: some View {
        let limited = machine.rows.filter { $0.rateLimit != nil }
        let others = machine.rows.filter { $0.rateLimit == nil && ($0.sessions100d ?? 0) > 0 }
            .sorted { ($0.sessions100d ?? 0) > ($1.sessions100d ?? 0) }
        if limited.isEmpty && showEmpty {
            Text("No limits reported on this machine.")
                .font(Theme.caption).foregroundStyle(Theme.textMuted).padding(.horizontal, 4)
        }
        ForEach(limited) { harness in
            LimitCard(harness: harness, machine: machine, serverTime: serverTime, now: now)
        }
        if !others.isEmpty {
            GroupLabel(text: "Other harnesses", trailing: "Sessions, \(machine.windowDays ?? 100) days")
            SessionCountList(rows: others)
        }
    }
}

/// One harness's quota: logo and name, a bar per window, and a muted note
/// when the reading is a kept older one.
struct LimitCard: View {
    var harness: UsageHarness
    var machine: UsageMachine
    var serverTime: Double?
    var now: Date

    var body: some View {
        let limit = harness.rateLimit
        let stale = UsageFormat.isStale(fetchedAt: limit?.fetchedAt, snapshotAt: machine.reportedAt ?? machine.receivedAt)
        Card {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 8) {
                    HarnessTitle(id: harness.name, name: harness.title)
                    Spacer(minLength: 8)
                    if let plan = UsageFormat.plan(limit?.planType) { Badge(plan, tint: Theme.textSecondary) }
                }
                if let limit, limit.isOK {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(limit.displayWindows.enumerated()), id: \.offset) { _, window in
                            LimitBar(window: window, now: now)
                        }
                    }
                    .opacity(stale ? 0.55 : 1)
                    extras(limit)
                } else {
                    Text(UsageFormat.limitStatus(limit?.status, machine: machine.machine?.displayName ?? "that machine"))
                        .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                }
                if stale, let fetched = limit?.fetchedAt {
                    let age = UsageFormat.readingAge(fetchedAt: fetched, reportedAt: machine.reportedAt,
                                                     receivedAt: machine.receivedAt, serverTime: serverTime)
                    Label("Stale: last read \(UsageFormat.ago(age)), newer reads failed", systemImage: "clock.arrow.circlepath")
                        .font(Theme.caption).foregroundStyle(Theme.textMuted)
                }
            }
        }
    }

    /// Credits, prepaid balance and on-demand spend, when the provider has them.
    @ViewBuilder private func extras(_ limit: RateLimit) -> some View {
        let lines = Self.extraLines(limit)
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(lines, id: \.self) { Text($0) }
            }
            .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary)
        }
    }

    static func extraLines(_ limit: RateLimit) -> [String] {
        var lines: [String] = []
        if let total = limit.totalUnits, let left = limit.remainingUnits {
            lines.append(total < 0 ? "Unlimited credits" : "\(Int(left)) of \(Int(total)) credits left")
        }
        if let extra = limit.extraUsageUsd { lines.append("\(UsageFormat.usd(extra)) extra usage left") }
        if let od = limit.onDemand {
            if od.state == "fixed", let cap = od.limitUsd {
                lines.append("On-demand: \(UsageFormat.usd(od.usedUsd)) of \(UsageFormat.usd(cap))")
            } else if od.state == "unlimited" {
                lines.append("On-demand: \(UsageFormat.usd(od.usedUsd)), no limit")
            }
        }
        return lines
    }
}

/// One quota window: label, reset countdown and percent over a 6 pt bar.
struct LimitBar: View {
    var window: LimitWindow
    var now: Date

    var body: some View {
        let used = UsageFormat.clampPercent(window.usedPercent)
        let unavailable = window.unavailable == true
        let tone = UsageFormat.tone(usedPercent: used, limitReached: window.limitReached == true)
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(window.label).font(Theme.control).foregroundStyle(Theme.textPrimary).lineLimit(1)
                Spacer(minLength: 4)
                Text(unavailable ? asOf : UsageFormat.resetCountdown(window.resetAt, now: now.timeIntervalSince1970))
                    .font(Theme.caption.monospacedDigit()).foregroundStyle(Theme.textSecondary).lineLimit(1)
                Text(unavailable ? "Unavailable" : "\(UsageFormat.percent(used)) used")
                    .font(Theme.control.monospacedDigit())
                    .foregroundStyle(unavailable ? Theme.textMuted : Self.textColor(tone))
                    .lineLimit(1)
            }
            UsageTrack(fraction: unavailable ? 0 : used / 100, color: Self.fillColor(tone))
        }
        .accessibilityElement(children: .combine)
    }

    private var asOf: String {
        guard let at = window.asOf else { return "" }
        return "as of \(UsageFormat.ago(now.timeIntervalSince1970 - at))"
    }

    static func fillColor(_ tone: UsageFormat.Tone) -> Color {
        switch tone {
        case .normal: Theme.textPrimary
        case .warning: Theme.warning
        case .danger: Theme.danger
        }
    }

    static func textColor(_ tone: UsageFormat.Tone) -> Color {
        switch tone {
        case .normal: Theme.textPrimary
        case .warning: Theme.warningText
        case .danger: Theme.dangerText
        }
    }
}

/// The 6 pt bar: a quiet track and a rounded fill (the Mac's `UsageTrack`).
struct UsageTrack: View {
    var fraction: Double
    var color: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.usageTrack)
                Capsule().fill(color).frame(width: max(0, min(1, fraction)) * geo.size.width)
            }
        }
        .frame(height: Theme.usageTrackHeight)
    }
}

/// Harnesses with no quota, as their 100-day session counts.
struct SessionCountList: View {
    var rows: [UsageHarness]

    var body: some View {
        CardList(data: rows) { harness in
            HStack(spacing: 10) {
                HarnessTitle(id: harness.name, name: harness.title, size: 16, font: Theme.body)
                Spacer(minLength: 8)
                Text("\(harness.sessions100d ?? 0)")
                    .font(Theme.body.monospacedDigit()).foregroundStyle(Theme.textSecondary)
            }
            .padding(.vertical, 10)
            .padding(.horizontal, Theme.rowHorizontal)
        }
    }
}

/// A harness's logo beside its name; the name alone when no logo is bundled.
struct HarnessTitle: View {
    var id: String
    var name: String
    var size: CGFloat = 18
    var font: Font = Theme.cardTitle

    var body: some View {
        HStack(spacing: 8) {
            if HarnessIcon.assetName(id) != nil {
                HarnessIcon(harness: id, name: name, size: size)
            }
            Text(name).font(font).foregroundStyle(Theme.textPrimary).lineLimit(1)
        }
    }
}
