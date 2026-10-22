import Foundation

/// The GitHub pages' warm start: the pins, the selection, the last rows
/// each surface held, and the last good inbox. Written through
/// `SnapshotFile` on a debounce after every reply and once at quit; a
/// missing or corrupt file only ever costs the cold start it would have
/// been.
///
/// Fetch times are deliberately NOT persisted: every restored surface is
/// cold, so it shows its saved rows immediately and refetches on the
/// first look. A snapshot can otherwise skip a whole `staleAfter` window
/// across a restart — including a gh account switch while the app was
/// closed.
public struct GitHubSnapshot: SnapshotContent {
    public static let currentVersion = 2
    public var version = Self.currentVersion
    public var savedAt: Date
    /// The pins and the selection, so the Repositories page opens on its
    /// last view instead of a probe spinner.
    public var pins: [PinnedRepo]
    public var selected: String?
    /// repo name -> the board as it last stood.
    public var boards: [String: RepoBoard]
    /// The last successfully loaded inbox; a failed refresh never
    /// overwrites it. `inboxLoaded` records whether a load ever
    /// succeeded: without it an empty inbox restores as "no unread"
    /// when the app simply never asked.
    public var inbox: [GhNotification]
    public var inboxLoaded: Bool
    /// repo name -> accumulated focus-page rows. Fetches merge in by
    /// number, so a repo's listing grows past any single fetch window;
    /// a surface whose reply fails keeps its remembered rows. Optional:
    /// the field postdates v2's shape, and a file without it still
    /// decodes.
    public var history: [String: RepoHistory]?

    public init(savedAt: Date, pins: [PinnedRepo], selected: String?,
                boards: [String: RepoBoard], inbox: [GhNotification],
                inboxLoaded: Bool, history: [String: RepoHistory]? = nil) {
        self.savedAt = savedAt
        self.pins = pins
        self.selected = selected
        self.boards = boards
        self.inbox = inbox
        self.inboxLoaded = inboxLoaded
        self.history = history
    }
}

/// One repo's remembered rows on the accumulating surfaces — the ones
/// where an old row still matters once it falls out of the fetch
/// window. Releases, tags and branches are list state instead: what
/// the reply did not list no longer exists, so they persist by
/// replacement in the SQLite store, not here.
public struct RepoHistory: Codable, Equatable, Sendable {
    public var pulls: [GhPr]
    public var issues: [GhIssue]
    public var runs: [GhRun]

    public init(pulls: [GhPr] = [], issues: [GhIssue] = [], runs: [GhRun] = []) {
        self.pulls = pulls
        self.issues = issues
        self.runs = runs
    }

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        pulls = try box.decodeIfPresent([GhPr].self, forKey: .pulls) ?? []
        issues = try box.decodeIfPresent([GhIssue].self, forKey: .issues) ?? []
        // `runs` postdates the snapshot field — decode-or-empty keeps
        // files written before it existed loading.
        runs = try box.decodeIfPresent([GhRun].self, forKey: .runs) ?? []
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
