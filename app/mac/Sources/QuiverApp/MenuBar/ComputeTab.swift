import QuiverCore
import SwiftUI

/// The popup's Compute tab: the Compute page's trend lines plus the
/// whole-Mac temperature chart, one chart per switched-on metric stacked
/// in the order chosen on the Menu bar page. The charts share one hover,
/// so the rule lines up across them.
struct ComputeTabContent: View {
    @Environment(AppModel.self) private var app
    @State private var hovered: Date?

    private static let chartHeight: CGFloat = 96

    /// Harnesses with a point in the window, in the dragged order, less
    /// the ones switched off.
    private func harnesses(_ model: ComputeModel) -> (active: [String], shown: [String]) {
        guard let range = TrendChart.window(model) else { return ([], []) }
        let active = model.history.active(in: range)
        return (active, app.computePopup.harnesses.visible(active))
    }

    var body: some View {
        let model = app.compute
        let metrics = app.computePopup.visibleMetrics
        let temperatures = model.history.temperatureWindow(interval: model.trendInterval)
        let (active, shown) = harnesses(model)
        // Temperature needs no running harness, so once it is switched on
        // every box carries its own empty state rather than one note
        // standing in for the whole tab.
        let temperatureOn = metrics.contains(.temperature)
        VStack(alignment: .leading, spacing: 10) {
            // The user's own setting first: with every chart hidden,
            // "Collecting" would promise charts that never come.
            if metrics.isEmpty {
                note("Every chart is hidden in Menu bar.")
            } else if model.history.isEmpty && !temperatureOn {
                note("Collecting, the first points arrive within \(model.trendInterval.span).")
            } else if active.isEmpty && !temperatureOn {
                note("Nothing a starred harness runs is up right now.")
            } else if shown.isEmpty && !temperatureOn {
                note("Every running harness is hidden in Menu bar.")
            } else {
                // One box per chart, so each reads on its own.
                ForEach(metrics) { metric in
                    // Room under the header for the axis's top label.
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Text(metric.title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            Spacer()
                            if let total = total(of: metric, model: model,
                                                 temperatures: temperatures, shown: shown) {
                                Text(TrendChart.formatted(total, metric))
                                    .font(Theme.bodySmall.monospacedDigit())
                                    .foregroundStyle(Theme.textPrimary)
                            }
                        }
                        content(metric, model: model, temperatures: temperatures,
                                active: active, shown: shown)
                    }
                    .padding(12)
                    .background(Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                        .strokeBorder(Theme.border, lineWidth: 1))
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// The header's current figure: the sum over the shown harnesses for
    /// a trend metric, the hottest sensor's latest reading for
    /// temperature.
    private func total(of metric: TrendMetric, model: ComputeModel,
                       temperatures: [TemperaturePoint], shown: [String]) -> Double? {
        if metric == .temperature {
            return temperatures.last?.values.values.max()
        }
        return model.history.total(of: metric, harnesses: shown)
    }

    /// One chart's body: its lines, or the reason there are none.
    @ViewBuilder
    private func content(_ metric: TrendMetric, model: ComputeModel,
                         temperatures: [TemperaturePoint],
                         active: [String], shown: [String]) -> some View {
        if metric == .temperature {
            if !temperatures.isEmpty {
                TemperatureChart(height: Self.chartHeight, hovered: $hovered)
            } else {
                note(model.history.lastTick == nil
                     ? "Collecting, the first points arrive within \(model.trendInterval.span)."
                     : "Temperature sensors aren't available on this Mac.", height: 40)
            }
        } else if model.history.isEmpty {
            note("Collecting, the first points arrive within \(model.trendInterval.span).", height: 40)
        } else if shown.isEmpty {
            note(active.isEmpty
                 ? "Nothing a starred harness runs is up right now."
                 : "Every running harness is hidden in Menu bar.", height: 40)
        } else if metric == .gpu && !model.gpuAvailable {
            note("GPU time isn't available on this Mac.", height: 40)
        } else {
            TrendChart(metric: metric, harnesses: shown, height: Self.chartHeight,
                       hovered: $hovered)
        }
    }

    private func note(_ text: String, height: CGFloat = 120) -> some View {
        Text(text)
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .frame(maxWidth: .infinity, minHeight: height)
    }
}
