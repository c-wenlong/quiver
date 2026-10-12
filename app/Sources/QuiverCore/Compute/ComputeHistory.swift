import Foundation

/// How often the Compute page's trend lines take a point, and the label
/// the picker shows. Kept in UserDefaults so the choice survives restarts.
public enum TrendInterval: Int, CaseIterable, Sendable {
    case threeSeconds = 3
    case tenSeconds = 10
    case thirtySeconds = 30
    case oneMinute = 60
    case fiveMinutes = 300

    public static let defaultsKey = "computeTrendInterval"
    public static let `default`: TrendInterval = .tenSeconds

    public var seconds: TimeInterval { TimeInterval(rawValue) }

    /// The SelectMenu title.
    public var title: String {
        switch self {
        case .threeSeconds: "Every 3 s"
        case .tenSeconds: "Every 10 s"
        case .thirtySeconds: "Every 30 s"
        case .oneMinute: "Every minute"
        case .fiveMinutes: "Every 5 minutes"
        }
    }

    /// The interval as a noun phrase, for the "Collecting" message.
    public var span: String {
        switch self {
        case .threeSeconds: "3 seconds"
        case .tenSeconds: "10 seconds"
        case .thirtySeconds: "30 seconds"
        case .oneMinute: "a minute"
        case .fiveMinutes: "5 minutes"
        }
    }

    public init(defaults: UserDefaults) {
        self = TrendInterval(rawValue: defaults.integer(forKey: Self.defaultsKey)) ?? .default
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(rawValue, forKey: Self.defaultsKey)
    }
}

/// What one harness cost at one tick: memory, CPU and GPU.
public struct TrendPoint: Sendable, Equatable {
    public var at: Date
    public var memoryBytes: UInt64
    public var cpuPercent: Double
    /// Nil when the GPU counters could not be read on this Mac.
    public var gpuPercent: Double?

    public init(at: Date, memoryBytes: UInt64, cpuPercent: Double, gpuPercent: Double?) {
        self.at = at
        self.memoryBytes = memoryBytes
        self.cpuPercent = cpuPercent
        self.gpuPercent = gpuPercent
    }
}

/// The running history behind the Compute page's trend card: a ring of
/// points per harness, in memory only. A stopped harness simply gets no
/// new points, so its line slides off the left edge as time moves on and
/// leaves the chart once its points have aged out of the window.
public struct ComputeHistory: Sendable, Equatable {
    /// Points kept per harness: two hours at the 10 s default.
    public static let capacity = 720
    /// Intervals drawn per line; at 3 s that is six minutes, at 5 m ten hours.
    public static let window = 120

    public private(set) var series: [String: [TrendPoint]] = [:]

    public init() {}

    public mutating func append(_ harness: String, _ point: TrendPoint) {
        var points = series[harness] ?? []
        // Two ticks reusing one sample agree on `at`; the second replaces
        // the first so a sample makes one point, not two sharing an x.
        if points.last?.at == point.at {
            points[points.count - 1] = point
        } else {
            points.append(point)
        }
        if points.count > Self.capacity {
            points.removeFirst(points.count - Self.capacity)
        }
        series[harness] = points
    }

    /// Whether any point has been recorded yet.
    public var isEmpty: Bool {
        series.values.allSatisfy { $0.isEmpty }
    }

    public func points(for harness: String) -> [TrendPoint] {
        series[harness] ?? []
    }

    /// The newest point time anywhere in the ring: where the shared
    /// window ends, so a stopped harness's stale points slide off the
    /// left edge instead of holding the axis open.
    public var latest: Date? {
        series.values.compactMap { $0.last?.at }.max()
    }

    /// The shared x range every series is clipped to: `window` intervals
    /// ending at `now`. The chart pins its domain to exactly this.
    public func window(endingAt now: Date, interval: TrendInterval) -> ClosedRange<Date> {
        now.addingTimeInterval(-TimeInterval(Self.window) * interval.seconds)...now
    }

    /// A harness's points inside `range`, oldest first. A harness that
    /// stopped before the window opened simply has none.
    public func window(for harness: String, in range: ClosedRange<Date>) -> [TrendPoint] {
        points(for: harness).filter { range.contains($0.at) }
    }

    /// Every harness that went above `threshold` at any retained point,
    /// sorted. The same set stands on all three metric tabs, so a heavy
    /// harness is never on RAM but missing from CPU.
    public func shown(above threshold: UInt64) -> [String] {
        series.filter { $0.value.contains { $0.memoryBytes > threshold } }
            .keys.sorted()
    }
}
