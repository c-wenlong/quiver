import Foundation

/// Which Claude desktop session started another: the desktop app (Code
/// tab) keeps one record per session under
/// `~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/local_<id>.json`,
/// and a session started from another carries `spawnedFrom` with the
/// parent's `local_` id. `cliSessionId` is the transcript id the ledger and
/// swe use, so a Claude session row joins on it.
///
/// These are the harness's own files, read only. A record is 100KB to 1MB
/// of app state; the few fields used here are cached per file by
/// modification time in `cache/claude-desktop-sessions.json`.
public struct SessionParent: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable {
        /// An agent started it as a side session.
        case side
        /// The user forked the conversation.
        case fork
    }
    /// The parent's desktop id, `local_<uuid>`.
    public var id: String
    public var title: String
    public var kind: Kind

    public init(id: String, title: String, kind: Kind) {
        self.id = id
        self.title = title
        self.kind = kind
    }

    /// The line shown under a session: "From design-systems-repo".
    public var label: String {
        kind == .fork ? "Forked from \(title)" : "From \(title)"
    }
}

public struct ClaudeDesktopSessions: Sendable {
    /// The fields kept from one record.
    struct Record: Codable, Equatable {
        struct SpawnedFrom: Codable, Equatable {
            var sessionId: String?
            var userFork: Bool?
        }
        var sessionId: String?
        var cliSessionId: String?
        var priorCliSessionIds: [String]?
        var title: String?
        var spawnedFrom: SpawnedFrom?
    }

    struct CacheEntry: Codable, Equatable {
        var modified: TimeInterval
        var size: Int
        var record: Record
    }

    public let root: URL
    public let cacheFile: URL?

    public static func defaultRoot(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appending(path: "Library/Application Support/Claude/claude-code-sessions")
    }

    public init(root: URL, cacheFile: URL? = nil) {
        self.root = root
        self.cacheFile = cacheFile
    }

    /// Each child transcript id (current and prior) to its parent. A
    /// parent whose record is gone has no title to show, so it is left
    /// out.
    public func parents() -> [String: SessionParent] {
        let records = load()
        var titles: [String: String] = [:]
        for record in records {
            if let id = record.sessionId, let title = record.title, !title.isEmpty { titles[id] = title }
        }
        var parents: [String: SessionParent] = [:]
        for record in records {
            guard let parentId = record.spawnedFrom?.sessionId, let title = titles[parentId] else { continue }
            let parent = SessionParent(id: parentId, title: title,
                                       kind: record.spawnedFrom?.userFork == true ? .fork : .side)
            for id in [record.cliSessionId].compactMap({ $0 }) + (record.priorCliSessionIds ?? []) {
                parents[id] = parent
            }
        }
        return parents
    }

    /// Every record's kept fields, reading only files that changed since
    /// the cache was written.
    func load() -> [Record] {
        let fm = FileManager.default
        var cache: [String: CacheEntry] = [:]
        if let cacheFile, let data = try? Data(contentsOf: cacheFile) {
            cache = (try? JSONDecoder().decode([String: CacheEntry].self, from: data)) ?? [:]
        }
        var fresh: [String: CacheEntry] = [:]
        var changed = false
        // <account>/<org>/local_<id>.json
        for account in (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? [] {
            for org in (try? fm.contentsOfDirectory(at: account, includingPropertiesForKeys: nil)) ?? [] {
                let files = (try? fm.contentsOfDirectory(
                    at: org, includingPropertiesForKeys: [.contentModificationDateKey, .fileSizeKey])) ?? []
                for file in files where file.lastPathComponent.hasPrefix("local_")
                    && file.pathExtension == "json" {
                    let values = try? file.resourceValues(forKeys: [.contentModificationDateKey, .fileSizeKey])
                    let modified = values?.contentModificationDate?.timeIntervalSince1970 ?? 0
                    let size = values?.fileSize ?? 0
                    if let entry = cache[file.path], entry.modified == modified, entry.size == size {
                        fresh[file.path] = entry
                        continue
                    }
                    changed = true
                    guard let data = try? Data(contentsOf: file),
                          let record = try? JSONDecoder().decode(Record.self, from: data) else { continue }
                    fresh[file.path] = CacheEntry(modified: modified, size: size, record: record)
                }
            }
        }
        if let cacheFile, changed || fresh.count != cache.count {
            try? fm.createDirectory(at: cacheFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? JSONEncoder().encode(fresh).write(to: cacheFile, options: .atomic)
        }
        return fresh.keys.sorted().compactMap { fresh[$0]?.record }
    }
}
