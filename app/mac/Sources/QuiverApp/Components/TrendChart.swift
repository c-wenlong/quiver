import Charts
import QuiverCore
import SwiftUI

/// One metric's trend lines: a line per harness in the harness's colour,
/// its icon riding the line's right end, over the window ComputeModel's
/// trend sampler has filled. The Compute page shows one at a time behind
/// tabs; the popup's Compute tab stacks them. Charts that share a
/// `hovered` binding draw the same hover rule, and the readout bubble
/// shows only on the chart under the pointer.
struct TrendChart: View {
    @Environment(AppModel.self) private var app
    var metric: TrendMetric
    /// The harnesses to draw, in the order the readout lists them.
    var harnesses: [String]
    var height: CGFloat = 180
    var showsXAxis = true
    /// The RAM chart's dashed flag line and its label.
    var showsFlag = true
    @Binding var hovered: Date?
    @State private var pointerInside = false
    @State private var bubbleSize: CGSize = .zero

    private var model: ComputeModel { app.compute }
    /// The popup's stacked size: fewer ticks, smaller icons.
    private var compact: Bool { height < 120 }

    /// The one x range every series is clipped to: `window` intervals
    /// ending at the newest recorded point, so a stopped harness's line
    /// slides off the left edge instead of holding it open.
    static func window(_ model: ComputeModel) -> ClosedRange<Date>? {
        guard let end = model.history.latest else { return nil }
        return model.history.window(endingAt: end, interval: model.trendInterval)
    }

    /// The harness's points in the visible window that have a reading.
    private func series(_ harness: String) -> [TrendPoint] {
        guard let range = Self.window(model) else { return [] }
        return model.history.window(for: harness, in: range).filter(metric.reads)
    }

    /// The y axis's fixed top, from the plotted values only. Nothing the
    /// hover state touches feeds it, so the scale cannot move under the
    /// cursor.
    private var yTop: Double {
        metric.top(harnesses.flatMap { series($0).map(metric.value(of:)) },
                   flagBytes: model.limits.memoryBytes)
    }

    static func formatted(_ value: Double, _ metric: TrendMetric) -> String {
        switch metric {
        case .ram: ComputeSummary.bytes(UInt64(max(0, value) * Double(UInt64(1) << 30)))
        case .cpu, .gpu: "\(ComputePage.cpu(value))%"
        case .temperature: TemperatureChart.formatted(value)
        }
    }

    private func colour(of harness: String) -> Color {
        Color(rgb: HarnessColor.of(harness))
    }

