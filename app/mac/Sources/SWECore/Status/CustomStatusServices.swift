import Foundation

/// The page's editable list, as preferences hold it: the user's own pages
/// plus what they changed about the built-ins. Built-ins are never
/// written out as services — `hidden` lists the removed ids and
/// `overrides` the fields an edit replaced, so a built-in the user left
/// alone still follows the code's catalog when it changes.
public struct StatusServicePrefs: Equatable, Sendable {
    /// The fields an edit can replace on a built-in. A `nil` field keeps
    /// the catalog's; an all-nil edit is no override at all.
    public struct Edit: Codable, Equatable, Sendable {
        public var name: String?
        public var pageURL: URL?
        /// Only read on `.statuspage` services; other feeds are fixed.
        public var feedURL: URL?
        public var group: StatusService.Group?

        public init(name: String? = nil, pageURL: URL? = nil,
                    feedURL: URL? = nil, group: StatusService.Group? = nil) {
            self.name = name
            self.pageURL = pageURL
            self.feedURL = feedURL
            self.group = group
        }

        public var isEmpty: Bool {
            name == nil && pageURL == nil && feedURL == nil && group == nil
        }
    }

    /// Pages the user added, marked `isCustom`.
    public var custom: [StatusService] = []
    /// Built-in ids removed from the page.
    public var hidden: Set<String> = []
    /// Built-in id → the fields an edit replaced.
    public var overrides: [String: Edit] = [:]

    public init(custom: [StatusService] = [], hidden: Set<String> = [],
                overrides: [String: Edit] = [:]) {
        self.custom = custom
        self.hidden = hidden
        self.overrides = overrides
    }
}

/// Where a `ServiceStatusModel` keeps the list's edits. Tests use an
/// in-memory one; the app uses `CustomStatusServices`.
public protocol StatusServiceStoring: Sendable {
    func load() -> StatusServicePrefs
    func save(_ prefs: StatusServicePrefs)
}

/// The user's status pages and edits, kept in preferences as JSON records
/// like the app's other settings. Only what is needed to rebuild a
/// `StatusService` is written. Data that does not decode reads as an
/// empty list rather than failing.
/// UserDefaults reads and writes are thread-safe, so the class is.
public final class CustomStatusServices: StatusServiceStoring, @unchecked Sendable {
    public static let defaultsKey = "customStatusServices"

    private struct Record: Codable {
        var id: String
        var name: String
        var pageURL: URL
        /// Nil in records written before feeds could sit off /api/v2.
        var feedURL: URL?
        /// The raw string, so a group written before the split is still
        /// legible as one and `service(_:)` can migrate just those.
        var group: String
    }

    private struct Prefs: Codable {
        var custom: [Record] = []
        var hidden: Set<String> = []
        var overrides: [String: StatusServicePrefs.Edit] = [:]
    }

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// The groups before the eight-way split. A record still carrying
    /// one was last saved under the old layout, so a catalogue page it
    /// names follows the catalogue's group; anything else gets the
    /// mapped group. Records saved since keep their own string — a
    /// group the user picked is never rewritten on load.
    private static let legacyGroups: [String: StatusService.Group] =
        ["providers": .modelProviders, "infrastructure": .services]

    private func service(_ record: Record) -> StatusService {
        let feed = record.feedURL ?? record.pageURL.appending(path: "api/v2/summary.json")
        let group: StatusService.Group
        if let legacy = Self.legacyGroups[record.group] {
            group = StatusCatalogue.all
                .first(where: { $0.host == feed.host() })?.group ?? legacy
        } else {
            group = StatusService.Group(rawValue: record.group) ?? .services
        }
        return StatusService(id: record.id, name: record.name, pageURL: record.pageURL,
                             feed: .statuspage(feed), group: group, isCustom: true)
    }

    public func load() -> StatusServicePrefs {
        guard let data = defaults.data(forKey: Self.defaultsKey) else { return StatusServicePrefs() }
        if let prefs = try? JSONDecoder().decode(Prefs.self, from: data) {
            return StatusServicePrefs(custom: prefs.custom.map(service),
                                      hidden: prefs.hidden, overrides: prefs.overrides)
        }
        // The first layout was a bare array of custom records.
        guard let records = try? JSONDecoder().decode([Record].self, from: data) else {
            return StatusServicePrefs()
        }
        return StatusServicePrefs(custom: records.map(service))
    }

    /// Nil data removes the key, so saving an empty list clears the entry.
    public func save(_ prefs: StatusServicePrefs) {
        let stored = Prefs(
            custom: prefs.custom.filter(\.isCustom).map {
                Record(id: $0.id, name: $0.name, pageURL: $0.pageURL,
                       feedURL: $0.feedURL, group: $0.group.rawValue)
            },
            hidden: prefs.hidden,
            overrides: prefs.overrides)
        defaults.set(try? JSONEncoder().encode(stored), forKey: Self.defaultsKey)
    }
}
