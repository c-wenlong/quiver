import AppKit
import Charts
import SWECore
import SwiftUI
import WidgetKit

/// The app's colour roles, copied from `SWEApp/Theme.swift` (the Devin
/// desktop package's tokens.json), which the extension can't import.
/// The widget surface is #262626 in dark, one step up from the app's
/// #252525 card so it stands off a dark desktop.
public enum Palette {
    public static let card = Color(light: "#fefefe", dark: "#262626")
    public static let textPrimary = Color(light: "#0d0d0de6", dark: "#ffffffe6")
    public static let textSecondary = Color(light: "#0d0d0d8f", dark: "#ffffff85")
    public static let track = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    public static let accent = Color(light: "#059669", dark: "#059669")
    public static let accentFill = Color(light: "#10b981", dark: "#10b981")
    public static let warning = Color(light: "#f58e3a", dark: "#f58e3a")
    public static let danger = Color(light: "#f53b3a", dark: "#f53b3a")
    public static let heatmap: [Color] = [
        // #404040 at 50%: the empty step must show on the #262626 surface.
        Color(light: "#ebedf0", dark: "#40404080"),
        Color(light: "#a7f3d0", dark: "#065f46"),
        Color(light: "#6ee7b7", dark: "#059669"),
        Color(light: "#34d399", dark: "#10b981"),
        Color(light: "#10b981", dark: "#34d399"),
    ]

    public static func usage(_ percent: Int) -> Color {
        percent >= 90 ? danger : percent >= 70 ? warning : accentFill
    }
}

