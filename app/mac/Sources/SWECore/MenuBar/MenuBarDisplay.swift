import Foundation

/// One figure the menu bar item can show next to its icon.
public enum MenuBarFigure: String, CaseIterable, Identifiable, Sendable {
    case none, limit, todaySpend, todayTokens

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .none: "Nothing"
        case .limit: "Limit"
        case .todaySpend: "Today's spend"
        case .todayTokens: "Today's tokens"
        }
    }
}

/// What the menu bar item shows, and whether it's there at all. Set on the
/// Menu bar page; the icon is always the watched limit's ring.
public struct MenuBarDisplay: Equatable, Sendable {
    public var isVisible = true
    public var primary = MenuBarFigure.none
    public var secondary = MenuBarFigure.none
    /// A `LimitReading.id`, or nil for the tightest of all.
    public var watchedLimit: String?

    public enum Key {
        public static let visible = "menuBarVisible"
        public static let primary = "menuBarPrimary"
        public static let secondary = "menuBarSecondary"
        public static let watchedLimit = "menuBarWatchedLimit"
    }

    public init(defaults: UserDefaults) {
        isVisible = defaults.object(forKey: Key.visible) as? Bool ?? true
        primary = defaults.string(forKey: Key.primary).flatMap(MenuBarFigure.init(rawValue:)) ?? .none
        secondary = defaults.string(forKey: Key.secondary).flatMap(MenuBarFigure.init(rawValue:)) ?? .none
        watchedLimit = defaults.string(forKey: Key.watchedLimit)
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(isVisible, forKey: Key.visible)
        defaults.set(primary.rawValue, forKey: Key.primary)
        defaults.set(secondary.rawValue, forKey: Key.secondary)
        defaults.set(watchedLimit, forKey: Key.watchedLimit)
    }

    /// The watched limit, or the tightest current one when none is chosen
    /// or the chosen one is gone (its account removed), stale (its last
    /// fetch failed) or past its reset. An old percentage never shows as now.
    public func limit(in readings: [LimitReading], now: Date) -> LimitReading? {
        if let watchedLimit, let reading = readings.first(where: { $0.id == watchedLimit }),
           reading.isCurrent(now: now) {
            return reading
        }
        return LimitReading.tightest(readings, now: now)
    }

    /// Today's totals across every harness, or nil before the ledger has
    /// been read.
    public static func today(in spend: [SpendSlice]?) -> UsageTotals? {
        guard let slice = spend?.first(where: { $0.period == .today }) else { return nil }
        return slice.byHarness.values.reduce(into: UsageTotals()) { $0.add($1) }
    }

    /// The text beside the icon: the chosen figures that have data, joined
    /// with " · ". Empty shows the icon alone.
    public func text(limit: LimitReading?, today: UsageTotals?) -> String {
        var parts: [String] = []
        for figure in [primary, secondary] {
            guard let part = Self.value(figure, limit: limit, today: today), !parts.contains(part) else { continue }
            parts.append(part)
        }
        return parts.joined(separator: " · ")
    }

    private static func value(_ figure: MenuBarFigure, limit: LimitReading?, today: UsageTotals?) -> String? {
        switch figure {
        case .none: nil
        case .limit: limit.map { "\($0.percent)%" }
        case .todaySpend: today.map { UsageFormat.dollars($0.costUSD) }
        case .todayTokens: today.map { UsageFormat.tokens($0.totalTokens) }
        }
    }
}

/// The desktop widgets the app ships, by their WidgetKit kind.
public struct WidgetCatalog: Sendable, Identifiable {
    public var id: String { kind }
    public let kind: String
    public let title: String
    public let detail: String
    /// SF Symbol name.
    public let symbol: String

    public static let all = [
        WidgetCatalog(kind: "limits", title: "Usage limits",
                      detail: "How much of each rate limit is used, in the order you set in SWE.",
                      symbol: "gauge.with.dots.needle.67percent"),
        WidgetCatalog(kind: "summary", title: "Usage summary",
                      detail: "Today and the last seven days, with a sparkline.",
                      symbol: "chart.bar.xaxis"),
        WidgetCatalog(kind: "heatmap", title: "Activity heatmap",
                      detail: "Tokens per day for the last half year.",
                      symbol: "square.grid.3x3.fill"),
        WidgetCatalog(kind: "models", title: "Top models",
                      detail: "Your most used models over the last 30 days, by token share.",
                      symbol: "list.number"),
        WidgetCatalog(kind: "compute", title: "Compute",
                      detail: "What each running harness costs in RAM, CPU and GPU, with the Mac's temperatures.",
                      symbol: "cpu"),
    ]
}
