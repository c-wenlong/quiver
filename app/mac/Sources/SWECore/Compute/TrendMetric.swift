import Foundation

/// One of the charts the Compute page's trend card and the menu bar
/// popup's Compute tab can show. RAM, CPU and GPU plot a line per
/// harness; temperature plots the whole Mac's sensors instead, so it has
/// no per-harness value: `reads` is false for every point and `value`
/// returns a fallback nothing plots. The raw values are the Compute
/// page's saved tab ids, so its stored choice still reads.
public enum TrendMetric: String, CaseIterable, Identifiable, Sendable {
    case ram, cpu, gpu, temperature

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .ram: "RAM"
        case .cpu: "CPU"
        case .gpu: "GPU"
        case .temperature: "Temperature"
        }
    }

    static let gigabyte = Double(UInt64(1) << 30)

    /// The plotted value: gigabytes for RAM, percent for CPU and GPU.
    /// Temperature's zero is a fallback like GPU's: `reads` is false for
    /// every point, so it never reaches a line.
    public func value(of point: TrendPoint) -> Double {
        switch self {
        case .ram: Double(point.memoryBytes) / Self.gigabyte
        case .cpu: point.cpuPercent
        case .gpu: point.gpuPercent ?? 0
        case .temperature: 0
        }
    }

    /// Whether the point has a reading for this metric. GPU points from
    /// before the counters ever answered have none, and are left out so
    /// the line does not dip to zero for "no reading". A TrendPoint never
    /// holds a temperature: those readings are whole-Mac and live in
    /// `ComputeHistory.temperatures`.
    public func reads(_ point: TrendPoint) -> Bool {
        switch self {
        case .gpu: point.gpuPercent != nil
        case .temperature: false
        case .ram, .cpu: true
        }
    }

    /// The y axis's fixed top for these plotted values: RAM rounds up to
    /// the next 2 GB with the flag threshold plus a step as its floor,
    /// CPU and GPU to the next 25% (GPU at least 100%). Temperature's is
    /// never drawn: TemperatureChart computes its own domain, so a
    /// ten-degree stepped scale stands in.
    public func top(_ values: [Double], flagBytes: UInt64) -> Double {
        switch self {
        case .ram: TrendScale.top(values, step: 2, floor: Double(flagBytes) / Self.gigabyte + 2)
        case .cpu: TrendScale.top(values, step: 25, floor: 25)
        case .gpu: TrendScale.top(values, step: 25, floor: 100)
        case .temperature: TrendScale.top(values, step: 10, floor: 50)
        }
    }
}

extension ComputeHistory {
    /// The metric summed over the given harnesses at the newest tick, or
    /// nil before any tick. A harness not running at that tick adds
    /// nothing rather than its last old value, so once every harness has
    /// stopped the total is zero. GPU is nil when no point at the tick
    /// has a GPU reading. Temperature is always nil: it is whole-Mac,
    /// not a per-harness sum.
    public func total(of metric: TrendMetric, harnesses: [String]) -> Double? {
        guard metric != .temperature, let tick = lastTick else { return nil }
        let points = harnesses.compactMap { self.points(for: $0).last }.filter { $0.at == tick }
        let read = points.filter(metric.reads)
        if metric == .gpu && !points.isEmpty && read.isEmpty { return nil }
        return read.reduce(0) { $0 + metric.value(of: $1) }
    }

    /// Every harness with a point in `range`, sorted: the lines a chart
    /// over that window could draw.
    public func active(in range: ClosedRange<Date>) -> [String] {
        series.filter { $0.value.contains { range.contains($0.at) } }.keys.sorted()
    }
}
