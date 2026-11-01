import Foundation

/// The Sessions page as it last loaded: swe's list and each session's usage.
/// Saved so the next launch shows it at once while swe lists again.
/// Holds session titles and folders, no credential.
public struct SessionSnapshot: SnapshotContent, Equatable {
    public static let currentVersion = 1

    public var version = Self.currentVersion
    /// The swe the list came from, as set in Settings (empty for PATH's).
    public var source: String
    public var savedAt: Date
    public var sessions: [SessionInfo]
    public var failures: [String]
    public var isCapped: Bool
    public var usage: [SessionUsage]

    public init(source: String, savedAt: Date, sessions: [SessionInfo], failures: [String], isCapped: Bool,
                usage: [SessionUsage]) {
        self.source = source
        self.savedAt = savedAt
        self.sessions = sessions
        self.failures = failures
        self.isCapped = isCapped
        self.usage = usage
    }
}

/// The snapshot file, ~/.swe/cache/sessions.json, readable only by you.
public typealias SessionSnapshotFile = SnapshotFile<SessionSnapshot>
