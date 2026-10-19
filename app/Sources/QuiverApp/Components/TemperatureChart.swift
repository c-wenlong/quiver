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
        VStack(alignment: .leading, spacing: 10) {
            legend(available, latest: points.last)
            if shown.isEmpty {
                Text("Every line is off. Pick one above.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: height)
            } else {
                chart(points, shown: shown)
            }
        }
    }

    /// One chip per component: its colour, name and latest reading. A
    /// chip that is off keeps its name but drops its colour.
    private func legend(_ components: [ThermalComponent], latest: TemperaturePoint?) -> some View {
        HStack(spacing: 6) {
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
                .help(on ? "Hide \(component.title)" : "Show \(component.title)")
            }
        }
    }

    private func chart(_ points: [TemperaturePoint], shown: [ThermalComponent]) -> some View {
        let values = points.flatMap { point in shown.compactMap { point[$0] } }
        // The same x range as the harness charts: the whole window, so
        // the first minutes draw from the right rather than stretched.
        let end = model.history.temperatureEnd ?? .now
        let domain = model.history.window(endingAt: end, interval: model.trendInterval)
        return Chart {
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
                            Text(component.title)
                                .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                        }
                }
            }
            if let hovered {
                RuleMark(x: .value("Time", hovered))
                    .foregroundStyle(Theme.border)
            }
        }
        .chartXScale(domain: domain)
        .chartYScale(domain: Self.domain(values))
        .chartXAxis { AxisMarks(values: .automatic(desiredCount: 6)) }
        .chartYAxis {
            // The scale sits on the left, so the line names at the right
            // ends have the trailing edge to themselves.
            AxisMarks(position: .leading, values: .automatic(desiredCount: 5)) { value in
                AxisGridLine()
                AxisValueLabel { Text("\(Int((value.as(Double.self) ?? 0).rounded())) °C") }
            }
        }
        .padding(.trailing, 64)
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
        }
        .font(Theme.caption).foregroundStyle(Theme.textPrimary)
        .padding(8)
        .frame(minWidth: 120)
        .background(Theme.panel, in: .rect(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
    }
}
