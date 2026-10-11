import AppKit
import Charts
import QuiverCore
import SwiftUI
import WidgetKit

/// The app's colour roles, copied from `QuiverApp/Theme.swift` (the
/// TokenTracker package's tokens.json), which this target can't import.
enum Palette {
    static let card = Color(light: "#ffffff", dark: "#171717")
    static let textPrimary = Color(light: "#0a0a0a", dark: "#fafafa")
    static let textSecondary = Color(light: "#737373", dark: "#a3a3a3")
    static let track = Color(light: "#f5f5f5", dark: "#40404080")
    static let accent = Color(light: "#059669", dark: "#059669")
    static let accentFill = Color(light: "#10b981", dark: "#10b981")
    static let warning = Color(light: "#f59e0b", dark: "#fbbf24")
    static let danger = Color(light: "#ef4444", dark: "#f87171")
    static let heatmap: [Color] = [
        Color(light: "#ebedf0", dark: "#262626"),
        Color(light: "#a7f3d0", dark: "#065f46"),
        Color(light: "#6ee7b7", dark: "#059669"),
        Color(light: "#34d399", dark: "#10b981"),
        Color(light: "#10b981", dark: "#34d399"),
    ]

    static func harness(_ id: String) -> Color {
        switch id {
        case "claude": Color(light: "#cf6848", dark: "#cf6848")
        case "codex": Color(light: "#3b7ff0", dark: "#3b7ff0")
        case "opencode": Color(light: "#139f8b", dark: "#139f8b")
        case "devin": Color(light: "#c4860f", dark: "#c4860f")
        default: .gray
        }
    }

    static func usage(_ percent: Int) -> Color {
        percent >= 90 ? danger : percent >= 70 ? warning : accentFill
    }
}

extension Color {
    init(light: String, dark: String) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
    }
}

extension NSColor {
    convenience init(hex: String) {
        var text = hex
        if text.hasPrefix("#") { text.removeFirst() }
        let value = UInt64(text, radix: 16) ?? 0
        let hasAlpha = text.count == 8
        self.init(srgbRed: Double((value >> (hasAlpha ? 24 : 16)) & 0xff) / 255,
                  green: Double((value >> (hasAlpha ? 16 : 8)) & 0xff) / 255,
                  blue: Double((value >> (hasAlpha ? 8 : 0)) & 0xff) / 255,
                  alpha: hasAlpha ? Double(value & 0xff) / 255 : 1)
    }
}

/// The card every widget sits on, and what shows before the app has
/// written a snapshot.
struct WidgetFrame<Content: View>: View {
    var entry: SnapshotEntry
    @ViewBuilder var content: (WidgetSnapshot) -> Content

    var body: some View {
        Group {
            if let snapshot = entry.snapshot {
                content(snapshot)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Quiver").font(.system(size: 13, weight: .semibold))
                    Text("Open Quiver once to fill this widget.")
                        .font(.system(size: 11)).foregroundStyle(Palette.textSecondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .foregroundStyle(Palette.textPrimary)
        .containerBackground(Palette.card, for: .widget)
    }
}

/// A thin bar, as in TokenTracker's limits and models widgets.
struct ThinBar: View {
    var fraction: Double
    var color: Color

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.track)
                Capsule().fill(color).frame(width: max(0, min(1, fraction)) * geo.size.width)
                    .widgetAccentable()
            }
        }
        .frame(height: 3)
    }
}

// MARK: - Usage limits

struct LimitsView: View {
    var snapshot: WidgetSnapshot
    var now: Date
    @Environment(\.widgetFamily) private var family

    var rows: Int {
        switch family {
        case .systemSmall: 3
        case .systemLarge: 6
        default: 4
        }
    }

