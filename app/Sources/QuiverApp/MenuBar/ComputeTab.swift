import QuiverCore
import SwiftUI

/// The popup's Compute tab: the Compute page's trend lines, one chart per
/// metric stacked in the order chosen on the Menu bar page. The charts
/// share one hover, so the rule lines up across them.
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
        let (active, shown) = harnesses(model)
        VStack(alignment: .leading, spacing: 10) {
            if model.history.isEmpty {
                note("Collecting, the first points arrive within \(model.trendInterval.span).")
            } else if metrics.isEmpty {
                note("Every chart is hidden in Menu bar.")
            } else if active.isEmpty {
                note("Nothing a starred harness runs is up right now.")
            } else if shown.isEmpty {
                note("Every running harness is hidden in Menu bar.")
            } else {
                // One box per chart, so each reads on its own.
                ForEach(metrics) { metric in
                    // Room under the header for the axis's top label.
                    VStack(alignment: .leading, spacing: 14) {
                        HStack {
                            Text(metric.title).font(Theme.bodySmall).foregroundStyle(Theme.textPrimary)
                            Spacer()
                            if let total = model.history.total(of: metric, harnesses: shown) {
                                Text(TrendChart.formatted(total, metric))
                                    .font(Theme.bodySmall.monospacedDigit())
                                    .foregroundStyle(Theme.textPrimary)
                            }
                        }
                        if metric == .gpu && !model.gpuAvailable {
                            note("GPU time isn't available on this Mac.", height: 40)
                        } else {
                            TrendChart(metric: metric, harnesses: shown, height: Self.chartHeight,
                                       hovered: $hovered)
                        }
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

    private func note(_ text: String, height: CGFloat = 120) -> some View {
        Text(text)
            .font(Theme.caption).foregroundStyle(Theme.textSecondary)
            .frame(maxWidth: .infinity, minHeight: height)
    }
}