extension Color {
    public init(light: String, dark: String) {
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

/// One timeline entry: the snapshot the widget draws at `date`, or none
/// until the app has written one.
public struct SnapshotEntry: TimelineEntry {
    public var date: Date
    /// Nil until the app has written one.
    public var snapshot: WidgetSnapshot?

    public init(date: Date, snapshot: WidgetSnapshot?) {
        self.date = date
        self.snapshot = snapshot
    }
}

/// The card every widget sits on, and what shows before the app has
/// written a snapshot.
public struct WidgetFrame<Content: View>: View {
    public var entry: SnapshotEntry
    @ViewBuilder public var content: (WidgetSnapshot) -> Content

    public init(entry: SnapshotEntry, @ViewBuilder content: @escaping (WidgetSnapshot) -> Content) {
        self.entry = entry
        self.content = content
    }

    public var body: some View {
        Group {
            if let snapshot = entry.snapshot {
                content(snapshot)
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text("SWE").font(.system(size: 13, weight: .semibold))
                    Text("Open SWE once to fill this widget.")
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
public struct ThinBar: View {
    public var fraction: Double
    public var color: Color

    public init(fraction: Double, color: Color) {
        self.fraction = fraction
        self.color = color
    }

    public var body: some View {
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

public struct LimitsView: View {
    public var snapshot: WidgetSnapshot
    public var now: Date
    /// The size to draw for. Previews outside WidgetKit set it themselves;
    /// the real widget leaves it nil and the environment answers.
    public var family: WidgetFamily?
    @Environment(\.widgetFamily) private var envFamily

    public init(snapshot: WidgetSnapshot, now: Date, family: WidgetFamily? = nil) {
        self.snapshot = snapshot
        self.now = now
        self.family = family
    }

    var effectiveFamily: WidgetFamily { family ?? envFamily }

    /// One limit line's height: the 11pt label line, the 3pt gap to it
    /// and the 3pt bar.
    static let rowHeight: CGFloat = 20
    /// The tightest spacing two rows can take.
    static func minGap(compact: Bool) -> CGFloat { compact ? 3 : 4 }
    /// The most a gap spreads to before leftover room sits at the bottom.
    static func maxGap(compact: Bool) -> CGFloat { compact ? 6 : 8 }

    /// The rows `height` holds at the minimum gap, so a widget just short
    /// of the roomy layout still fits its fifth (or nth) row.
    static func rows(for height: CGFloat, compact: Bool) -> Int {
        let gap = minGap(compact: compact)
        return max(1, Int((height + gap) / (rowHeight + gap)))
    }

    /// The spacing `count` rows spread over `height`: the minimum when it
    /// is tight, up to the maximum when there is room to spare.
    static func rowSpacing(for height: CGFloat, count: Int, compact: Bool) -> CGFloat {
        guard count > 1 else { return 0 }
        return min(maxGap(compact: compact),
                   (height - CGFloat(count) * rowHeight) / CGFloat(count - 1))
    }

    public var body: some View {
        let compact = effectiveFamily == .systemSmall
        GeometryReader { geo in
            let limits = snapshot.limits.prefix(Self.rows(for: geo.size.height, compact: compact))
            VStack(alignment: .leading,
                   spacing: Self.rowSpacing(for: geo.size.height, count: limits.count, compact: compact)) {
                if limits.isEmpty {
                    Text("No limits yet").font(.system(size: 12, weight: .medium))
                    Text("Add an account in SWE, or star a harness in swe.")
                        .font(.system(size: 11)).foregroundStyle(Palette.textSecondary)
                }
                ForEach(Array(limits), id: \.id) { reading in
                    LimitLine(reading: reading, now: now, compact: compact,
                              old: reading.isOld(now: now, fallback: snapshot.generatedAt))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
    }
}

private struct LimitLine: View {
    var reading: LimitReading
    var now: Date
    var compact: Bool
    /// Stale, or not refreshed lately (SWE isn't running).
    var old: Bool

    var body: some View {
        // Old readings keep their last figure, dimmed and dated; only a
        // window whose reset time has passed says "reset".
        let reset = reading.hasReset(now: now)
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 5) {
                HarnessLogo(harness: reading.harness, size: 12)
                Text(reading.window.isEmpty ? reading.title : "\(reading.title) · \(UsageFormat.window(reading.window))")
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

public struct SummaryView: View {
    public var snapshot: WidgetSnapshot
    public var now: Date

    public init(snapshot: WidgetSnapshot, now: Date) {
        self.snapshot = snapshot
        self.now = now
    }

    public var body: some View {
        let points = snapshot.sparkline(days: 14, now: now, calendar: .current)
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 24) {
                FigureBlock(title: "Today", figure: snapshot.today(at: now, calendar: .current))
                FigureBlock(title: "7 days", figure: snapshot.summary.week)
                Spacer(minLength: 0)
                if snapshot.age(at: now) > 3_600 {
                    Text("Updated \(UsageFormat.ago(snapshot.generatedAt, now: now))")
                        .font(.system(size: 10)).foregroundStyle(Palette.textSecondary)
                }
            }
            Chart {
                if snapshot.summary.showsTotal {
                    ForEach(Array(points.enumerated()), id: \.offset) { point in
                        AreaMark(x: .value("Day", point.offset), y: .value("Tokens", point.element),
                                 series: .value("Series", "Total"))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(LinearGradient(colors: [Palette.accentFill.opacity(0.35), .clear],
                                                            startPoint: .top, endPoint: .bottom))
                        LineMark(x: .value("Day", point.offset), y: .value("Tokens", point.element),
                                 series: .value("Series", "Total"))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(Palette.accentFill)
                            .lineStyle(StrokeStyle(lineWidth: 2))
                    }
                }
                ForEach(snapshot.summary.series) { series in
                    let points = snapshot.sparkline(series, days: 14, now: now, calendar: .current)
                    ForEach(Array(points.enumerated()), id: \.offset) { point in
                        LineMark(x: .value("Day", point.offset), y: .value("Tokens", point.element),
                                 series: .value("Series", series.harness))
                            .interpolationMethod(.monotone)
                            .foregroundStyle(series.colorHex.isEmpty
                                             ? Palette.accent : Color(nsColor: NSColor(hex: series.colorHex)))
                            .lineStyle(StrokeStyle(lineWidth: 1.5))
                    }
                }
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

public struct HeatmapView: View {
    public var snapshot: WidgetSnapshot
    public var now: Date

    public init(snapshot: WidgetSnapshot, now: Date) {
        self.snapshot = snapshot
        self.now = now
    }

    public var body: some View {
        GeometryReader { geo in
            let gap: CGFloat = 2
            let caption: CGFloat = 22
            let cell = min((geo.size.height - caption - 6 * gap) / 7, 10)
            let weeks = min(WidgetSnapshot.heatmapWeeks, Int((geo.size.width + gap) / (cell + gap)))
            let map = snapshot.heatmap(weeks: max(weeks, 1), now: now, calendar: .current)
            VStack(alignment: .leading, spacing: 0) {
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
                Spacer(minLength: 0)
                // Totals of the weeks drawn, which a narrow widget cuts short.
                HStack(spacing: 4) {
                    Text(UsageFormat.tokens(map.totalTokens)).font(.system(size: 11, weight: .semibold))
                    Text("tokens · \(map.activeDays) active days")
                        .font(.system(size: 11)).foregroundStyle(Palette.textSecondary)
                }
            }
        }
    }
}

// MARK: - Top models

public struct ModelsView: View {
    public var snapshot: WidgetSnapshot

    public init(snapshot: WidgetSnapshot) {
        self.snapshot = snapshot
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if snapshot.models.isEmpty {
                Text("No usage in the last 30 days").font(.system(size: 12, weight: .medium))
            }
            ForEach(Array(snapshot.models.enumerated()), id: \.offset) { _, model in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 5) {
                        ModelBrandLogo(model: model.name, harnesses: model.harnesses)
                        Text(model.name).font(.system(size: 11, weight: .medium)).lineLimit(1)
                        Spacer(minLength: 4)
                        Text(UsageFormat.tokens(model.tokens))
                            .font(.system(size: 10).monospacedDigit()).foregroundStyle(Palette.textSecondary)
                        Text("\(Int((model.share * 100).rounded()))%")
                            .font(.system(size: 10).monospacedDigit()).foregroundStyle(Palette.textSecondary)
                            .frame(width: 30, alignment: .trailing)
                    }
                    // Neutral: green is kept for money and "on", and a model can
                    // run under several harnesses, so no harness colour fits.
                    ThinBar(fraction: model.share, color: Palette.textSecondary)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// A model's lab logo from `ModelBrands`, as every app page shows it
/// before a model name, and the old dot when no lab is known. A widget
/// row has room for one logo and a widget takes no hover, so the lab and
/// the tools that ran it go to VoiceOver only.
private struct ModelBrandLogo: View {
    var model: String
    var harnesses: [String]

    var body: some View {
        Group {
            if let icon = ModelBrands.forModel(model).icon, HarnessIcons.logo(for: icon) != nil {
                HarnessLogo(harness: icon, size: 12)
            } else {
                Circle().fill(Palette.textSecondary).frame(width: 6, height: 6)
                    .frame(width: 12, height: 12)
            }
        }
        // Its own element: HarnessLogo hides itself from VoiceOver.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(([ModelBrands.forModel(model).name] + harnesses.map { UsageFormat.harness($0) })
            .joined(separator: " · "))
    }
}

// MARK: - Compute

/// The Compute widget: a row per running harness with what it cost at the
/// newest trend tick, and the Mac's temperature lines below. The whole
/// thing dims and dates once the last tick ages past the widgets' stale
/// line, so a widget left up while SWE is off reads as old, not live.
public struct ComputeView: View {
    public var snapshot: WidgetSnapshot
    public var now: Date
    /// The size to draw for. Previews outside WidgetKit set it themselves;
    /// the real widget leaves it nil and the environment answers.
    public var family: WidgetFamily?
    @Environment(\.widgetFamily) private var envFamily

    public init(snapshot: WidgetSnapshot, now: Date, family: WidgetFamily? = nil) {
        self.snapshot = snapshot
        self.now = now
        self.family = family
    }

    var effectiveFamily: WidgetFamily { family ?? envFamily }

    /// A row's height: the medium's single line, the small's name over
    /// its figures.
    static func rowHeight(compact: Bool) -> CGFloat { compact ? 27 : 14 }
    /// The tightest spacing two rows can take.
    static func minGap(compact: Bool) -> CGFloat { compact ? 5 : 3 }

    /// What the top and bottom of the widget reserve: the medium pays a
    /// header and a footer line, the small pays only its two-line footer.
    static func labelHeight(compact: Bool) -> CGFloat { compact ? 24 : 28 }

    /// The rows `height` holds under the header and footer lines.
    static func rows(for height: CGFloat, compact: Bool) -> Int {
        let gap = minGap(compact: compact)
        let room = height - labelHeight(compact: compact) - gap * 2
        return max(1, Int((room + gap) / (rowHeight(compact: compact) + gap)))
    }

    /// The spacing `count` rows spread over `height`, like `LimitsView`'s:
    /// tight when it must be, up to 8 when there is room to spare.
    static func rowSpacing(for height: CGFloat, count: Int, compact: Bool) -> CGFloat {
        guard count > 1 else { return 0 }
        let reserved = labelHeight(compact: compact) + minGap(compact: compact) * 2
        return min(8, (height - reserved - CGFloat(count) * rowHeight(compact: compact))
                   / CGFloat(count - 1))
    }

    /// The temperatures the user charts, newest reading per component,
    /// in the components' fixed order.
    static func temperatures(_ section: WidgetSnapshot.ComputeSection) -> [(component: ThermalComponent, celsius: Double)] {
        ThermalComponent.allCases.compactMap { component in
            section.temperatures[component.rawValue].map { (component, $0) }
        }
    }

    public var body: some View {
        let compact = effectiveFamily == .systemSmall
        let compute = snapshot.compute
        let old = compute?.isOld(now: now) ?? false
        GeometryReader { geo in
            VStack(alignment: .leading, spacing: 0) {
                if let compute {
                    content(compute, height: geo.size.height, compact: compact)
                } else {
                    note("SWE has not written compute figures yet.")
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .opacity(old ? 0.5 : 1)
        }
    }

    /// The rows over the temperature line, or the reason there are none.
    @ViewBuilder
    private func content(_ compute: WidgetSnapshot.ComputeSection,
                         height: CGFloat, compact: Bool) -> some View {
        let rows = compute.harnesses
        let temps = Self.temperatures(compute)
        if rows.isEmpty {
            note(compute.sampledAt == nil
                 ? "Collecting, the first points arrive shortly."
                 : "Nothing a starred harness runs is up right now.")
            Spacer(minLength: 0)
            footer(compute, temps: temps, compact: compact)
        } else {
            let hasGPU = !compact && rows.contains { $0.gpuPercent != nil }
            let count = min(rows.count, Self.rows(for: height, compact: compact))
            if !compact { ComputeColumns(hasGPU: hasGPU) }
            VStack(alignment: .leading,
                   spacing: Self.rowSpacing(for: height, count: count, compact: compact)) {
                ForEach(rows.prefix(count)) { row in
                    if compact {
                        ComputeLineCompact(row: row)
                    } else {
                        ComputeLine(row: row, hasGPU: hasGPU)
                    }
                }
            }
            Spacer(minLength: 2)
            footer(compute, temps: temps, compact: compact)
        }
    }

    /// The temperature lines plus the figures' age. The age always shows,
    /// so a frozen timeline can never pass an old sample off as live.
    @ViewBuilder
    private func footer(_ compute: WidgetSnapshot.ComputeSection,
                        temps: [(component: ThermalComponent, celsius: Double)],
                        compact: Bool) -> some View {
        if !temps.isEmpty || compute.sampledAt != nil {
            // The small's footer fits about two temperatures; past that
            // it counts the rest rather than cut a sensor name in half.
            let shown = compact ? temps.prefix(2) : temps.prefix(temps.count)
            let tempsText = shown.map { "\($0.component.title) \(Int($0.celsius.rounded())) °C" }
                .joined(separator: " · ")
                + (shown.count < temps.count ? " · +\(temps.count - shown.count)" : "")
            let age = compute.sampledAt.map { "as of \(UsageFormat.ago($0, now: now))" }
            Group {
                if compact {
                    // The small has no room for temps and age side by
                    // side, so they stack and the temps get a full line.
                    VStack(alignment: .leading, spacing: 1) {
                        if !temps.isEmpty { Text(tempsText).lineLimit(1) }
                        if let age { Text(age).foregroundStyle(Palette.textSecondary) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    HStack(spacing: 4) {
                        if !temps.isEmpty { Text(tempsText).lineLimit(1) }
                        Spacer(minLength: 0)
                        if let age { Text(age).foregroundStyle(Palette.textSecondary) }
                    }
                }
            }
            .font(.system(size: 10).monospacedDigit())
            .frame(height: compact ? 24 : 14, alignment: .center)
        }
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11)).foregroundStyle(Palette.textSecondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

/// The column labels over a compute row's figures: RAM always, GPU only
/// when a row read it and the widget is not the small one.
private struct ComputeColumns: View {
    var hasGPU: Bool

    var body: some View {
        HStack(spacing: 5) {
            Spacer(minLength: 0)
            Group {
                Text("RAM").frame(width: ComputeLine.bytesWidth, alignment: .trailing)
                Text("CPU").frame(width: ComputeLine.percentWidth, alignment: .trailing)
                if hasGPU { Text("GPU").frame(width: ComputeLine.percentWidth, alignment: .trailing) }
            }
            .font(.system(size: 9)).foregroundStyle(Palette.textSecondary)
        }
        .frame(height: 14, alignment: .center)
    }
}

/// One running harness: its logo and name, then its share of the Mac.
private struct ComputeLine: View {
    var row: WidgetSnapshot.ComputeSection.Row
    var hasGPU: Bool

    /// "14.21 GB" fits; "CPU 102%" the same.
    static let bytesWidth: CGFloat = 56
    static let percentWidth: CGFloat = 38

    var body: some View {
        HStack(spacing: 5) {
            HarnessLogo(harness: row.harness, size: 12)
            Text(UsageFormat.harness(row.harness))
                .font(.system(size: 11, weight: .medium)).lineLimit(1)
            Spacer(minLength: 4)
            Group {
                Text(ComputeSummary.bytes(row.memoryBytes))
                    .frame(width: Self.bytesWidth, alignment: .trailing)
                Text("\(ComputeSummary.percent(row.cpuPercent))%")
                    .frame(width: Self.percentWidth, alignment: .trailing)
                if hasGPU {
                    Text(row.gpuPercent.map { "\(ComputeSummary.percent($0))%" } ?? "-")
                        .frame(width: Self.percentWidth, alignment: .trailing)
                }
            }
            .font(.system(size: 11).monospacedDigit())
            .widgetAccentable()
        }
        .frame(height: ComputeView.rowHeight(compact: false))
    }
}

/// The small widget's row: the name on its own line, the figures under
/// it where they cannot squeeze the name out.
private struct ComputeLineCompact: View {
    var row: WidgetSnapshot.ComputeSection.Row

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 5) {
                HarnessLogo(harness: row.harness, size: 12)
                Text(UsageFormat.harness(row.harness))
                    .font(.system(size: 11, weight: .medium)).lineLimit(1)
            }
            // The GB unit marks RAM; the percents keep the medium's
            // column order, CPU then GPU.
            Text("\(ComputeSummary.bytes(row.memoryBytes)) · \(ComputeSummary.percent(row.cpuPercent))%"
                 + (row.gpuPercent.map { " · \(ComputeSummary.percent($0))%" } ?? ""))
                .font(.system(size: 10).monospacedDigit())
                .foregroundStyle(Palette.textSecondary)
                .lineLimit(1)
                .padding(.leading, 17)
        }
        .frame(height: ComputeView.rowHeight(compact: true), alignment: .top)
    }
}

// MARK: - Preview data

extension WidgetSnapshot {
    /// Shown in the widget gallery before the app has written a snapshot.
    public static var preview: WidgetSnapshot {
        let calendar = Calendar.current
        let now = Date.now
        let today = calendar.startOfDay(for: now)
        var activity: [DailyUsage] = []
        var models: [DailyUsage] = []
        for back in 0..<120 where back % 3 != 1 {
            guard let day = calendar.date(byAdding: .day, value: -back, to: today) else { continue }
            var totals = UsageTotals()
            totals.inputTokens = 2_000_000 + (back * 7_919) % 9_000_000
            totals.costUSD = Double(totals.inputTokens) / 1_000_000 * 1.8
            let row = DailyUsage(day: day, harness: "claude", model: "claude-opus-5-5", totals: totals)
            activity.append(row)
            if back < 30 { models.append(row) }
        }
        func modelRow(_ name: String, _ tokens: Int, harness: String) -> DailyUsage {
            var totals = UsageTotals()
            totals.inputTokens = tokens
            return DailyUsage(day: today, harness: harness, model: name, totals: totals)
        }
        models += [
            modelRow("claude-opus-5-5", 586_400_000 - 2_000_000, harness: "claude"),
            modelRow("claude-sonnet-5-5", 218_700_000, harness: "claude"),
            modelRow("gpt-5.4", 80_600_000, harness: "codex"),
            modelRow("composer-2", 52_100_000, harness: "cursor"),
        ]
        let limits = [
            LimitReading(source: "claude-default", harness: "claude", title: "Claude: Default", window: "7d",
                         usedPercent: 61, resetsAt: now + 86_400, fetchedAt: now, isStale: false),
            LimitReading(source: "claude-default", harness: "claude", title: "Claude: Default", window: "5h",
                         usedPercent: 4, resetsAt: now + 16_000, fetchedAt: now, isStale: false),
            LimitReading(source: "codex-default", harness: "codex", title: "Codex: Default", window: "7d",
                         usedPercent: 32, resetsAt: now + 90_000, fetchedAt: now, isStale: false),
        ]
        return make(activity: activity, models: models, limits: limits,
                    compute: WidgetSnapshot.ComputeSection(
                        sampledAt: now,
                        harnesses: [
                            .init(harness: "claude", memoryBytes: 13_400_000_000,
                                  cpuPercent: 87, gpuPercent: 34),
                            .init(harness: "codex", memoryBytes: 3_100_000_000,
                                  cpuPercent: 12, gpuPercent: 4),
                            .init(harness: "cursor", memoryBytes: 1_900_000_000,
                                  cpuPercent: 4, gpuPercent: nil),
                        ],
                        temperatures: ["cpu": 61, "gpu": 58, "ssd": 34, "battery": 33]),
                    selection: WidgetSelection(), now: now, calendar: calendar)
    }
}