    var body: some View {
        let limits = snapshot.limits.prefix(rows)
        VStack(alignment: .leading, spacing: family == .systemSmall ? 6 : 8) {
            if limits.isEmpty {
                Text("No limits yet").font(.system(size: 12, weight: .medium))
                Text("Add an account in Quiver, or star a harness in swe.")
                    .font(.system(size: 11)).foregroundStyle(Palette.textSecondary)
            }
            ForEach(Array(limits), id: \.id) { reading in
                LimitLine(reading: reading, now: now, compact: family == .systemSmall,
                          old: reading.isOld(now: now, fallback: snapshot.generatedAt))
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct LimitLine: View {
    var reading: LimitReading
    var now: Date
    var compact: Bool
    /// Stale, or not refreshed lately (Quiver isn't running).
    var old: Bool

    var body: some View {
        // Old readings keep their last figure, dimmed and dated; only a
        // window whose reset time has passed says "reset".
        let reset = reading.hasReset(now: now)
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 5) {
                Circle().fill(Palette.harness(reading.harness)).frame(width: 6, height: 6)
                Text(reading.window.isEmpty ? reading.title : "\(reading.title) · \(reading.window)")
                    .font(.system(size: 11, weight: .medium)).lineLimit(1)
                Spacer(minLength: 4)
                if !compact, !reset, old, let taken = reading.fetchedAt {
                    Text(UsageFormat.ago(taken, now: now))
                        .font(.system(size: 10)).foregroundStyle(Palette.textSecondary)
                } else if !compact, !reset, let resetsAt = reading.resetsAt {
                    Text(UsageFormat.resetIn(resetsAt, now: now))
                        .font(.system(size: 10)).foregroundStyle(Palette.textSecondary)
                }
                Text(reset ? "reset" : "\(reading.percent)%")
                    .font(.system(size: 11, weight: .semibold).monospacedDigit())
            }
            ThinBar(fraction: reset ? 0 : reading.usedPercent / 100, color: Palette.usage(reading.percent))
        }
        .opacity(old ? 0.5 : 1)
    }
}

// MARK: - Usage summary

struct SummaryView: View {
    var snapshot: WidgetSnapshot
    var now: Date

    var body: some View {
        let points = snapshot.sparkline(days: 14, now: now, calendar: .current)
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 24) {
                FigureBlock(title: "Today", figure: snapshot.today(at: now, calendar: .current))
                FigureBlock(title: "7 days", figure: snapshot.week)
                Spacer(minLength: 0)
                if snapshot.age(at: now) > 3_600 {
                    Text("Updated \(UsageFormat.ago(snapshot.generatedAt, now: now))")
                        .font(.system(size: 10)).foregroundStyle(Palette.textSecondary)
                }
            }
            Chart(Array(points.enumerated()), id: \.offset) { point in
                AreaMark(x: .value("Day", point.offset), y: .value("Tokens", point.element))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(LinearGradient(colors: [Palette.accentFill.opacity(0.35), .clear],
                                                    startPoint: .top, endPoint: .bottom))
                LineMark(x: .value("Day", point.offset), y: .value("Tokens", point.element))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(Palette.accentFill)
                    .lineStyle(StrokeStyle(lineWidth: 1.5))
            }
            .chartXAxis(.hidden)
            .chartYAxis(.hidden)
            .widgetAccentable()
        }
    }
}

private struct FigureBlock: View {
    var title: String
    var figure: WidgetSnapshot.Figure

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(.system(size: 10, weight: .medium)).foregroundStyle(Palette.textSecondary)
            Text(UsageFormat.tokens(figure.tokens)).font(.system(size: 22, weight: .bold)).monospacedDigit()
            Text(UsageFormat.dollars(figure.costUSD))
                .font(.system(size: 10, weight: .medium)).monospacedDigit()
                .foregroundStyle(Palette.accent)
        }
    }
}

// MARK: - Activity heatmap

