import Foundation

/// Everything the widgets show, written by the app and read by the widget
/// extension. Usage figures and limit readings only: never a credential,
/// email or session detail.
///
/// Schema 2 gives each widget its own section, so the Desktop widgets
/// tab's switches change one widget without touching another's figures.
public struct WidgetSnapshot: Codable, Sendable, Equatable {
    public static let currentSchema = 2

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

        public init(day: Date, tokens: Int) {
            self.day = day
            self.tokens = tokens
        }
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

    /// What the Usage summary widget draws, from its own harnesses.
    public struct SummarySection: Codable, Sendable, Equatable {
        public var today: Figure
        /// The last seven days, today included.
        public var week: Figure
        /// Days with usage in the heatmap's span, oldest first; the
        /// sparkline reads its tail.
        public var days: [Day]

        public init(today: Figure, week: Figure, days: [Day]) {
            self.today = today
            self.week = week
            self.days = days
        }
    }

    /// What the Activity heatmap widget draws, from its own harnesses.
    public struct HeatmapSection: Codable, Sendable, Equatable {
        /// Days with usage in the heatmap's span, oldest first.
        public var days: [Day]
        public var activeDays: Int
        public var activityTokens: Int

        public init(days: [Day], activeDays: Int, activityTokens: Int) {
            self.days = days
            self.activeDays = activeDays
            self.activityTokens = activityTokens
        }
    }

    public var schema: Int
    public var generatedAt: Date
    public var summary: SummarySection
    public var heatmap: HeatmapSection
    /// The last 30 days' heaviest models, largest first, from the models
    /// widget's own harnesses.
    public var models: [Model]
    /// Most used first, current ones before stale ones, from the limits
    /// widget's own sources and windows.
    public var limits: [LimitReading]

    /// The heatmap's span: 26 weeks, as on the Overview page.
    public static let heatmapWeeks = 26
    static let modelCount = 4
    static let limitCount = 6

    /// `activity` covers the heatmap's half-year span, `models` the last
    /// 30 days; each is filtered down to the harnesses its widget counts.
    /// `limits` drops the sources and windows the limits selection hides.
    public static func make(activity: [DailyUsage], models: [DailyUsage], limits: [LimitReading],
                            selection: WidgetSelection = WidgetSelection(),
                            now: Date, calendar: Calendar) -> WidgetSnapshot {
        let today = calendar.startOfDay(for: now)
        let weekStart = calendar.date(byAdding: .day, value: -6, to: today) ?? today
        let spanStart = calendar.date(byAdding: .day, value: -7 * heatmapWeeks, to: today) ?? today

        let summaryRows = selection.filter(activity, for: .summary)
        let heatmapRows = selection.filter(activity, for: .heatmap)
        let modelRows = selection.filter(models, for: .models)

        func figure(from start: Date, in rows: [DailyUsage]) -> Figure {
            rows.filter { $0.day >= start && $0.day <= now }.reduce(Figure(tokens: 0, costUSD: 0)) {
                Figure(tokens: $0.tokens + $1.totals.totalTokens, costUSD: $0.costUSD + $1.totals.costUSD)
            }
        }
        /// Token totals per day over the span, quiet days skipped.
        func days(in rows: [DailyUsage]) -> [Day] {
            var byDay: [Date: Int] = [:]
            for row in rows where row.day >= spanStart && row.totals.totalTokens > 0 {
                byDay[row.day, default: 0] += row.totals.totalTokens
            }
            return byDay.sorted { $0.key < $1.key }.map { Day(day: $0.key, tokens: $0.value) }
        }

        let summaryDays = days(in: summaryRows)
        let heatmapDays = days(in: heatmapRows)
        return WidgetSnapshot(
            schema: currentSchema, generatedAt: now,
            summary: SummarySection(today: figure(from: today, in: summaryRows),
                                    week: figure(from: weekStart, in: summaryRows),
                                    days: summaryDays),
            heatmap: HeatmapSection(days: heatmapDays, activeDays: heatmapDays.count,
                                    activityTokens: heatmapDays.reduce(0) { $0 + $1.tokens }),
            models: OverviewModel.models(modelRows).prefix(modelCount)
                .map { Model(name: $0.model, tokens: $0.totals.totalTokens, share: $0.share) },
            limits: Array(LimitReading.byPressure(selection.limits.filter(limits), now: now).prefix(limitCount)))
    }

    /// Today's figure, or nothing when the snapshot was written on another day.
    public func today(at now: Date, calendar: Calendar) -> Figure {
        calendar.isDate(generatedAt, inSameDayAs: now) ? summary.today : Figure(tokens: 0, costUSD: 0)
    }

    /// How old the snapshot is: the app writes it only while it runs.
    public func age(at now: Date) -> TimeInterval {
        now.timeIntervalSince(generatedAt)
    }

    /// Tokens per day for the last `days` days, oldest first, quiet days as 0.
    public func sparkline(days count: Int, now: Date, calendar: Calendar) -> [Int] {
        let today = calendar.startOfDay(for: now)
        var byDay: [Date: Int] = [:]
        for row in summary.days { byDay[calendar.startOfDay(for: row.day), default: 0] += row.tokens }
        return (0..<count).reversed().map { back in
            calendar.date(byAdding: .day, value: -back, to: today).map { byDay[$0] ?? 0 } ?? 0
        }
    }

    public func heatmap(weeks: Int, now: Date, calendar: Calendar) -> Heatmap {
        let rows = heatmap.days.map { day -> DayRow in
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
