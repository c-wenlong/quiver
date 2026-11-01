import Foundation

/// An `AccountDirectory` keyed to the accounts store's profiles.json
/// modification date. Callers that need a directory per row or per block
/// (the calendar's per-segment label lookups) would otherwise rebuild it
/// on every call — a profiles.json read plus a file-system attribute
/// lookup per home. Here the stamp is a single stat; only a changed
/// file rebuilds.
public final class CachedAccountDirectory: @unchecked Sendable {
    /// The file whose mtime stands in for "the store may have changed":
    /// profiles.json inside the accounts root.
    private let file: URL
    private let build: @Sendable () -> AccountDirectory
    private let lock = NSLock()
    private var cached: (stamp: Date?, directory: AccountDirectory)?

    public init(file: URL, build: @escaping @Sendable () -> AccountDirectory) {
        self.file = file
        self.build = build
    }

    /// The directory for the store's current state: an unchanged stamp
    /// returns the built directory without touching profiles.json.
    public var current: AccountDirectory {
        lock.lock()
        defer { lock.unlock() }
        // attributesOfItem stats afresh; URL.resourceValues can answer
        // from the URL's cached resource values.
        let stamp = (try? FileManager.default.attributesOfItem(atPath: file.path))?[.modificationDate] as? Date
        if let cached, cached.stamp == stamp { return cached.directory }
        let directory = build()
        cached = (stamp, directory)
        return directory
    }
}
