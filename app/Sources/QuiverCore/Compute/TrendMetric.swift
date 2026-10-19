import Foundation

/// One of the three things the trend charts plot. The raw values are the
/// Compute page's saved tab ids, so its stored choice still reads.
public enum TrendMetric: String, CaseIterable, Identifiable, Sendable {
    case ram, cpu, gpu

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .ram: "RAM"
        case .cpu: "CPU"
        case .gpu: "GPU"
        }
    }

    static let gigabyte = Double(UInt64(1) << 30)

    /// The plotted value: gigabytes for RAM, percent for CPU and GPU.
    public func value(of point: TrendPoint) -> Double {
        switch self {
        case .ram: Double(point.memoryBytes) / Self.gigabyte
        case .cpu: point.cpuPercent
        case .gpu: point.gpuPercent ?? 0
        }
    }

    /// Whether the point has a reading for this metric. GPU points from
    /// before the counters ever answered have none, and are left out so
    /// the line does not dip to zero for "no reading".
    public func reads(_ point: TrendPoint) -> Bool {
        self != .gpu || point.gpuPercent != nil
    }

    /// The y axis's fixed top for these plotted values: RAM rounds up to
    /// the next 2 GB with the flag threshold plus a step as its floor,
    /// CPU and GPU to the next 25% (GPU at least 100%).
    public func top(_ values: [Double], flagBytes: UInt64) -> Double {
        switch self {
        case .ram: TrendScale.top(values, step: 2, floor: Double(flagBytes) / Self.gigabyte + 2)
        case .cpu: TrendScale.top(values, step: 25, floor: 25)
        case .gpu: TrendScale.top(values, step: 25, floor: 100)
        }
    }
}

extension ComputeHistory {
    /// The metric summed over the given harnesses at the newest tick, or
    /// nil before any tick. A harness not running at that tick adds
    /// nothing rather than its last old value, so once every harness has
    /// stopped the total is zero. GPU is nil when no point at the tick
    /// has a GPU reading.
    public func total(of metric: TrendMetric, harnesses: [String]) -> Double? {
        guard let tick = lastTick else { return nil }
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
