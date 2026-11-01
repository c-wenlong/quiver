import Foundation

/// What the menu bar popup shows: the incidents line, the spend section,
/// and which accounts and harnesses and which of their limit windows are
/// listed. Set on the Menu bar page; everything shows until
/// hidden there.
///
/// Settings are keyed by source: `LimitReading.source`, the profile id
/// for an SWE account and the harness name for the other harnesses, so
/// two accounts of one harness hide and order independently. The older
/// harness-level keys still read, as legacy fallbacks a per-source
/// choice always beats; they are never written again.
public struct MenuBarPopup: Equatable, Sendable {
    public var showIncidents = true
    /// The spend-by-harness section.
    public var showStats = true
    /// Source ids left out of the popup.
    public private(set) var hiddenSources: Set<String> = []
    /// Window labels left out, per source id. A source with any entry at
    /// all, an empty set included, has its own window list: it no longer
    /// falls back to the harness's legacy one, and a legacy harness-wide
    /// hide no longer covers it either.
    public private(set) var hiddenSourceWindows: [String: Set<String>] = [:]
    /// The order the popup lists sources in, dragged on the Menu bar and
    /// widgets page. A source not shown right now keeps its place, so an
    /// unstarred account comes back where it was when starred again.
    public private(set) var sourceOrder: [String] = []
    /// Harness-level choices the pre-account UI saved. Read from these
    /// only as fallbacks for sources with no per-source entry.
    var legacyHiddenHarnesses: Set<String> = []
    var legacyHiddenWindows: [String: Set<String>] = [:]
    var legacyHarnessOrder: [String] = []

    public enum Key {
        public static let incidents = "popupShowIncidents"
        public static let stats = "popupShowStats"
        public static let hiddenSources = "popupHiddenSources"
        public static let hiddenSourceWindows = "popupHiddenSourceWindows"
        public static let sourceOrder = "popupSourceOrder"
        /// The old harness-level keys. Kept so the migration that rewrites
        /// them still compiles; new choices never land here.
        public static let hiddenHarnesses = "popupHiddenHarnesses"
        public static let hiddenWindows = "popupHiddenWindows"
        public static let harnessOrder = "popupHarnessOrder"
    }

    public init(defaults: UserDefaults) {
        showIncidents = defaults.object(forKey: Key.incidents) as? Bool ?? true
        showStats = defaults.object(forKey: Key.stats) as? Bool ?? true
        hiddenSources = Set(defaults.stringArray(forKey: Key.hiddenSources) ?? [])
        let windows = defaults.dictionary(forKey: Key.hiddenSourceWindows) as? [String: [String]] ?? [:]
        // An empty set is a real entry: it means "show every window".
        hiddenSourceWindows = windows.mapValues(Set.init)
        sourceOrder = defaults.stringArray(forKey: Key.sourceOrder) ?? []
        legacyHiddenHarnesses = Set(defaults.stringArray(forKey: Key.hiddenHarnesses) ?? [])
        let legacy = defaults.dictionary(forKey: Key.hiddenWindows) as? [String: [String]] ?? [:]
        legacyHiddenWindows = legacy.mapValues(Set.init).filter { !$0.value.isEmpty }
        legacyHarnessOrder = defaults.stringArray(forKey: Key.harnessOrder) ?? []
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(showIncidents, forKey: Key.incidents)
        defaults.set(showStats, forKey: Key.stats)
        defaults.set(hiddenSources.sorted(), forKey: Key.hiddenSources)
        defaults.set(hiddenSourceWindows.mapValues { $0.sorted() }, forKey: Key.hiddenSourceWindows)
        defaults.set(sourceOrder, forKey: Key.sourceOrder)
    }

    /// Whether the popup lists this source at all. An explicit per-source
    /// state wins; with none, the legacy hide of its harness decides.
    public func shows(source: String, harness: String) -> Bool {
        if hiddenSources.contains(source) { return false }
        // A source with its own window list stands on its own.
        if hiddenSourceWindows[source] != nil { return true }
        return !legacyHiddenHarnesses.contains(harness)
    }

    /// The windows hidden for a source right now: its own list when it
    /// has one, else the legacy set of its harness, except when that set
    /// would hide every window the source reports; a legacy rule must
    /// never make a whole account vanish.
    private func effectiveHiddenWindows(source: String, harness: String,
                                        allWindows: [String]) -> Set<String> {
        if let explicit = hiddenSourceWindows[source] { return explicit }
        let legacy = legacyHiddenWindows[harness] ?? []
        if !allWindows.isEmpty, allWindows.allSatisfy(legacy.contains) { return [] }
        return legacy.intersection(allWindows)
    }

    public func shows(window: String, source: String, harness: String, allWindows: [String]) -> Bool {
        shows(source: source, harness: harness)
            && !effectiveHiddenWindows(source: source, harness: harness,
                                       allWindows: allWindows).contains(window)
    }

