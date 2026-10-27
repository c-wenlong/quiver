import Foundation

/// Which sources and limit windows one surface shows, and in what order,
/// from the switches, chips and drags on the Desktop widgets tab. Unlike
/// the menu bar popup's `MenuBarPopup` there are no legacy keys: the
/// choice starts clean.
public struct SourceSelection: Equatable, Sendable {
    /// Sources hidden under this key.
    public let hiddenSourcesKey: String
    /// Window labels hidden per source id, under this key.
    public let hiddenWindowsKey: String
    /// The dragged source order under this key; empty disables it.
    public let sourceOrderKey: String
    /// Source ids left out.
    public private(set) var hiddenSources: Set<String> = []
    /// Window labels left out, per source id.
    public private(set) var hiddenWindows: [String: Set<String>] = [:]
    /// The order the surface lists sources in, dragged on the settings
    /// row. A source not shown right now keeps its place.
    public private(set) var sourceOrder: [String] = []

    public init(hiddenSourcesKey: String, hiddenWindowsKey: String, sourceOrderKey: String = "") {
        self.hiddenSourcesKey = hiddenSourcesKey
        self.hiddenWindowsKey = hiddenWindowsKey
        self.sourceOrderKey = sourceOrderKey
    }

    public init(defaults: UserDefaults, hiddenSourcesKey: String, hiddenWindowsKey: String,
                sourceOrderKey: String = "") {
        self.init(hiddenSourcesKey: hiddenSourcesKey, hiddenWindowsKey: hiddenWindowsKey,
                  sourceOrderKey: sourceOrderKey)
        hiddenSources = Set(defaults.stringArray(forKey: hiddenSourcesKey) ?? [])
        let windows = defaults.dictionary(forKey: hiddenWindowsKey) as? [String: [String]] ?? [:]
        hiddenWindows = windows.mapValues(Set.init)
        if !sourceOrderKey.isEmpty {
            sourceOrder = defaults.stringArray(forKey: sourceOrderKey) ?? []
        }
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(hiddenSources.sorted(), forKey: hiddenSourcesKey)
        defaults.set(hiddenWindows.mapValues { $0.sorted() }, forKey: hiddenWindowsKey)
        if !sourceOrderKey.isEmpty {
            defaults.set(sourceOrder, forKey: sourceOrderKey)
        }
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

    /// The sources in first-appearance order over every reading, hidden
    /// ones included. Both the widget snapshot and the settings list rank
    /// by this, so a never-dragged widget shows pressure order in both
    /// places, and hiding a source or a window never moves its row.
    public func defaultRank(_ readings: [LimitReading]) -> [String] {
        var seen: [String] = []
        for reading in readings where !seen.contains(reading.source) {
            seen.append(reading.source)
        }
        return seen
    }

    /// The given ids in the dragged order: explicitly ordered ids first,
    /// then the rest in the given order.
    public func ordered(_ ids: [String]) -> [String] {
        sourceOrder.filter(ids.contains) + ids.filter { !sourceOrder.contains($0) }
    }

    /// Drag `id` before `target`, where `shown` is what the list shows
    /// now. The first move freezes the shown order into `sourceOrder`.
    public mutating func move(_ id: String, before target: String, in shown: [String]) {
        SourceOrdering.move(id, before: target, in: shown, order: &sourceOrder)
    }

    /// Drop `id` below the last shown row.
    public mutating func moveToEnd(_ id: String, in shown: [String]) {
        SourceOrdering.moveToEnd(id, in: shown, order: &sourceOrder)
    }

    /// The readings this selection keeps: shown sources, shown windows.
    public func filter(_ readings: [LimitReading]) -> [LimitReading] {
        readings.filter { shows(window: $0.window, source: $0.source) }
    }
}

/// The desktop widgets' own settings: which limit sources and windows the
/// Usage limits widget shows, and which harnesses the per-harness widgets
/// count. A harness shows until switched off, so one added later is on.
public struct WidgetSelection: Equatable, Sendable {
    /// A widget that lists per-harness figures. Its raw value ends the
    /// `widgetHarnesses` key, and matches its `WidgetCatalog` kind.
    public enum Widget: String, CaseIterable, Sendable {
        case summary, heatmap, models, compute
    }

    /// The Usage limits widget's sources and windows, in the dragged order.
    public var limits = SourceSelection(hiddenSourcesKey: SourceSelection.widgetHiddenSourcesKey,
                                        hiddenWindowsKey: SourceSelection.widgetHiddenWindowsKey,
                                        sourceOrderKey: SourceSelection.widgetSourceOrderKey)
    /// Harnesses each usage widget leaves out.
    public private(set) var hiddenHarnesses: [Widget: Set<String>] = [:]
    /// Whether the Usage summary widget draws the aggregate total line
    /// on top of the per-harness ones.
    public var summaryShowsTotal = true

    public init() {}

    public init(defaults: UserDefaults) {
        limits = SourceSelection(defaults: defaults,
                                 hiddenSourcesKey: SourceSelection.widgetHiddenSourcesKey,
                                 hiddenWindowsKey: SourceSelection.widgetHiddenWindowsKey,
                                 sourceOrderKey: SourceSelection.widgetSourceOrderKey)
        for widget in Widget.allCases {
            hiddenHarnesses[widget] = Set(defaults.stringArray(forKey: Self.harnessesKey(widget)) ?? [])
        }
        summaryShowsTotal = defaults.object(forKey: Self.summaryShowsTotalKey) as? Bool ?? true
    }

    public func save(to defaults: UserDefaults) {
        limits.save(to: defaults)
        for widget in Widget.allCases {
            defaults.set((hiddenHarnesses[widget] ?? []).sorted(), forKey: Self.harnessesKey(widget))
        }
        defaults.set(summaryShowsTotal, forKey: Self.summaryShowsTotalKey)
    }

    public static let summaryShowsTotalKey = "widgetSummaryShowsTotal"

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
    public static let widgetSourceOrderKey = "widgetLimitsSourceOrder"
}
