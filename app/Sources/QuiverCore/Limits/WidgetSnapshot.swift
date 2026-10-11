import Foundation

/// Everything the widgets show, written by the app and read by the widget
/// extension. Usage figures and limit readings only: never a credential,
/// email or session detail.
public struct WidgetSnapshot: Codable, Sendable, Equatable {
    public static let currentSchema = 1

    public struct Figure: Codable, Sendable, Equatable {
        public var tokens: Int
        public var costUSD: Double

        public init(tokens: Int, costUSD: Double) {
            self.tokens = tokens
            self.costUSD = costUSD
        }
    }

    public struct Day: Codable, Sendable, Equatable {
        public var day: Date
        public var tokens: Int
    }

    public struct Model: Codable, Sendable, Equatable {
        public var name: String
        public var tokens: Int
        /// Of all tokens in the last 30 days, 0...1.
        public var share: Double

        public init(name: String, tokens: Int, share: Double) {
            self.name = name
            self.tokens = tokens
            self.share = share
        }
    }

    public var schema: Int
    public var generatedAt: Date
    public var today: Figure
    /// The last seven days, today included.
    public var week: Figure
    /// Days with usage in the heatmap's span, oldest first.
    public var days: [Day]
    public var activeDays: Int
    public var activityTokens: Int
    /// The last 30 days' heaviest models, largest first.
    public var models: [Model]
    /// Most used first, current ones before stale ones.
    public var limits: [LimitReading]

    /// The heatmap's span: 26 weeks, as on the Overview page.
    public static let heatmapWeeks = 26
    static let modelCount = 4
    static let limitCount = 6

    public static func make(activity: [DayRow], models: [ModelShare], limits: [LimitReading],
                            now: Date, calendar: Calendar) -> WidgetSnapshot {
        let today = calendar.startOfDay(for: now)
        let weekStart = calendar.date(byAdding: .day, value: -6, to: today) ?? today
        let spanStart = calendar.date(byAdding: .day, value: -7 * heatmapWeeks, to: today) ?? today
        func figure(from start: Date) -> Figure {
            activity.filter { $0.day >= start && $0.day <= now }.reduce(Figure(tokens: 0, costUSD: 0)) {
                Figure(tokens: $0.tokens + $1.totals.totalTokens, costUSD: $0.costUSD + $1.totals.costUSD)
            }
        }
        let days = activity
            .filter { $0.day >= spanStart && $0.totals.totalTokens > 0 }
            .sorted { $0.day < $1.day }
            .map { Day(day: $0.day, tokens: $0.totals.totalTokens) }
        return WidgetSnapshot(
            schema: currentSchema, generatedAt: now,
            today: figure(from: today), week: figure(from: weekStart),
            days: days, activeDays: days.count, activityTokens: days.reduce(0) { $0 + $1.tokens },
            models: models.prefix(modelCount).map { Model(name: $0.model, tokens: $0.totals.totalTokens, share: $0.share) },
            limits: Array(LimitReading.byPressure(limits, now: now).prefix(limitCount)))
    }

    /// Today's figure, or nothing when the snapshot was written on another day.
    public func today(at now: Date, calendar: Calendar) -> Figure {
        calendar.isDate(generatedAt, inSameDayAs: now) ? today : Figure(tokens: 0, costUSD: 0)
    }

    /// How old the snapshot is: the app writes it only while it runs.
    public func age(at now: Date) -> TimeInterval {
        now.timeIntervalSince(generatedAt)
    }

    /// Tokens per day for the last `days` days, oldest first, quiet days as 0.
    public func sparkline(days count: Int, now: Date, calendar: Calendar) -> [Int] {
        let today = calendar.startOfDay(for: now)
        var byDay: [Date: Int] = [:]
        for row in days { byDay[calendar.startOfDay(for: row.day), default: 0] += row.tokens }
        return (0..<count).reversed().map { back in
            calendar.date(byAdding: .day, value: -back, to: today).map { byDay[$0] ?? 0 } ?? 0
        }
    }

    public func heatmap(weeks: Int, now: Date, calendar: Calendar) -> Heatmap {
        let rows = days.map { day -> DayRow in
            var totals = UsageTotals()
            totals.inputTokens = day.tokens
            return DayRow(day: day.day, totals: totals)
        }
        return Heatmap(days: rows, weeks: weeks, now: now, calendar: calendar)
    }

    public static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return decoder
    }()
}

/// The snapshot file the app writes and the widget extension reads. The
/// extension is sandboxed with read access to this one folder, so it lives
/// under `~/.quiver` like everything else Quiver keeps.
public struct WidgetSnapshotStore: Sendable {
    public let file: URL

    public init(file: URL) {
        self.file = file
    }

    public static func defaultFile(home: URL = realHome()) -> URL {
        home.appending(path: ".quiver/cache/widgets/snapshot.json")
    }

    public func write(_ snapshot: WidgetSnapshot) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try WidgetSnapshot.encoder.encode(snapshot).write(to: file, options: .atomic)
    }

    /// Nil when there is no file yet, or one this build can't read.
    public func read() -> WidgetSnapshot? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        struct Header: Decodable { var schema: Int }
        guard (try? WidgetSnapshot.decoder.decode(Header.self, from: data))?.schema == WidgetSnapshot.currentSchema
        else { return nil }
        return try? WidgetSnapshot.decoder.decode(WidgetSnapshot.self, from: data)
    }
}

/// The user's real home folder. Inside a sandbox, `homeDirectoryForCurrentUser`
/// is the container; the password database still has the real one.
public func realHome() -> URL {
    if let entry = getpwuid(getuid()), let dir = entry.pointee.pw_dir {
        return URL(fileURLWithPath: String(cString: dir), isDirectory: true)
    }
    return FileManager.default.homeDirectoryForCurrentUser
}
