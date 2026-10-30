import Charts
import QuiverCore
import SwiftUI

extension ThermalComponent {
    /// Fixed per component, never by rank, so switching a line off does
    /// not repaint the others. Slots 1 to 7 of the dataviz categorical
    /// palette, checked for colour-blind separation on the card in both
    /// themes; three sit under 3:1 on white, so every line also carries
    /// its name at its end and in the legend.
    var colour: Color {
        switch self {
        case .cpu: Color(light: "#2a78d6", dark: "#3987e5")
        case .gpu: Color(light: "#eb6834", dark: "#d95926")
        case .memory: Color(light: "#1baf7a", dark: "#199e70")
        case .ssd: Color(light: "#eda100", dark: "#c98500")
        case .battery: Color(light: "#e87ba4", dark: "#d55181")
        case .airflow: Color(light: "#008300", dark: "#008300")
        case .display: Color(light: "#6250d6", dark: "#9085e9")
        }
    }
}

/// The whole Mac's temperatures over the trend window: one line per
/// component that answered and is switched on, with the same hover rule
/// and readout as the harness charts. The legend above switches lines.
struct TemperatureChart: View {
    @Environment(AppModel.self) private var app
    var height: CGFloat = 180
    @Binding var hovered: Date?
    @State private var pointerInside = false
    @State private var bubbleSize: CGSize = .zero

    private var model: ComputeModel { app.compute }
    /// The popup's stacked size: the same trailing edge and tick counts as
    /// the compact TrendChart, so a shared hover rule lines up across the
    /// charts, and no line-end names (the legend names every line).
    private var compact: Bool { height < 120 }

    private var points: [TemperaturePoint] {
        model.history.temperatureWindow(interval: model.trendInterval)
    }

    /// Every component with a reading in the window, in palette order.
    private func available(_ points: [TemperaturePoint]) -> [ThermalComponent] {
        let present = Set(points.flatMap(\.values.keys))
        return ThermalComponent.allCases.filter(present.contains)
    }

    /// Ten-degree steps around the plotted values, at least 30 °C tall,
    /// so a steady reading is not drawn as a wild line.
    static func domain(_ values: [Double]) -> ClosedRange<Double> {
        let low = max(0, (((values.min() ?? 30) - 5) / 10).rounded(.down) * 10)
        // Five degrees of headroom keeps the hottest line off the top edge.
        return low...TrendScale.top(values.map { $0 + 5 }, step: 10, floor: low + 30)
    }

    static func formatted(_ celsius: Double) -> String {
        "\(Int(celsius.rounded())) °C"
    }

