import Foundation

/// The GitHub pages' warm start: the last rows each surface held, the
/// inbox, and the fetch times that make them stale. Written atomically
/// on a debounce after every reply and once at quit; a missing or
/// corrupt file only ever costs the cold start it would have been.
public struct GitHubSnapshot: Codable, Sendable {
    public var savedAt: Date
    /// repo name -> the board as it last stood.
    public var boards: [String: RepoBoard]
    /// "owner/repo|tab" -> last fetch, matching each board's loaded slots.
    public var fetched: [String: Date]
    public var inbox: [GhNotification]
    public var inboxFetchedAt: Date?

    public init(savedAt: Date, boards: [String: RepoBoard], fetched: [String: Date],
                inbox: [GhNotification], inboxFetchedAt: Date?) {
        self.savedAt = savedAt
        self.boards = boards
        self.fetched = fetched
        self.inbox = inbox
        self.inboxFetchedAt = inboxFetchedAt
    }
}

/// `Loadable` was decode-only as a wire shape; the snapshot needs it
/// round-trippable. `.loading` encodes but decodes as `.idle` — a fetch
/// interrupted by quit has no reply coming, so it must not look in-flight.
extension Loadable: Codable where Value: Codable {
    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: SnapshotKeys.self)
        switch try box.decode(String.self, forKey: .state) {
        case "loaded": self = .loaded(try box.decode(Value.self, forKey: .value))
        case "failed": self = .failed(try box.decode(String.self, forKey: .error))
        default: self = .idle
        }
    }

    public func encode(to encoder: any Encoder) throws {
        var box = encoder.container(keyedBy: SnapshotKeys.self)
        switch self {
        case .idle: try box.encode("idle", forKey: .state)
        case .loading: try box.encode("loading", forKey: .state)
        case .loaded(let value):
            try box.encode("loaded", forKey: .state)
            try box.encode(value, forKey: .value)
        case .failed(let message):
            try box.encode("failed", forKey: .state)
            try box.encode(message, forKey: .error)
        }
    }

    private enum SnapshotKeys: String, CodingKey { case state, value, error }
}
