import Charts
import QuiverCore
import SwiftUI

/// The whole Mac's temperatures over the trend window: the CPU (the mean
/// of the SoC die sensors) and the battery, one line each, with the same
/// hover rule and readout as the harness charts.
struct TemperatureChart: View {
    @Environment(AppModel.self) private var app
    var height: CGFloat = 180
    @Binding var hovered: Date?
    @State private var pointerInside = false
    @State private var bubbleSize: CGSize = .zero

    /// One line on the chart.
    enum Sensor: String, CaseIterable {
        case cpu = "CPU", battery = "Battery"

        func value(_ point: TemperaturePoint) -> Double? {
            switch self {
            case .cpu: point.cpu
            case .battery: point.battery
            }
        }

        var colour: Color {
            switch self {
            case .cpu: Theme.warning
            case .battery: Theme.accentFill
            }
        }
    }

    private var points: [TemperaturePoint] {
        app.compute.history.temperatureWindow(interval: app.compute.trendInterval)
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
        let values = points.flatMap { point in Sensor.allCases.compactMap { $0.value(point) } }
        // The same x range as the harness charts: the whole window, so
        // the first minutes draw from the right rather than stretched.
        let end = app.compute.history.temperatureEnd ?? .now
        let domain = app.compute.history.window(endingAt: end, interval: app.compute.trendInterval)
        Chart {
            ForEach(Sensor.allCases, id: \.self) { sensor in
                ForEach(points.filter { sensor.value($0) != nil }, id: \.at) { point in
                    LineMark(x: .value("Time", point.at), y: .value("°C", sensor.value(point) ?? 0),
                             series: .value("Sensor", sensor.rawValue))
                        .foregroundStyle(sensor.colour)
                        .interpolationMethod(.monotone)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                }
                if let last = points.last(where: { sensor.value($0) != nil }), let value = sensor.value(last) {
                    PointMark(x: .value("Time", last.at), y: .value("°C", value))
                        .foregroundStyle(sensor.colour)
                        .symbolSize(16)
                        .annotation(position: .trailing, alignment: .center, spacing: 6,
                                    overflowResolution: .init(x: .disabled, y: .disabled)) {
                            Text(sensor.rawValue)
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
        .padding(.trailing, 56)
        .frame(height: height)
        .chartOverlay { proxy in hoverOverlay(proxy, points: points) }
    }

    private func hoverOverlay(_ proxy: ChartProxy, points: [TemperaturePoint]) -> some View {
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
                bubble(nearest)
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

    private func bubble(_ point: TemperaturePoint) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Sensor.allCases, id: \.self) { sensor in
                if let value = sensor.value(point) {
                    HStack(spacing: 6) {
                        Circle().fill(sensor.colour).frame(width: 8, height: 8)
                        Text(sensor.rawValue)
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
