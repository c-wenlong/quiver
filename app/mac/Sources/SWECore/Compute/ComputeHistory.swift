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

/// The Mac's temperatures at one tick, for the Temperature tab: one
/// value per component that answered.
public struct TemperaturePoint: Sendable, Equatable {
    public var at: Date
    public var values: [ThermalComponent: Double]
    /// Nil on a Mac without fans.
    public var fans: FanSummary?

    public init(at: Date, values: [ThermalComponent: Double], fans: FanSummary? = nil) {
        self.at = at
        self.values = values
        self.fans = fans
    }

    public subscript(_ component: ThermalComponent) -> Double? { values[component] }
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
    /// The whole Mac's temperatures, one point per tick that read them.
    public private(set) var temperatures: [TemperaturePoint] = []
    /// The newest tick, whether or not any harness was running at it.
    public private(set) var lastTick: Date?

    public init() {}

    /// Notes a tick's time. A tick with nothing running adds no point,
    /// but still marks the earlier readings as no longer current.
    public mutating func recordTick(_ at: Date) {
        lastTick = max(lastTick ?? at, at)
    }

    public mutating func append(_ harness: String, _ point: TrendPoint) {
        recordTick(point.at)
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

    /// Same ring rules as a harness's points: one point per `at`, the
    /// oldest dropped past `capacity`.
    public mutating func appendTemperature(_ point: TemperaturePoint) {
        recordTick(point.at)
        if temperatures.last?.at == point.at {
            temperatures[temperatures.count - 1] = point
        } else {
            temperatures.append(point)
        }
        if temperatures.count > Self.capacity {
            temperatures.removeFirst(temperatures.count - Self.capacity)
        }
    }

    /// Where the temperature chart's x range ends: the newest tick, read
    /// or not, so readings age out even once the sensors stop answering.
    public var temperatureEnd: Date? { lastTick ?? temperatures.last?.at }

    /// The temperature points in the `window` intervals ending at
    /// `temperatureEnd`, oldest first.
    public func temperatureWindow(interval: TrendInterval) -> [TemperaturePoint] {
        guard let end = temperatureEnd else { return [] }
        let range = window(endingAt: end, interval: interval)
        return temperatures.filter { range.contains($0.at) }
    }

    /// The stretches of time the fans ran up, for the chart's shading.
    /// Each up point reaches halfway to its neighbours, using the spacing
    /// the points were actually recorded at, so changing the interval
    /// picker never re-spaces old readings. A down point between two up
    /// ones keeps them apart, and no point reaches more than
    /// `maxReach` toward a neighbour, so a gap (the Mac asleep) is not
    /// shaded as up. A point with no neighbours spans `interval`.
    public static func fanSpans(_ points: [TemperaturePoint], interval: TrendInterval) -> [ClosedRange<Date>] {
        let sorted = points.sorted { $0.at < $1.at }
        var spans: [ClosedRange<Date>] = []
        for (index, point) in sorted.enumerated() where point.fans?.up == true {
            let before = index > 0 ? point.at.timeIntervalSince(sorted[index - 1].at) / 2 : nil
            let after = index + 1 < sorted.count ? sorted[index + 1].at.timeIntervalSince(point.at) / 2 : nil
            let left = min(before ?? after ?? interval.seconds / 2, maxReach)
            let right = min(after ?? before ?? interval.seconds / 2, maxReach)
            let span = point.at.addingTimeInterval(-left)...point.at.addingTimeInterval(right)
            if let last = spans.last, span.lowerBound <= last.upperBound {
                spans[spans.count - 1] = last.lowerBound...max(last.upperBound, span.upperBound)
            } else {
                spans.append(span)
            }
        }
        return spans
    }

    /// Half the longest interval: the furthest one reading speaks for.
    static let maxReach = TrendInterval.fiveMinutes.seconds / 2

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

/// The fixed top of a trend chart's y axis. Only the plotted values
/// feed it, so hover furniture can never move the scale.
public enum TrendScale {
    /// The biggest value rounded up to the next whole number of steps,
    /// at least `minimum`. A value sitting exactly on a step still gets
    /// the step above it, so the line's end icon is never clipped.
    public static func top(_ values: [Double], step: Double, floor minimum: Double) -> Double {
        let highest = values.max() ?? 0
        let rounded = (highest / step).rounded(.down) + 1
        return max(rounded * step, minimum)
    }
}