struct HeatmapView: View {
    var snapshot: WidgetSnapshot
    var now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geo in
                let gap: CGFloat = 2
                let cell = min((geo.size.height - 6 * gap) / 7, 10)
                let weeks = min(WidgetSnapshot.heatmapWeeks, Int((geo.size.width + gap) / (cell + gap)))
                let map = snapshot.heatmap(weeks: max(weeks, 1), now: now, calendar: .current)
                HStack(alignment: .top, spacing: gap) {
                    ForEach(Array(map.columns.enumerated()), id: \.offset) { _, column in
                        VStack(spacing: gap) {
                            ForEach(0..<7, id: \.self) { row in
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(column[row].map { Palette.heatmap[$0.level] } ?? .clear)
                                    .frame(width: cell, height: cell)
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .center)
                .widgetAccentable()
            }
            HStack(spacing: 4) {
                Text(UsageFormat.tokens(snapshot.activityTokens)).font(.system(size: 11, weight: .semibold))
                Text("tokens · \(snapshot.activeDays) active days")
                    .font(.system(size: 11)).foregroundStyle(Palette.textSecondary)
            }
        }
    }
}

// MARK: - Top models

struct ModelsView: View {
    var snapshot: WidgetSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if snapshot.models.isEmpty {
                Text("No usage in the last 30 days").font(.system(size: 12, weight: .medium))
            }
            ForEach(Array(snapshot.models.enumerated()), id: \.offset) { index, model in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 5) {
                        Circle().fill(Palette.accentFill.opacity(1 - Double(index) * 0.2)).frame(width: 6, height: 6)
                        Text(model.name).font(.system(size: 11, weight: .medium)).lineLimit(1)
                        Spacer(minLength: 4)
                        Text(UsageFormat.tokens(model.tokens))
                            .font(.system(size: 10).monospacedDigit()).foregroundStyle(Palette.textSecondary)
                        Text("\(Int((model.share * 100).rounded()))%")
                            .font(.system(size: 10).monospacedDigit()).foregroundStyle(Palette.textSecondary)
                            .frame(width: 30, alignment: .trailing)
                    }
                    ThinBar(fraction: model.share, color: Palette.accentFill.opacity(1 - Double(index) * 0.2))
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

// MARK: - Preview data

extension WidgetSnapshot {
    /// Shown in the widget gallery before the app has written a snapshot.
    static var preview: WidgetSnapshot {
        let calendar = Calendar.current
        let now = Date.now
        let today = calendar.startOfDay(for: now)
        let activity = (0..<120).compactMap { back -> DayRow? in
            guard back % 3 != 1, let day = calendar.date(byAdding: .day, value: -back, to: today) else { return nil }
            var totals = UsageTotals()
            totals.inputTokens = 2_000_000 + (back * 7_919) % 9_000_000
            totals.costUSD = Double(totals.inputTokens) / 1_000_000 * 1.8
            return DayRow(day: day, totals: totals)
        }
        func model(_ name: String, _ tokens: Int, _ share: Double) -> ModelShare {
            var totals = UsageTotals()
            totals.inputTokens = tokens
            return ModelShare(model: name, totals: totals, share: share)
        }
        let limits = [
            LimitReading(source: "claude-default", harness: "claude", title: "Claude: Default", window: "7d",
                         usedPercent: 61, resetsAt: now + 86_400, fetchedAt: now, isStale: false),
            LimitReading(source: "claude-default", harness: "claude", title: "Claude: Default", window: "5h",
                         usedPercent: 4, resetsAt: now + 16_000, fetchedAt: now, isStale: false),
            LimitReading(source: "codex-default", harness: "codex", title: "Codex: Default", window: "7d",
                         usedPercent: 32, resetsAt: now + 90_000, fetchedAt: now, isStale: false),
        ]
        return make(activity: activity,
                    models: [model("claude-opus-5-5", 586_400_000, 0.59), model("claude-sonnet-5-5", 218_700_000, 0.22),
                             model("gpt-5.4", 80_600_000, 0.08), model("composer-2", 52_100_000, 0.05)],
                    limits: limits, now: now, calendar: calendar)
    }
}
