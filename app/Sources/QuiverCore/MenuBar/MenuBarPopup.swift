import Foundation

/// What the menu bar popup shows: the incidents line, the spend section,
/// and which harnesses and which of their limit windows are listed. Set on
/// the Menu bar and widgets page; everything shows until hidden there.
public struct MenuBarPopup: Equatable, Sendable {
    public var showIncidents = true
    /// The spend-by-harness section.
    public var showStats = true
    /// Harness ids (`LimitReading.harness`) left out of the popup.
    public private(set) var hiddenHarnesses: Set<String> = []
    /// Window labels left out, per harness id.
    public private(set) var hiddenWindows: [String: Set<String>] = [:]
    /// The order the popup lists harnesses in, dragged on the Menu bar and
    /// widgets page. An id not shown right now keeps its place, so an
    /// unstarred harness comes back where it was when starred again.
    public private(set) var harnessOrder: [String] = []

    public enum Key {
        public static let incidents = "popupShowIncidents"
        public static let stats = "popupShowStats"
        public static let hiddenHarnesses = "popupHiddenHarnesses"
        public static let hiddenWindows = "popupHiddenWindows"
        public static let harnessOrder = "popupHarnessOrder"
    }

    public init(defaults: UserDefaults) {
        showIncidents = defaults.object(forKey: Key.incidents) as? Bool ?? true
        showStats = defaults.object(forKey: Key.stats) as? Bool ?? true
        hiddenHarnesses = Set(defaults.stringArray(forKey: Key.hiddenHarnesses) ?? [])
        let windows = defaults.dictionary(forKey: Key.hiddenWindows) as? [String: [String]] ?? [:]
        hiddenWindows = windows.mapValues(Set.init).filter { !$0.value.isEmpty }
        harnessOrder = defaults.stringArray(forKey: Key.harnessOrder) ?? []
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(showIncidents, forKey: Key.incidents)
        defaults.set(showStats, forKey: Key.stats)
        defaults.set(hiddenHarnesses.sorted(), forKey: Key.hiddenHarnesses)
        defaults.set(hiddenWindows.mapValues { $0.sorted() }, forKey: Key.hiddenWindows)
        defaults.set(harnessOrder, forKey: Key.harnessOrder)
    }

    public func shows(harness: String) -> Bool { !hiddenHarnesses.contains(harness) }

    public func shows(window: String, of harness: String) -> Bool {
        shows(harness: harness) && !(hiddenWindows[harness]?.contains(window) ?? false)
    }

    public mutating func setShown(_ shown: Bool, harness: String) {
        if shown { hiddenHarnesses.remove(harness) } else { hiddenHarnesses.insert(harness) }
    }

    public mutating func setShown(_ shown: Bool, window: String, of harness: String) {
        var hidden = hiddenWindows[harness] ?? []
        if shown { hidden.remove(window) } else { hidden.insert(window) }
        hiddenWindows[harness] = hidden.isEmpty ? nil : hidden
    }

    /// `ids` in the dragged order: the ones it knows by their place, then
    /// the ones it doesn't in the order given. An empty dragged order
    /// leaves `ids` exactly as it came.
    public func ordered(_ ids: [String]) -> [String] {
        harnessOrder.filter { ids.contains($0) } + ids.filter { !harnessOrder.contains($0) }
    }

    /// Drag `id` before `target`, where `shown` is what the list shows now.
    /// Shown ids not yet ordered join `harnessOrder` in their shown order
    /// first, so the move lands where it was dropped.
    public mutating func move(_ id: String, before target: String, in shown: [String]) {
        guard id != target, shown.contains(id), shown.contains(target) else { return }
        harnessOrder += ordered(shown).filter { !harnessOrder.contains($0) }
        harnessOrder.removeAll { $0 == id }
        if let index = harnessOrder.firstIndex(of: target) {
            harnessOrder.insert(id, at: index)
        }
    }

    /// Drop `id` below the last shown row: it lands at the end of the
    /// visible list, without jumping past hidden ids that follow it.
    public mutating func moveToEnd(_ id: String, in shown: [String]) {
        guard shown.contains(id) else { return }
        harnessOrder += ordered(shown).filter { !harnessOrder.contains($0) }
        harnessOrder.removeAll { $0 == id }
        if let last = ordered(shown).last(where: { $0 != id }),
           let index = harnessOrder.firstIndex(of: last) {
            harnessOrder.insert(id, at: index + 1)
        } else {
            harnessOrder.append(id)
        }
    }

    /// `harnesses(in:order:)` re-sorted by the dragged order.
    public func orderedHarnesses(in readings: [LimitReading], order: [String]) -> [Harness] {
        let harnesses = Self.harnesses(in: readings, order: order)
        let byId = Dictionary(uniqueKeysWithValues: harnesses.map { ($0.id, $0) })
        return ordered(harnesses.map(\.id)).compactMap { byId[$0] }
    }

    /// One harness the popup can list, and the named windows it reports.
    public struct Harness: Equatable, Sendable, Identifiable {
        public var id: String { harness }
        public let harness: String
        public let windows: [String]

        public init(harness: String, windows: [String]) {
            self.harness = harness
            self.windows = windows
        }
    }

    /// Every harness in `order` (accounts, then other harnesses, so the list
    /// doesn't reshuffle as usage changes), then any other harness in the
    /// readings, each with its distinct named windows in first-seen order.
    /// A harness in `order` with no readings yet is listed with no windows.
    public static func harnesses(in readings: [LimitReading], order given: [String] = []) -> [Harness] {
        var order: [String] = []
        var seen: Set<String> = []
        for harness in given where seen.insert(harness).inserted { order.append(harness) }
        var windows: [String: [String]] = [:]
        for reading in readings {
            if seen.insert(reading.harness).inserted { order.append(reading.harness) }
            if windows[reading.harness] == nil { windows[reading.harness] = [] }
            if !reading.window.isEmpty, !(windows[reading.harness]?.contains(reading.window) ?? false) {
                windows[reading.harness]?.append(reading.window)
            }
        }
        return order.map { Harness(harness: $0, windows: windows[$0] ?? []) }
    }
}