    var body: some View {
        let limitGB = Double(model.limits.memoryBytes) / Double(UInt64(1) << 30)
        // The chart only renders once a point exists, so latest is set.
        let domain = Self.window(model) ?? Date.now.addingTimeInterval(-60)...Date.now
        Chart {
            ForEach(harnesses, id: \.self) { harness in
                let points = series(harness)
                ForEach(points, id: \.at) { point in
                    LineMark(x: .value("Time", point.at), y: .value("Value", metric.value(of: point)),
                             series: .value("Harness", harness))
                        .foregroundStyle(colour(of: harness))
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
                if let last = points.last {
                    PointMark(x: .value("Time", last.at), y: .value("Value", metric.value(of: last)))
                        .foregroundStyle(colour(of: harness))
                        .symbolSize(16)
                        .annotation(position: .trailing, alignment: .center,
                                    overflowResolution: .init(x: .disabled, y: .disabled)) {
                            HarnessIcon(harness: harness, size: compact ? 12 : 16)
                        }
                }
            }
            if metric == .ram && showsFlag {
                RuleMark(y: .value("Flag", limitGB))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4]))
                    .foregroundStyle(Theme.warning)
                    .annotation(position: .top, alignment: .leading, spacing: 4) {
                        Text("Flag above \(ComputeSummary.bytes(model.limits.memoryBytes))")
                            .font(Theme.caption).foregroundStyle(Theme.warning)
                    }
            }
            // An x-only rule cannot touch the y domain, and the bubble
            // lives in the overlay where no chart mark or annotation can
            // grow the scale.
            if let hovered {
                RuleMark(x: .value("Time", hovered))
                    .foregroundStyle(Theme.border)
            }
        }
        .chartXScale(domain: domain)
        .chartYScale(domain: 0...yTop)
        .chartXAxis {
            if showsXAxis {
                AxisMarks(values: .automatic(desiredCount: compact ? 4 : 6)) { value in
                    AxisGridLine()
                    AxisTick()
                    if ChartTimeAxis.labels(value.as(Date.self), in: domain, compact: compact) { AxisValueLabel() }
                }
            }
        }
        // A compact chart puts its scale on the left, so the icons at the
        // lines' right ends have the trailing edge to themselves.
        .chartYAxis {
            AxisMarks(position: compact ? .leading : .automatic,
                      values: .automatic(desiredCount: compact ? 3 : 5)) { value in
                AxisGridLine()
                AxisValueLabel { axisLabel(value) }
            }
        }
        .padding(.trailing, compact ? 16 : 22)
        .frame(height: height)
        .chartOverlay { proxy in hoverOverlay(proxy) }
    }

    private func axisLabel(_ value: AxisValue) -> some View {
        let number = value.as(Double.self) ?? 0
        if metric == .ram {
            return Text("\(number.formatted(.number.precision(.fractionLength(compact ? 0 : 1)))) GB")
        }
        return Text("\(Int(number.rounded()))%")
    }

    private func hoverOverlay(_ proxy: ChartProxy) -> some View {
        GeometryReader { geometry in
            Rectangle().fill(.clear).contentShape(.rect)
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let point):
                        guard let plot = proxy.plotFrame else { return }
                        let x = point.x - geometry[plot].origin.x
                        pointerInside = true
                        hovered = proxy.value(atX: x, as: Date.self)
                    case .ended:
                        pointerInside = false
                        hovered = nil
                    }
                }
            // The readout rides the overlay: positioned against the plot
            // and clamped inside it, never a mark the scales can see.
            if pointerInside, let hovered, let plot = proxy.plotFrame,
               let x = proxy.position(forX: hovered) {
                bubble(at: hovered)
                    .fixedSize()
                    .onGeometryChange(for: CGSize.self) { $0.size } action: { bubbleSize = $0 }
                    .position(bubbleOrigin(atX: x, plot: geometry[plot]))
                    .allowsHitTesting(false)
            }
        }
    }

    /// The bubble's center at the hovered x, just under the plot's top
    /// edge, slid sideways until it clears both edges of the plot.
    private func bubbleOrigin(atX x: CGFloat, plot: CGRect) -> CGPoint {
        let half = bubbleSize.width / 2
        let px = plot.origin.x + x
        let lo = plot.minX + half + 4, hi = plot.maxX - half - 4
        let cx = lo <= hi ? min(max(px, lo), hi) : plot.midX
        return CGPoint(x: cx, y: plot.minY + bubbleSize.height / 2 + 4)
    }

    /// Each harness's value at the point nearest the hovered time.
    private func bubble(at time: Date) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(harnesses, id: \.self) { harness in
                if let point = series(harness).min(by: {
                    abs($0.at.timeIntervalSince(time)) < abs($1.at.timeIntervalSince(time))
                }) {
                    HStack(spacing: 6) {
                        HarnessIcon(harness: harness, size: 12)
                        Text(UsageFormat.harness(harness))
                        Spacer()
                        Text(Self.formatted(metric.value(of: point), metric)).monospacedDigit()
                    }
                }
            }
        }
        .font(Theme.caption).foregroundStyle(Theme.textPrimary)
        .padding(8)
        .background(Theme.panel, in: .rect(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
        .fixedSize()
    }
}

/// The time axis under Compute's trend and temperature charts. Each time
/// label starts at its tick and runs right, so a tick near the window's
/// newest edge had its label cut at the plot's trailing edge, down to its
/// first character ("2" for "21:16"). A tick that close keeps its grid
/// line and drops its label.
enum ChartTimeAxis {
    /// The share of the window at the trailing edge whose ticks go
    /// unlabelled: about 50pt on the Compute page's chart. The popup's
    /// narrow chart needs a larger share for the same room.
    static func edge(compact: Bool) -> Double { compact ? 0.12 : 0.06 }

    static func labels(_ date: Date?, in domain: ClosedRange<Date>, compact: Bool = false) -> Bool {
        guard let date else { return false }
        let span = domain.upperBound.timeIntervalSince(domain.lowerBound)
        guard span > 0 else { return true }
        return domain.upperBound.timeIntervalSince(date) >= span * edge(compact: compact)
    }
}
