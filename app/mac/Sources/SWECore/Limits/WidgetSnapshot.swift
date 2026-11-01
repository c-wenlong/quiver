import Foundation

/// Everything the widgets show, written by the app and read by the widget
/// extension. Usage figures, limit readings and compute numbers only:
/// never a credential, email or session detail.
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
        /// The harnesses that ran the model, most tokens first.
        public var harnesses: [String]

        public init(name: String, tokens: Int, share: Double, harnesses: [String] = []) {
            self.name = name
            self.tokens = tokens
            self.share = share
            self.harnesses = harnesses
        }

        /// `harnesses` is absent from snapshots an older build wrote.
        public init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            name = try container.decode(String.self, forKey: .name)
            tokens = try container.decode(Int.self, forKey: .tokens)
            share = try container.decode(Double.self, forKey: .share)
            harnesses = try container.decodeIfPresent([String].self, forKey: .harnesses) ?? []
        }
    }

    /// One harness's daily usage for the summary chart's own line.
    public struct HarnessSeries: Codable, Sendable, Equatable, Identifiable {
        public var id: String { harness }
        public var harness: String
        /// The trend-chart colour as "#RRGGBB", so the sandboxed widget
        /// never reads the app's logo files.
        public var colorHex: String
        /// Days with usage in the heatmap's span, oldest first.
        public var days: [Day]

        public init(harness: String, colorHex: String, days: [Day]) {
            self.harness = harness
            self.colorHex = colorHex
            self.days = days
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
        /// A line per counted harness, in the Settings order.
        public var series: [HarnessSeries]
        /// Whether the aggregate line draws on top of the series.
        public var showsTotal: Bool

        public init(today: Figure, week: Figure, days: [Day],
                    series: [HarnessSeries] = [], showsTotal: Bool = true) {
            self.today = today
            self.week = week
            self.days = days
            self.series = series
            self.showsTotal = showsTotal
        }

        /// `series` and `showsTotal` are absent from snapshots an older
        /// build wrote; they read as no series, total shown.
        public init(from decoder: Decoder) throws {
            let container = try decoder.container(keyedBy: CodingKeys.self)
            today = try container.decode(Figure.self, forKey: .today)
            week = try container.decode(Figure.self, forKey: .week)
            days = try container.decode([Day].self, forKey: .days)
            series = try container.decodeIfPresent([HarnessSeries].self, forKey: .series) ?? []
            showsTotal = try container.decodeIfPresent(Bool.self, forKey: .showsTotal) ?? true
        }
    }

    /// What the Compute widget draws: the running harnesses' cost at the
    /// newest trend tick, and the whole-Mac temperatures the user charts.
    /// Figures read as live only while the app keeps ticking; `isOld` dims
    /// the widget once the last tick ages past the widgets' stale line.
    public struct ComputeSection: Codable, Sendable, Equatable {
        /// One running harness's cost at `sampledAt`.
        public struct Row: Codable, Sendable, Equatable, Identifiable {
            public var id: String { harness }
            public var harness: String
            public var memoryBytes: UInt64
            public var cpuPercent: Double
            /// Nil where the Mac's GPU counters never answered.
            public var gpuPercent: Double?

            public init(harness: String, memoryBytes: UInt64, cpuPercent: Double,
                        gpuPercent: Double? = nil) {
                self.harness = harness
                self.memoryBytes = memoryBytes
                self.cpuPercent = cpuPercent
                self.gpuPercent = gpuPercent
            }
        }

        /// The newest trend tick the figures came from, nil before the
        /// first sample.
        public var sampledAt: Date?
        /// The harnesses with a point at `sampledAt`, in the matchers' order.
        public var harnesses: [Row]
        /// The chosen temperature lines' latest readings, keyed by
        /// `ThermalComponent` raw value, so the file stays JSON-simple.
        public var temperatures: [String: Double]

        public init(sampledAt: Date?, harnesses: [Row], temperatures: [String: Double]) {
            self.sampledAt = sampledAt
            self.harnesses = harnesses
            self.temperatures = temperatures
        }

        /// The most rows the snapshot carries; the widget fits far fewer.
        static let rowCount = 8

        /// The section for a write: each harness's newest point counts only
        /// when it sits at the newest tick, so a stopped harness drops out
        /// rather than freezing its last figure. `order` is the matchers'
        /// (swe's) order; a harness it does not name keeps its history
        /// place at the end. Temperatures count only when the newest point
        /// sits at the tick too: a tick the sensors missed leaves the last
        /// reading old, and it stays home like a stopped harness. The
        /// selection's hidden compute harnesses and unchosen temperature
        /// lines stay out of the file.
        public init(history: ComputeHistory, order: [String],
                    temperatureLines: Set<ThermalComponent>,
                    selection: WidgetSelection) {
            let tick = history.lastTick
            sampledAt = tick
            let extras = history.series.keys.filter { !order.contains($0) }.sorted()
            harnesses = Array((order + extras).compactMap { name -> Row? in
                guard selection.shows(harness: name, in: .compute),
                      let point = history.points(for: name).last, point.at == tick
                else { return nil }
                return Row(harness: name, memoryBytes: point.memoryBytes,
                           cpuPercent: point.cpuPercent, gpuPercent: point.gpuPercent)
            }.prefix(Self.rowCount))
            // The temperature point runs to the same newest-tick rule:
            // ticks where the sensors did not answer record no point, so
            // a last point behind the tick is old and stays home rather
            // than sitting beside a fresh "as of".
            let latest = history.temperatures.last
            let values = latest?.at == tick ? latest?.values ?? [:] : [:]
            var temperatures: [String: Double] = [:]
            for (component, value) in values where temperatureLines.contains(component) {
                temperatures[component.rawValue] = value
            }
            self.temperatures = temperatures
        }

        /// Whether the figures are stale: no tick yet, or the last one is
        /// older than the line the limits widget already dims at. A nil
        /// tick is "collecting", not stale, so the widget can say so.
        public func isOld(now: Date) -> Bool {
            guard let sampledAt else { return false }
            return now.timeIntervalSince(sampledAt) > LimitReading.widgetMaxAge
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
    /// Grouped by source in the dragged order (by pressure until the
    /// first drag), a source's own windows most used first with current
    /// ones before stale ones, from the limits widget's own sources.
    public var limits: [LimitReading]
    /// What the Compute widget draws; nil in files older builds wrote.
    public var compute: ComputeSection?

    /// The heatmap's span: 26 weeks, as on the Overview page.
    public static let heatmapWeeks = 26
    static let modelCount = 4
    /// Enough rows for the large widget, which sizes its rows to its height.
    static let limitCount = 12

    /// `activity` covers the heatmap's half-year span, `models` the last
    /// 30 days; each is filtered down to the harnesses its widget counts.
    /// `limits` drops the sources and windows the limits selection hides,
    /// and `compute` carries the Compute widget's own section, already
    /// filtered to its shown harnesses and temperature lines.
    /// `harnessOrder` ranks the summary's per-harness series (the
    /// Settings order; unknown harnesses keep their first-seen places)
    /// and `harnessColors` gives each its hex.
    public static func make(activity: [DailyUsage], models: [DailyUsage], limits: [LimitReading],
                            compute: ComputeSection? = nil,
                            selection: WidgetSelection = WidgetSelection(),
                            harnessOrder: [String] = [], harnessColors: [String: String] = [:],
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

        // Sources group together in the dragged order; until the first
        // drag that's pressure over every reading, hidden ones included,
        // so hiding a window never moves its source. A source's own
        // windows keep their byPressure order inside the group.
        let sourceRank = selection.limits.defaultRank(LimitReading.byPressure(limits, now: now))
        let sortedLimits = LimitReading.byPressure(selection.limits.filter(limits), now: now)
        let limitRows = selection.limits.ordered(sourceRank)
            .flatMap { source in sortedLimits.filter { $0.source == source } }
            .prefix(limitCount)

        let summaryDays = days(in: summaryRows)
        let heatmapDays = days(in: heatmapRows)

        // A series per counted harness with usage in the span, in the
        // given order; a harness the order doesn't know keeps the place
        // its usage earned.
        var seen: [String] = []
        for row in summaryRows where !seen.contains(row.harness) { seen.append(row.harness) }
        let rank = Dictionary(harnessOrder.enumerated().map { ($1, $0) },
                              uniquingKeysWith: { first, _ in first })
        let orderedHarnesses = seen.enumerated().sorted {
            ((rank[$0.element] ?? .max), $0.offset) < ((rank[$1.element] ?? .max), $1.offset)
        }.map(\.element)
        let series = orderedHarnesses.map { harness in
            HarnessSeries(harness: harness, colorHex: harnessColors[harness] ?? "",
                          days: days(in: summaryRows.filter { $0.harness == harness }))
        }

        return WidgetSnapshot(
            schema: currentSchema, generatedAt: now,
            summary: SummarySection(today: figure(from: today, in: summaryRows),
                                    week: figure(from: weekStart, in: summaryRows),
                                    days: summaryDays,
                                    series: series, showsTotal: selection.summaryShowsTotal),
            heatmap: HeatmapSection(days: heatmapDays, activeDays: heatmapDays.count,
                                    activityTokens: heatmapDays.reduce(0) { $0 + $1.tokens }),
            models: OverviewModel.models(modelRows).prefix(modelCount)
                .map { Model(name: $0.model, tokens: $0.totals.totalTokens,
                             share: $0.share, harnesses: $0.harnesses) },
            limits: Array(limitRows), compute: compute)
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
        Self.perDay(summary.days.map { ($0.day, $0.tokens) }, days: count, now: now, calendar: calendar)
    }

    /// One series' tokens per day for the last `days` days, like
    /// `sparkline(days:now:calendar:)` for the aggregate.
    public func sparkline(_ series: HarnessSeries, days count: Int, now: Date,
                          calendar: Calendar) -> [Int] {
        Self.perDay(series.days.map { ($0.day, $0.tokens) }, days: count, now: now, calendar: calendar)
    }

    private static func perDay(_ rows: [(day: Date, tokens: Int)], days count: Int, now: Date,
                               calendar: Calendar) -> [Int] {
        let today = calendar.startOfDay(for: now)
        var byDay: [Date: Int] = [:]
        for row in rows { byDay[calendar.startOfDay(for: row.day), default: 0] += row.tokens }
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
/// under `~/.swe` like everything else SWE keeps.
public struct WidgetSnapshotStore: Sendable {
    public let file: URL

    public init(file: URL) {
        self.file = file
    }

    public static func defaultFile(home: URL = realHome()) -> URL {
        home.appending(path: ".swe/cache/widgets/snapshot.json")
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
