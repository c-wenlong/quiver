import Foundation

/// Which sources and limit windows one surface shows, from the switch and
/// chips on the Desktop widgets tab. Unlike the menu bar popup's
/// `MenuBarPopup` there is no ordering (the Usage limits widget always
/// sorts by pressure) and no legacy keys: the choice starts clean.
public struct SourceSelection: Equatable, Sendable {
    /// Sources hidden under this key prefix.
    public let hiddenSourcesKey: String
    /// Window labels hidden per source id, under this key prefix.
    public let hiddenWindowsKey: String
    /// Source ids left out.
    public private(set) var hiddenSources: Set<String> = []
    /// Window labels left out, per source id.
    public private(set) var hiddenWindows: [String: Set<String>] = [:]

    public init(hiddenSourcesKey: String, hiddenWindowsKey: String) {
        self.hiddenSourcesKey = hiddenSourcesKey
        self.hiddenWindowsKey = hiddenWindowsKey
    }

    public init(defaults: UserDefaults, hiddenSourcesKey: String, hiddenWindowsKey: String) {
        self.init(hiddenSourcesKey: hiddenSourcesKey, hiddenWindowsKey: hiddenWindowsKey)
        hiddenSources = Set(defaults.stringArray(forKey: hiddenSourcesKey) ?? [])
        let windows = defaults.dictionary(forKey: hiddenWindowsKey) as? [String: [String]] ?? [:]
        hiddenWindows = windows.mapValues(Set.init)
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(hiddenSources.sorted(), forKey: hiddenSourcesKey)
        defaults.set(hiddenWindows.mapValues { $0.sorted() }, forKey: hiddenWindowsKey)
    }

    /// Whether the source shows at all.
    public func shows(source: String) -> Bool { !hiddenSources.contains(source) }

    /// A window shows with its source shown and itself not hidden. A
    /// sourceless window label, the whole-limit kind, hides with the source.
    public func shows(window: String, source: String) -> Bool {
        shows(source: source) && !(hiddenWindows[source] ?? []).contains(window)
    }

    public mutating func setShown(_ shown: Bool, source: String) {
        if shown { hiddenSources.remove(source) } else { hiddenSources.insert(source) }
    }

    public mutating func setShown(_ shown: Bool, window: String, source: String) {
        var hidden = hiddenWindows[source] ?? []
        if shown { hidden.remove(window) } else { hidden.insert(window) }
        hiddenWindows[source] = hidden
    }

    /// The readings this selection keeps: shown sources, shown windows.
    public func filter(_ readings: [LimitReading]) -> [LimitReading] {
        readings.filter { shows(window: $0.window, source: $0.source) }
    }
}

/// The desktop widgets' own settings: which limit sources and windows the
/// Usage limits widget shows, and which harnesses the three usage widgets
/// count. A harness shows until switched off, so one added later is on.
public struct WidgetSelection: Equatable, Sendable {
    /// A widget that counts per-harness usage. Its raw value ends the
    /// `widgetHarnesses` key, and matches its `WidgetCatalog` kind.
    public enum Widget: String, CaseIterable, Sendable {
        case summary, heatmap, models
    }

    /// The Usage limits widget's sources and windows.
    public var limits = SourceSelection(hiddenSourcesKey: SourceSelection.widgetHiddenSourcesKey,
                                        hiddenWindowsKey: SourceSelection.widgetHiddenWindowsKey)
    /// Harnesses each usage widget leaves out.
    public private(set) var hiddenHarnesses: [Widget: Set<String>] = [:]

    public init() {}

    public init(defaults: UserDefaults) {
        limits = SourceSelection(defaults: defaults,
                                 hiddenSourcesKey: SourceSelection.widgetHiddenSourcesKey,
                                 hiddenWindowsKey: SourceSelection.widgetHiddenWindowsKey)
        for widget in Widget.allCases {
            hiddenHarnesses[widget] = Set(defaults.stringArray(forKey: Self.harnessesKey(widget)) ?? [])
        }
    }

    public func save(to defaults: UserDefaults) {
        limits.save(to: defaults)
        for widget in Widget.allCases {
            defaults.set((hiddenHarnesses[widget] ?? []).sorted(), forKey: Self.harnessesKey(widget))
        }
    }

    /// Whether `widget` counts `harness`.
    public func shows(harness: String, in widget: Widget) -> Bool {
        !(hiddenHarnesses[widget] ?? []).contains(harness)
    }

    public mutating func setShown(_ shown: Bool, harness: String, in widget: Widget) {
        var hidden = hiddenHarnesses[widget] ?? []
        if shown { hidden.remove(harness) } else { hidden.insert(harness) }
        hiddenHarnesses[widget] = hidden
    }

    /// The rows `widget` counts, its hidden harnesses dropped.
    public func filter(_ rows: [DailyUsage], for widget: Widget) -> [DailyUsage] {
        rows.filter { shows(harness: $0.harness, in: widget) }
    }

    /// Every harness the widget's switch list offers: the ones with
    /// usage, plus any the hidden set still names, so a choice survives
    /// its usage leaving the range and can be switched back on.
    public func listedHarnesses(withUsage names: Set<String>, for widget: Widget) -> Set<String> {
        names.union(hiddenHarnesses[widget] ?? [])
    }

    public static func harnessesKey(_ widget: Widget) -> String {
        "widgetHarnesses.\(widget.rawValue)"
    }
}

extension SourceSelection {
    public static let widgetHiddenSourcesKey = "widgetLimitsHiddenSources"
    public static let widgetHiddenWindowsKey = "widgetLimitsHiddenSourceWindows"
}
