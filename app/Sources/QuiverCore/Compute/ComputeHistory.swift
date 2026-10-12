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
    /// Points drawn per line; at 3 s that is six minutes, at 5 m ten hours.
    public static let window = 120

    public private(set) var series: [String: [TrendPoint]] = [:]

    public init() {}

    public mutating func append(_ harness: String, _ point: TrendPoint) {
        var points = series[harness] ?? []
        points.append(point)
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

    /// The last `window` points of a harness, oldest first.
    public func window(for harness: String) -> [TrendPoint] {
        Array(points(for: harness).suffix(Self.window))
    }

    /// Every harness that went above `threshold` at any retained point,
    /// sorted. The same set stands on all three metric tabs, so a heavy
    /// harness is never on RAM but missing from CPU.
    public func shown(above threshold: UInt64) -> [String] {
        series.filter { $0.value.contains { $0.memoryBytes > threshold } }
            .keys.sorted()
    }
}
