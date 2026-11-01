import Foundation
import os

/// A per-source cache of what each file produced: path -> stamp and the
/// sessions parsed out of it. Sources stat their files on every query but
/// re-parse only new or changed ones, so a week view never re-reads the
/// whole history. Persisted under ~/.swe/cache/calendar/<source>.json.
public actor ActivityFileIndex {
    /// One session's response times as a file gave them (epoch seconds).
    public struct IndexedSession: Codable, Sendable, Equatable {
        public var sessionId: String
        public var accountHome: String
        public var cwd: String?
        public var timestamps: [Double]

        public init(sessionId: String, accountHome: String,
                    cwd: String?, timestamps: [Double]) {
            self.sessionId = sessionId
            self.accountHome = accountHome
            self.cwd = cwd
            self.timestamps = timestamps
        }
    }

    /// When a file last changed and how big it is: the cheap check that
    /// decides whether its cached sessions still stand.
    public struct FileStamp: Codable, Sendable, Equatable {
        public var mtime: Double
        public var size: Int64
        public init(mtime: Double, size: Int64) {
            self.mtime = mtime
            self.size = size
        }
    }

    private struct FileEntry: Codable, Equatable {
        var mtime: Double
        var size: Int64
        var sessions: [IndexedSession]
    }
    private struct State: Codable, Equatable {
        var files: [String: FileEntry]
    }

    /// How often a source re-stats its files; `force` skips the wait.
    public static let refreshInterval: TimeInterval = 30

    private let url: URL
    private var entries: [String: FileEntry]
    private var lastRefresh = Date.distantPast
    private let now: @Sendable () -> Date

    /// `url` is the source's cache file; a missing or damaged one starts
    /// the index empty.
    public init(url: URL, now: @escaping @Sendable () -> Date = Date.init) {
        self.url = url
        self.now = now
        entries = (try? JSONDecoder().decode(State.self, from: Data(contentsOf: url)))?.files ?? [:]
    }

    /// A plain file's stamp. Sources whose truth lives across files (a db
    /// plus its WAL) fold the extra mtimes into their own stamp.
    public static func stamp(_ url: URL) -> FileStamp? {
        guard let values = try? url.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey]),
              let mtime = values.contentModificationDate
        else { return nil }
        return FileStamp(mtime: mtime.timeIntervalSince1970,
                         size: Int64(values.fileSize ?? 0))
    }

    /// Every session every file holds, re-parsing only files whose stamp
    /// changed since the index last saw them and dropping vanished ones.
    /// Within `refreshInterval` of the last refresh, `force: false` just
    /// returns the cached sessions, and `files` is never invoked, so the
    /// source's directory walk only runs on a real refresh. Persists only
    /// when something changed. `parse` reads one file.
    public func sessions(files: () throws -> [String: FileStamp],
                         force: Bool = false,
                         parse: @Sendable (String) throws -> [IndexedSession]) throws -> [IndexedSession] {
        if !force, now().timeIntervalSince(lastRefresh) < Self.refreshInterval {
            return entries.values.flatMap(\.sessions)
        }
        let files = try files()
        lastRefresh = now()
        var changed = false
        // Drop vanished files.
        for path in entries.keys where files[path] == nil {
            entries[path] = nil
            changed = true
        }
        for (path, stamp) in files {
            if let entry = entries[path],
               entry.mtime == stamp.mtime, entry.size == stamp.size { continue }
            // One bad file must not sink the rest of the source: it stays
            // out of the index, so the next refresh tries it again.
            do {
                let parsed = try parse(path)
                entries[path] = FileEntry(mtime: stamp.mtime, size: stamp.size,
                                          sessions: parsed)
            } catch {
                Logger.calendar.debug("\(path, privacy: .public): parse failed: \(error.localizedDescription, privacy: .public)")
                entries[path] = nil
            }
            changed = true
        }
        if changed { persist() }
        return files.keys.sorted().flatMap { entries[$0]?.sessions ?? [] }
    }

    private func persist() {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .deferredToDate
        guard let data = try? encoder.encode(State(files: entries)) else { return }
        let manager = FileManager.default
        try? manager.createDirectory(at: url.deletingLastPathComponent(),
                                     withIntermediateDirectories: true,
                                     attributes: [.posixPermissions: 0o700])
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
