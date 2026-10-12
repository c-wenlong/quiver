import Foundation

/// Where a `ServiceStatusModel` keeps the status pages the user added.
/// Tests use an in-memory one; the app uses `CustomStatusServices`.
public protocol CustomStatusStoring: Sendable {
    func load() -> [StatusService]
    func save(_ services: [StatusService])
}

/// The user's own status pages, kept in preferences as JSON records like
/// the app's other settings. Only what is needed to rebuild a
/// `StatusService` is written; the feed is always Statuspage's
/// `summary.json` at the page's root. Data that does not decode reads as
/// an empty list rather than failing.
/// UserDefaults reads and writes are thread-safe, so the class is.
public final class CustomStatusServices: CustomStatusStoring, @unchecked Sendable {
    public static let defaultsKey = "customStatusServices"

    private struct Record: Codable {
        var id: String
        var name: String
        var pageURL: URL
        var group: StatusService.Group
    }

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public func load() -> [StatusService] {
        guard let data = defaults.data(forKey: Self.defaultsKey),
              let records = try? JSONDecoder().decode([Record].self, from: data)
        else { return [] }
        return records.map { record in
            StatusService(id: record.id, name: record.name, pageURL: record.pageURL,
                          feed: .statuspage(record.pageURL.appending(path: "api/v2/summary.json")),
                          group: record.group, isCustom: true)
        }
    }

    /// Nil data removes the key, so saving no services clears the entry.
    public func save(_ services: [StatusService]) {
        let records = services.filter(\.isCustom).map {
            Record(id: $0.id, name: $0.name, pageURL: $0.pageURL, group: $0.group)
        }
        defaults.set(try? JSONEncoder().encode(records), forKey: Self.defaultsKey)
    }
}