    /// Writes the source's effective state as its own, then flips the
    /// switch, so a choice never leaks to the harness's other accounts.
    public mutating func setShown(_ shown: Bool, source: String, harness: String,
                                  allWindows: [String]) {
        if !shows(source: source, harness: harness) { hiddenSources.insert(source) }
        if shown {
            hiddenSources.remove(source)
            // Shown is the default only where no legacy hide covers the
            // harness; against one, the source needs a record of its own
            // to stay shown. That record keeps the windows it hides now,
            // not an empty list that would show them all.
            if legacyHiddenHarnesses.contains(harness), hiddenSourceWindows[source] == nil {
                hiddenSourceWindows[source] = effectiveHiddenWindows(
                    source: source, harness: harness, allWindows: allWindows)
            }
        } else {
            hiddenSources.insert(source)
        }
    }

    /// Writes the source's effective state as its own, then flips the one
    /// window.
    public mutating func setShown(_ shown: Bool, window: String, source: String,
                                  harness: String, allWindows: [String]) {
        if !shows(source: source, harness: harness) { hiddenSources.insert(source) }
        var hidden = effectiveHiddenWindows(source: source, harness: harness,
                                            allWindows: allWindows)
        if shown { hidden.remove(window) } else { hidden.insert(window) }
        hiddenSourceWindows[source] = hidden
    }

    /// The given sources in the popup's order: a dragged position wins,
    /// then the legacy harness order of the source's harness, keeping the
    /// given order among one harness's accounts. Sources whose harness the
    /// legacy order doesn't know go last, in the given order.
    public func ordered(_ sources: [(source: String, harness: String)]) -> [String] {
        let explicit = sourceOrder.filter { id in sources.contains { $0.source == id } }
        let rank = Dictionary(legacyHarnessOrder.enumerated().map { ($1, $0) },
                              uniquingKeysWith: { first, _ in first })
        let rest = sources.enumerated()
            .filter { !sourceOrder.contains($0.element.source) }
            .sorted { a, b in
                let ra = rank[a.element.harness] ?? .max
                let rb = rank[b.element.harness] ?? .max
                return ra != rb ? ra < rb : a.offset < b.offset
            }
            .map(\.element.source)
        return explicit + rest
    }

    /// Drag `id` before `target`, where `shown` is what the list shows now.
    /// Shown sources not yet ordered join `sourceOrder` in their shown
    /// order first, so the move lands where it was dropped.
    public mutating func move(_ id: String, before target: String,
                              in shown: [(source: String, harness: String)]) {
        SourceOrdering.move(id, before: target, in: ordered(shown), order: &sourceOrder)
    }

    /// Drop `id` below the last shown row: it lands at the end of the
    /// visible list, without jumping past hidden sources that follow it.
    public mutating func moveToEnd(_ id: String, in shown: [(source: String, harness: String)]) {
        SourceOrdering.moveToEnd(id, in: ordered(shown), order: &sourceOrder)
    }

    /// One account or harness the popup can list, and the named windows it
    /// reports.
    public struct Row: Equatable, Sendable, Identifiable {
        public var id: String { source }
        /// `LimitReading.source`: a profile id, or a harness name.
        public let source: String
        /// The harness, for its colour and icon.
        public let harness: String
        /// Titled like its Accounts card: "Claude: Personal", "Devin".
        public let title: String
        public let windows: [String]

        public init(source: String, harness: String, title: String, windows: [String]) {
            self.source = source
            self.harness = harness
            self.title = title
            self.windows = windows
        }
    }

    /// Every given source, then any other source in the readings, each
    /// with its distinct named windows in first-seen order. A source with
    /// no readings yet is listed with no windows, so it can be hidden
    /// before its limits arrive.
    public static func rows(in readings: [LimitReading],
                            sources given: [(source: String, harness: String,
                                             title: String)]) -> [Row] {
        var listed: [(source: String, harness: String, title: String)] = []
        var seen: Set<String> = []
        for source in given where seen.insert(source.source).inserted { listed.append(source) }
        var windows: [String: [String]] = [:]
        for reading in readings {
            if seen.insert(reading.source).inserted {
                listed.append((reading.source, reading.harness, reading.title))
            }
            if windows[reading.source] == nil { windows[reading.source] = [] }
            if !reading.window.isEmpty, !(windows[reading.source]?.contains(reading.window) ?? false) {
                windows[reading.source]?.append(reading.window)
            }
        }
        return listed.map { Row(source: $0.source, harness: $0.harness, title: $0.title,
                                windows: windows[$0.source] ?? []) }
    }

    /// `rows(in:sources:)` re-sorted by the dragged order.
    public func orderedRows(in readings: [LimitReading],
                            sources given: [(source: String, harness: String,
                                             title: String)]) -> [Row] {
        let rows = Self.rows(in: readings, sources: given)
        let bySource = Dictionary(uniqueKeysWithValues: rows.map { ($0.source, $0) })
        return ordered(rows.map { ($0.source, $0.harness) }).compactMap { bySource[$0] }
    }
}
