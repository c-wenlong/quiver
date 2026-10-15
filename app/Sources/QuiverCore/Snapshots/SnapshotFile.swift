import Foundation

/// What a snapshot file holds. The version names its shape, so a file
/// written by an older or newer app is ignored rather than misread.
public protocol SnapshotContent: Codable, Sendable {
    static var currentVersion: Int { get }
    var version: Int { get }
}

public protocol SnapshotStoring<Content>: Sendable {
    associatedtype Content: SnapshotContent
    func load() async -> Content?
    func save(_ content: Content) async
}

/// A value a page shows and keeps between visits. Bump `snapshotVersion`
/// whenever its saved shape changes.
public protocol SnapshotValue: Codable, Sendable, Equatable {
    static var snapshotVersion: Int { get }
}

/// A page's value as it last loaded: which swe it came from and when.
public struct Snapshot<Value: SnapshotValue>: SnapshotContent, Equatable {
    public static var currentVersion: Int { Value.snapshotVersion }

    public var version = Self.currentVersion
    /// The swe it came from, as set in Settings (empty for PATH's).
    public var source: String
    public var savedAt: Date
    public var value: Value

    public init(source: String, savedAt: Date, value: Value) {
        self.source = source
        self.savedAt = savedAt
        self.value = value
    }
}

/// A snapshot under ~/.quiver/cache, readable only by you. An actor, so
/// saves happen off the main thread and one at a time.
public actor SnapshotFile<Content: SnapshotContent>: SnapshotStoring {
    let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// Nil when the file is missing, damaged or from another version.
    /// Nonisolated so a model can hydrate synchronously on init.
    public nonisolated func load() -> Content? {
        guard let data = try? Data(contentsOf: url),
              let content = try? JSONDecoder().decode(Content.self, from: data),
              content.version == Content.currentVersion
        else { return nil }
        return content
    }

    public func save(_ content: Content) {
        // Reference-date seconds, so a date comes back bit-for-bit equal.
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .deferredToDate
        guard let data = try? encoder.encode(content) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                     attributes: [.posixPermissions: 0o700])
        // Created private, then renamed into place, so the file is never
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
