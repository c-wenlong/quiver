import Foundation
import SQLite3

/// Cursor's desktop app: each composer chat's messages live in
/// `<user-data-dir>/User/globalStorage/state.vscdb` under
/// `cursorDiskKV` keys `bubbleId:<composerId>:<bubbleId>`. One index
/// entry per database, refreshed only when the db or its WAL changes;
/// `accountHome` is the user-data-dir, so extra Cursor accounts split
/// out on their own legend rows.
public struct CursorAppActivity: ActivityProviding {
    private let source: FileActivitySource

    /// `userDataDirs` are the account roots: the app's own
    /// `~/Library/Application Support/Cursor` plus each extra profile's
    /// `desktopData`.
    public init(userDataDirs: @escaping @Sendable () -> [URL],
                index: ActivityFileIndex) {
        source = FileActivitySource(
            harness: "cursor",
            index: index,
            files: {
                // Two account roots can name one database (an extra
                // profile pointed at the app's own folder, a path listed
                // twice); uniqueKeysWithValues trapped on that.
                Dictionary(userDataDirs().compactMap { dir in
                    let db = dir.appending(path: "User/globalStorage/state.vscdb")
                    guard let stamp = Self.stamp(db) else { return nil }
                    return (db.path, stamp)
                }, uniquingKeysWith: { first, _ in first })
            },
            parse: { try Self.parse(database: $0) })
    }

    public func activityBlocks(in range: DateInterval, gap: TimeInterval,
                               force: Bool) async throws -> [ActivityBlock] {
        try await source.activityBlocks(in: range, gap: gap, force: force)
    }

    /// The db's stamp, folded with its WAL's: a live WAL-mode database
    /// changes in the -wal file while the main file's mtime stands still.
    static func stamp(_ db: URL) -> ActivityFileIndex.FileStamp? {
        guard var stamp = ActivityFileIndex.stamp(db) else { return nil }
        let wal = URL(fileURLWithPath: db.path + "-wal")
        if let extra = ActivityFileIndex.stamp(wal) {
            stamp.mtime = max(stamp.mtime, extra.mtime)
            stamp.size += extra.size
        }
        return stamp
    }

    /// One IndexedSession per composer id. Keys without a second ':' and
    /// `task-` composers (subagent tasks) are skipped; `createdAt` may be
    /// an ISO string or epoch milliseconds. The database is opened
    /// strictly read-only and queried once, retrying a single BUSY; the
    /// retry starts the sessions over, so rows stepped before the BUSY
    /// never count twice.
    static func parse(database path: String) throws -> [ActivityFileIndex.IndexedSession] {
        var sessions: [String: [Double]] = [:]
        var lastError: Error?
        for attempt in 0..<2 {
            sessions = [:]
            do {
                let db = try SQLiteDatabase(readOnlyURI:
                    "file:\(path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? path)?mode=ro")
                let statement = try db.prepare(Self.bubbleQuery)
                while try statement.step() {
                    guard let key = statement.text(0),
                          let created = Self.createdAt(of: statement, column: 1),
                          let composer = Self.composer(of: key) else { continue }
                    sessions[composer, default: []].append(created)
                }
                lastError = nil
                break
            } catch let error as SQLiteDatabase.Failure {
                lastError = error
                // WAL readers can collide with a checkpoint; once is enough.
                if error.code == SQLITE_BUSY, attempt == 0 {
                    Thread.sleep(forTimeInterval: 0.1)
                    continue
                }
                throw error
            }
        }
        if let lastError { throw lastError }
        let accountHome = URL(fileURLWithPath: path)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().path
        return sessions.map { composer, timestamps in
            .init(sessionId: composer, accountHome: accountHome, cwd: nil,
                  timestamps: timestamps.sorted())
        }
    }

    /// Every bubble row. A range on the key, not `LIKE 'bubbleId:%'`:
    /// LIKE is case-insensitive, so SQLite can't use the key's index for
    /// it and scanned the whole table, every value blob included, on
    /// every refresh. `;` is the character after `:`.
    static let bubbleQuery = """
        SELECT key, json_extract(value, '$.createdAt')
        FROM cursorDiskKV WHERE key >= 'bubbleId:' AND key < 'bubbleId;'
        """

    /// The composer id out of `bubbleId:<composer>:<bubble>`; nil for
    /// keys that never got a bubble suffix or belong to a subagent task.
    static func composer(of key: String) -> String? {
        let parts = key.split(separator: ":")
        guard parts.count >= 3, parts[0] == "bubbleId" else { return nil }
        let composer = String(parts[1])
        return composer.hasPrefix("task-") ? nil : composer
    }

    /// `createdAt` as epoch seconds, from an ISO string or a millisecond
    /// number (json_extract hands text columns back as doubles too when
    /// they are numeric).
    static func createdAt(of statement: Statement, column: Int32) -> Double? {
        if let text = statement.text(column) {
            if let date = Timestamp.parse(text) { return date.timeIntervalSince1970 }
            // A numeric createdAt comes back as text of the number.
            if let ms = Double(text), ms > 0 { return ms / 1000 }
            return nil
        }
        let raw = statement.double(column)
        return raw > 0 ? raw / 1000 : nil
    }
}
