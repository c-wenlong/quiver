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

/// A snapshot under ~/.swe/cache, readable only by you. An actor, so
/// saves happen off the main thread and one at a time.
public actor SnapshotFile<Content: SnapshotContent>: SnapshotStoring {
    let url: URL

    public init(url: URL) {
        self.url = url
    }

    /// The lock lives on a sibling that is never renamed into place: a
    /// lock on `url` itself would ride the old inode when a save swaps
    /// the file, and two savers would hold different locks at once.
    /// app + widget + CLI can write the same snapshot; the lock keeps
    /// their writes serialized instead of interleaved.
    private nonisolated var lockPath: String {
        url.deletingLastPathComponent()
            .appending(path: ".\(url.lastPathComponent).lock").path
    }

    /// `body` under an `flock` on the sibling lock file: exclusive for a
    /// save, shared for a load. Advisory and best-effort — when the lock
    /// file can't be opened (the folder does not exist yet) the work runs
    /// anyway; a save's write-then-rename is already atomic per file.
    private nonisolated func locked<T>(exclusive: Bool, _ body: () throws -> T) rethrows -> T {
        let fd = open(lockPath, O_CREAT | O_RDWR | O_CLOEXEC, S_IRUSR | S_IWUSR)
        if fd >= 0 {
            flock(fd, exclusive ? LOCK_EX : LOCK_SH)
            defer { close(fd) }   // closing drops the flock
            return try body()
        }
        return try body()
    }

    /// Nil when the file is missing, damaged or from another version.
    /// Nonisolated so a model can hydrate synchronously on init.
    public nonisolated func load() -> Content? {
        locked(exclusive: false) {
            guard let data = try? Data(contentsOf: url),
                  let content = try? JSONDecoder().decode(Content.self, from: data),
                  content.version == Content.currentVersion
            else { return nil }
            return content
        }
    }

    public func save(_ content: Content) {
        saveNow(content)
    }

    /// The same write, synchronously — for quit paths that cannot await.
    /// The flock on the sibling lock file, not the actor, is what
    /// serializes writers, so a nonisolated save is safe.
    public nonisolated func saveNow(_ content: Content) {
        // Reference-date seconds, so a date comes back bit-for-bit equal.
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .deferredToDate
        guard let data = try? encoder.encode(content) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                     attributes: [.posixPermissions: 0o700])
        locked(exclusive: true) {
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
}
