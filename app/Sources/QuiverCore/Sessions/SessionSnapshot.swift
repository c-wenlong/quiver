import Foundation

/// The Sessions page as it last loaded: swe's list and each session's usage.
/// Saved so the next launch shows it at once while swe lists again.
/// Holds session titles and folders, no credential.
public struct SessionSnapshot: Codable, Sendable, Equatable {
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

public protocol SessionSnapshotStoring: Sendable {
    func load() async -> SessionSnapshot?
    func save(_ snapshot: SessionSnapshot) async
}

/// The snapshot file, ~/.quiver/cache/sessions.json, readable only by you.
/// An actor, so saves happen off the main thread and one at a time.
public actor SessionSnapshotFile: SessionSnapshotStoring {
    let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// Nil when the file is missing, damaged or from another version.
    public func load() -> SessionSnapshot? {
        guard let data = try? Data(contentsOf: url),
              let snapshot = try? JSONDecoder().decode(SessionSnapshot.self, from: data),
              snapshot.version == SessionSnapshot.currentVersion
        else { return nil }
        return snapshot
    }

    public func save(_ snapshot: SessionSnapshot) {
        // Reference-date seconds, so a date comes back bit-for-bit equal.
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .deferredToDate
        guard let data = try? encoder.encode(snapshot) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                     attributes: [.posixPermissions: 0o700])
        // Created private, then renamed into place, so the list is never
        // readable by others even for a moment.
        let temporary = url.deletingLastPathComponent()
            .appending(path: ".\(url.lastPathComponent).\(UUID().uuidString).tmp")
        guard manager.createFile(atPath: temporary.path, contents: data,
                                 attributes: [.posixPermissions: 0o600])
        else { return }
        if rename(temporary.path, url.path) != 0 {
            try? manager.removeItem(at: temporary)
        }
    }
}
