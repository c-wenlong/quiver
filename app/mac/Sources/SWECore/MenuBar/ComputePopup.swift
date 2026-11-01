import Foundation

/// What the popup's Compute tab draws: which of the RAM, CPU, GPU and
/// Temperature charts and in what order, top to bottom, and which
/// harnesses get a line. Every chart and every harness shows until
/// hidden on the Menu bar page. Unlike the Compute page's single chart,
/// harnesses are not filtered by the flag threshold: the switches
/// choose them instead.
public struct ComputePopup: Equatable, Sendable {
    public var metrics = ToggleList()
    /// Keyed by harness id ("claude", "codex").
    public var harnesses = ToggleList()

    public enum Key {
        public static let metricOrder = "popupComputeMetricOrder"
        public static let hiddenMetrics = "popupComputeHiddenMetrics"
        public static let harnessOrder = "popupComputeHarnessOrder"
        public static let hiddenHarnesses = "popupComputeHiddenHarnesses"
    }

    public init(defaults: UserDefaults) {
        metrics = ToggleList(defaults: defaults, orderKey: Key.metricOrder, hiddenKey: Key.hiddenMetrics)
        harnesses = ToggleList(defaults: defaults, orderKey: Key.harnessOrder,
                               hiddenKey: Key.hiddenHarnesses)
    }

    public func save(to defaults: UserDefaults) {
        metrics.save(to: defaults, orderKey: Key.metricOrder, hiddenKey: Key.hiddenMetrics)
        harnesses.save(to: defaults, orderKey: Key.harnessOrder, hiddenKey: Key.hiddenHarnesses)
    }

    private static let metricIDs = TrendMetric.allCases.map(\.rawValue)

    /// Every chart in the dragged order, hidden ones included.
    public var allMetrics: [TrendMetric] {
        metrics.ordered(Self.metricIDs).compactMap(TrendMetric.init(rawValue:))
    }

    /// The charts the tab stacks, top to bottom.
    public var visibleMetrics: [TrendMetric] {
        metrics.visible(Self.metricIDs).compactMap(TrendMetric.init(rawValue:))
    }

    public mutating func moveMetric(_ id: String, before target: String?) {
        if let target {
            metrics.move(id, before: target, in: Self.metricIDs)
        } else {
            metrics.moveToEnd(id, in: Self.metricIDs)
        }
    }
}