    var body: some View {
        let points = self.points
        let available = available(points)
        let shown = available.filter(model.temperatureLines.contains)
        let spans = ComputeHistory.fanSpans(points, interval: model.trendInterval)
        VStack(alignment: .leading, spacing: 10) {
            legend(available, latest: points.last, fansShown: !spans.isEmpty)
            if shown.isEmpty {
                Text("Every line is off. Pick one above.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: height)
            } else {
                chart(points, shown: shown, spans: spans)
            }
        }
    }

    /// One chip per component: its colour, name and latest reading. A
    /// chip that is off keeps its name but drops its colour. The chips
    /// wrap onto a second row rather than shrink in a narrow window.
    private func legend(_ components: [ThermalComponent], latest: TemperaturePoint?,
                        fansShown: Bool) -> some View {
        FlowLayout(spacing: 6, lineSpacing: 6) {
            ForEach(components, id: \.self) { component in
                let on = model.temperatureLines.contains(component)
                Button {
                    model.toggleTemperatureLine(component)
                } label: {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(on ? component.colour : Color.clear)
                            .overlay(Circle().strokeBorder(on ? Color.clear : Theme.borderStrong, lineWidth: 1))
                            .frame(width: 8, height: 8)
                        Text(component.title)
                            .foregroundStyle(on ? Theme.textPrimary : Theme.textSecondary)
                        if let value = latest?[component] {
                            Text(Self.formatted(value)).monospacedDigit()
                                .foregroundStyle(Theme.textSecondary)
                        }
                    }
                    .font(Theme.caption)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(on ? Theme.subtle : Color.clear, in: .capsule)
                    .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
                    .contentShape(.capsule)
                }
                .plainButton(cornerRadius: 12)
                .fixedSize()
                .help(on ? "Hide \(component.title)" : "Show \(component.title)")
            }
            // A key, not a switch: the shading explains itself here.
            if fansShown {
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 2).fill(Self.fanShade).frame(width: 12, height: 10)
                    Text("Fans up").foregroundStyle(Theme.textSecondary)
                }
                .font(Theme.caption)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .fixedSize()
                .hoverTip("Shaded where a fan ran more than 15% above its minimum speed.", replacesLabel: false)
            }
        }
    }

    /// Grey, so it reads as background and never as an eighth series.
    static let fanShade = Theme.textSecondary.opacity(0.14)

    private func chart(_ points: [TemperaturePoint], shown: [ThermalComponent],
                       spans: [ClosedRange<Date>]) -> some View {
        let values = points.flatMap { point in shown.compactMap { point[$0] } }
        // The user's fan threshold, when set: kept inside the scale so its
        // line is always on the chart.
        let threshold = app.fans.thresholdEnabled ? app.fans.threshold : nil
        let yDomain = Self.domain(values + (threshold.map { [$0] } ?? []))
        // The same x range as the harness charts: the whole window, so
        // the first minutes draw from the right rather than stretched.
        let end = model.history.temperatureEnd ?? .now
        let domain = model.history.window(endingAt: end, interval: model.trendInterval)
        return Chart {
            // First, so every line draws over the shading. Its y runs the
            // fixed scale, so it can never move the axis.
            ForEach(spans, id: \.lowerBound) { span in
                RectangleMark(xStart: .value("Start", max(span.lowerBound, domain.lowerBound)),
                              xEnd: .value("End", min(span.upperBound, domain.upperBound)),
                              yStart: .value("°C", yDomain.lowerBound), yEnd: .value("°C", yDomain.upperBound))
                    .foregroundStyle(Self.fanShade)
            }
            if let threshold {
                // Neutral, not amber: amber is the SSD line's colour.
                RuleMark(y: .value("Threshold", threshold))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [4]))
                    .foregroundStyle(Theme.textSecondary)
                    .annotation(position: .top, alignment: .leading, spacing: 4) {
                        Text("Fans up above \(Self.formatted(threshold))")
                            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    }
            }
            ForEach(shown, id: \.self) { component in
                ForEach(points.filter { $0[component] != nil }, id: \.at) { point in
                    LineMark(x: .value("Time", point.at), y: .value("°C", point[component] ?? 0),
                             series: .value("Component", component.title))
                        .foregroundStyle(component.colour)
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
                if let last = points.last(where: { $0[component] != nil }), let value = last[component] {
                    PointMark(x: .value("Time", last.at), y: .value("°C", value))
                        .foregroundStyle(component.colour)
                        .symbolSize(16)
                        .annotation(position: .trailing, alignment: .center, spacing: 6,
                                    overflowResolution: .init(x: .disabled, y: .disabled)) {
                            if !compact {
                                Text(component.title)
                                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                            }
                        }
                }
            }
            if let hovered {
                RuleMark(x: .value("Time", hovered))
                    .foregroundStyle(Theme.border)
            }
        }
        .chartXScale(domain: domain)
        .chartYScale(domain: yDomain)
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: compact ? 4 : 6)) { value in
                AxisGridLine()
                AxisTick()
                if ChartTimeAxis.labels(value.as(Date.self), in: domain, compact: compact) { AxisValueLabel() }
            }
        }
        .chartYAxis {
            // The scale sits on the left, so the line names at the right
            // ends have the trailing edge to themselves.
            AxisMarks(position: .leading, values: .automatic(desiredCount: compact ? 3 : 5)) { value in
                AxisGridLine()
                AxisValueLabel { Text("\(Int((value.as(Double.self) ?? 0).rounded())) °C") }
            }
        }
        // Compact matches TrendChart's trailing edge, so a hover rule at
        // one time sits at the same x on every stacked chart; the names
        // it makes room for are skipped at this height anyway.
        .padding(.trailing, compact ? 16 : 64)
        .frame(height: height)
        .chartOverlay { proxy in hoverOverlay(proxy, points: points, shown: shown) }
    }

    private func hoverOverlay(_ proxy: ChartProxy, points: [TemperaturePoint],
                              shown: [ThermalComponent]) -> some View {
        GeometryReader { geometry in
            Rectangle().fill(.clear).contentShape(.rect)
                .onContinuousHover { phase in
                    switch phase {
                    case .active(let point):
                        guard let plot = proxy.plotFrame else { return }
                        pointerInside = true
                        hovered = proxy.value(atX: point.x - geometry[plot].origin.x, as: Date.self)
                    case .ended:
                        pointerInside = false
                        hovered = nil
                    }
                }
            if pointerInside, let hovered, let plot = proxy.plotFrame,
               let x = proxy.position(forX: hovered),
               let nearest = points.min(by: {
                   abs($0.at.timeIntervalSince(hovered)) < abs($1.at.timeIntervalSince(hovered))
               }) {
                bubble(nearest, shown: shown)
                    .fixedSize()
                    .onGeometryChange(for: CGSize.self) { $0.size } action: { bubbleSize = $0 }
                    .position(bubbleOrigin(atX: x, plot: geometry[plot]))
                    .allowsHitTesting(false)
            }
        }
    }

    /// Centred on the hovered x under the plot's top edge, kept inside it.
    private func bubbleOrigin(atX x: CGFloat, plot: CGRect) -> CGPoint {
        let half = bubbleSize.width / 2
        let px = plot.origin.x + x
        let lo = plot.minX + half + 4, hi = plot.maxX - half - 4
        let cx = lo <= hi ? min(max(px, lo), hi) : plot.midX
        return CGPoint(x: cx, y: plot.minY + bubbleSize.height / 2 + 4)
    }

    private func bubble(_ point: TemperaturePoint, shown: [ThermalComponent]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(shown, id: \.self) { component in
                if let value = point[component] {
                    HStack(spacing: 6) {
                        Circle().fill(component.colour).frame(width: 8, height: 8)
                        Text(component.title)
                        Spacer()
                        Text(Self.formatted(value)).monospacedDigit()
                    }
                }
            }
            if let fans = point.fans {
                HStack(spacing: 6) {
                    RoundedRectangle(cornerRadius: 2).fill(fans.up ? Self.fanShade : Color.clear)
                        .overlay(RoundedRectangle(cornerRadius: 2).strokeBorder(Theme.borderStrong, lineWidth: 1))
                        .frame(width: 8, height: 8)
                    Text("Fans")
                    Spacer()
                    Text("\(Int(fans.rpm.rounded()).formatted()) rpm").monospacedDigit()
                }
                .foregroundStyle(Theme.textSecondary)
            }
        }
        .font(Theme.caption).foregroundStyle(Theme.textPrimary)
        .padding(8)
        .frame(minWidth: 120)
        .background(Theme.panel, in: .rect(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
    }
}
