import Foundation
import os

/// The GitHub pages' row store, in SQLite: every surface's rows keyed
/// by (repo, surface, key) — `key` is the row's `id` stringified, so a
/// pull number, a run id, a tag name and a branch name all fit — with
/// `pos` replaying the order the board wrote them in. `fetched` marks
/// a surface whose fetch ever succeeded, separating "asked, got none"
/// from "never asked" the way `.loaded([])` does from `.idle`.
///
/// Two write modes match how the entities live upstream: `remember`
/// upserts and trims for rows that accumulate past the fetch window —
/// pulls, issues and runs keep their history — while `replace` rewrites
/// the surface wholesale for list state — a deleted tag, release or
/// branch must disappear, so nothing unseen survives.
///
/// Where the JSON snapshot rewrote the whole file on every reply, the
/// database touches only the rows a reply moved, so a save is atomic
/// and bounded regardless of how much history accumulated. A store that
/// cannot open degrades to `openError` and empty reads — the pages then
/// simply fetch as if nothing were remembered.
final class GitHubHistory: @unchecked Sendable {
    /// Rows kept per repo per accumulating surface. Reads stay instant,
    /// and even heavy repos' recent history fits well under it.
    static let cap = 500

    private static let log = Logger(subsystem: "com.cwenlong.quiver", category: "gh-history")

    /// The open error, when the database could not be opened — the
    /// store reports it once and answers empty instead of throwing
    /// into every read and write.
    let openError: String?
    private var db: SQLiteDatabase?
    private let lock = NSLock()
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    init(url: URL) {
        do {
            // Titles and bodies from private repos: the folder 0700 and
            // the database 0600 before SQLite opens it, since its -wal and
            // -shm take the database file's mode. One an older build made
            // 0644 is tightened, its side files too.
            try PrivateFile.folder(url.deletingLastPathComponent())
            let manager = FileManager.default
            if !manager.fileExists(atPath: url.path) {
                manager.createFile(atPath: url.path, contents: nil,
                                   attributes: [.posixPermissions: 0o600])
            }
            for path in [url.path, url.path + "-wal", url.path + "-shm"]
            where manager.fileExists(atPath: path) {
                try? manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
            }
            let db = try SQLiteDatabase(url: url)
            try db.execute("""
                PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;
                PRAGMA busy_timeout = 5000;
                CREATE TABLE IF NOT EXISTS history (
                    repo TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    key TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (repo, surface, key));
                CREATE INDEX IF NOT EXISTS history_order
                    ON history (repo, surface, pos);
                CREATE TABLE IF NOT EXISTS fetched (
                    repo TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    PRIMARY KEY (repo, surface));
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL);
                """)
            self.db = db
            openError = nil
            migrateV1()
        } catch {
            db = nil
            openError = error.localizedDescription
        }
    }

    /// The first schema kept `pulls`/`issues` tables keyed by number;
    /// fold them into `history`, mark the repos fetched, and drop them.
    /// `pos` ranks rows newest-first inside their repo so the merged
    /// ordering survives the move.
    private func migrateV1() {
        lock.lock()
        defer { lock.unlock() }
        guard let db,
              ((try? db.scalar(
                  "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'pulls'")) ?? 0) > 0
        else { return }
        do {
            try db.execute("BEGIN IMMEDIATE")
            for surface in ["pulls", "issues"] {
                guard (try db.scalar("""
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type = 'table' AND name = '\(surface)'
                    """)) > 0 else { continue }
                try db.execute("""
                    INSERT OR IGNORE INTO history (repo, surface, key, pos, payload)
                    SELECT src.repo, '\(surface)', CAST(src.number AS TEXT), (
                        SELECT COUNT(*) FROM \(surface) newer
                        WHERE newer.repo = src.repo AND newer.number > src.number),
                        src.payload
                    FROM \(surface) src;
                    INSERT OR IGNORE INTO fetched (repo, surface)
                    SELECT DISTINCT repo, '\(surface)' FROM \(surface);
                    DROP TABLE \(surface);
                    """)
            }
            try db.execute("COMMIT")
        } catch {
            try? db.execute("ROLLBACK")
            Self.log.error("v1 history migration failed: \(error)")
        }
    }

    /// A surface's remembered rows — accumulating surfaces rank by
    /// numeric key (id order is age order); list surfaces replay the
    /// written `pos` order.
    func rows<Row: Codable>(repo: String, surface: Surface) -> [Row] {
        lock.lock()
        defer { lock.unlock() }
        let order = surface.accumulates ? "CAST(key AS INTEGER) DESC" : "pos"
        guard let db,
              let statement = try? db.prepare("""
                  SELECT payload FROM history
                  WHERE repo = ? AND surface = ? ORDER BY \(order)
                  """)
        else { return [] }
        statement.bind([.text(repo), .text(surface.rawValue)])
        var out: [Row] = []
        while (try? statement.step()) == true {
            if let text = statement.text(0),
               let row = try? decoder.decode(Row.self, from: Data(text.utf8)) {
                out.append(row)
            }
        }
        return out
    }

    /// The repo's dashboard board as the store last saw it: a surface
    /// whose write ever landed comes back `.loaded` — pages collapses
    /// its single row into the optional the board expects — while one
    /// never fetched stays `.idle` and asks the network on first look.
    func board(repo: String) -> RepoBoard {
        var board = RepoBoard()
        if fetched(repo: repo, surface: .pulls) {
            board.pulls = .loaded(rows(repo: repo, surface: .pulls))
        }
        if fetched(repo: repo, surface: .issues) {
            board.issues = .loaded(rows(repo: repo, surface: .issues))
        }
        if fetched(repo: repo, surface: .runs) {
            board.runs = .loaded(rows(repo: repo, surface: .runs))
        }
        if fetched(repo: repo, surface: .pages) {
            let site: [GhPages] = rows(repo: repo, surface: .pages)
            board.pages = .loaded(site.first)
        }
        if fetched(repo: repo, surface: .releases) {
            board.releases = .loaded(rows(repo: repo, surface: .releases))
        }
        if fetched(repo: repo, surface: .tags) {
            board.tags = .loaded(rows(repo: repo, surface: .tags))
        }
        if fetched(repo: repo, surface: .branches) {
            board.branches = .loaded(rows(repo: repo, surface: .branches))
        }
        return board
    }

    /// Upsert the merged listing for an accumulating surface and trim
    /// to the newest `cap` — the table only ever holds a repo's newest
    /// window of history.
    func remember<Row: Codable & Identifiable>(repo: String, surface: Surface, rows: [Row]) {
        write(repo: repo, surface: surface, rows: rows, accumulate: true) {
            String(describing: $0.id)
        }
    }

    /// Rewrite a state-list surface wholesale — what the reply did not
    /// list no longer exists. `key` lets a non-Identifiable row name
    /// itself (the Pages site's single "site" row).
    func replace<Row: Codable & Identifiable>(repo: String, surface: Surface, rows: [Row]) {
        write(repo: repo, surface: surface, rows: rows, accumulate: false) {
            String(describing: $0.id)
        }
    }

    func replace<Row: Codable>(repo: String, surface: Surface, rows: [Row],
                             key: (Row) -> String) {
        write(repo: repo, surface: surface, rows: rows, accumulate: false, key: key)
    }

    /// Whether a JSON-era snapshot history import already committed.
    /// Once true it stays true: a snapshot that still carries the field
    /// because its save failed must not re-import stale rows over
    /// newer ones.
    var importedLegacy: Bool { meta("legacy_imported") }

    /// Whether a snapshot's saved boards already moved in. Tracked
    /// apart from `importedLegacy`: a database made by the
    /// pulls/issues-only build carries that flag yet never imported
    /// boards, and skipping them here would lose those surfaces when
    /// the snapshot stops writing them.
    var boardsImported: Bool { meta("boards_imported") }

    private func meta(_ key: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return (try? db?.scalar(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key = '\(key)'")) == 1
    }

    /// One-shot import of what a JSON-era snapshot carried — the legacy
    /// history field plus every loaded board surface — in a single
    /// transaction. `INSERT OR IGNORE` keeps rows the live store already
    /// knows — those are newer than anything a leftover snapshot could
    /// hold — and the flag marks the import done even if the snapshot
    /// itself never saves again.
    @discardableResult
    func importAll(_ history: [String: RepoHistory], boards: [String: RepoBoard]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let db else { return false }
        do {
            try db.execute("BEGIN IMMEDIATE")
            for (repo, rows) in history {
                try importRows(rows.pulls, repo: repo, surface: .pulls, db: db)
                try importRows(rows.issues, repo: repo, surface: .issues, db: db)
                try importRows(rows.runs, repo: repo, surface: .runs, db: db)
            }
            for (repo, board) in boards {
                if case .loaded(let rows) = board.pulls {
                    try importRows(rows, repo: repo, surface: .pulls, db: db)
                }
                if case .loaded(let rows) = board.issues {
                    try importRows(rows, repo: repo, surface: .issues, db: db)
                }
                if case .loaded(let rows) = board.runs {
                    try importRows(rows, repo: repo, surface: .runs, db: db)
                }
                // `.loaded(nil)` is fetched state too — the marker must
                // land so the repo does not re-ask Pages it lacks.
                if case .loaded(let site) = board.pages {
                    try importRows(site.map { [$0] } ?? [], repo: repo,
                                   surface: .pages, db: db) { _ in "site" }
                }
                if case .loaded(let rows) = board.releases {
                    try importRows(rows, repo: repo, surface: .releases, db: db)
                }
                if case .loaded(let rows) = board.tags {
                    try importRows(rows, repo: repo, surface: .tags, db: db)
                }
                if case .loaded(let rows) = board.branches {
                    try importRows(rows, repo: repo, surface: .branches, db: db)
                }
            }
            try db.execute("""
                INSERT OR REPLACE INTO meta (key, value)
                VALUES ('legacy_imported', '1'), ('boards_imported', '1')
                """)
            try db.execute("COMMIT")
            return true
        } catch {
            try? db.execute("ROLLBACK")
            Self.log.error("legacy history import failed: \(error)")
            return false
        }
    }

    /// Forget a repo outright — an unpin drops every remembered row and
    /// every fetched marker, so a re-pin starts from a fetch.
    func remove(repo: String) {
        lock.lock()
        defer { lock.unlock() }
        guard let db else { return }
        for table in ["history", "fetched"] {
            try? db.prepare("DELETE FROM \(table) WHERE repo = ?")
                .bind([.text(repo)]).run()
        }
    }

    // MARK: - internals

    /// Surface identifiers — the board's own names, `actions` filed
    /// under `runs` for the payload it carries.
    enum Surface: String {
        case pulls, issues, runs, pages, releases, tags, branches

        /// Surfaces whose ids are numbers where bigger means newer —
        /// the ordering and the cap trim both rank them by key rather
        /// than by `pos`, so windows written out of order cannot
        /// shuffle or evict newer rows.
        var accumulates: Bool {
            self == .pulls || self == .issues || self == .runs
        }
    }

    /// Whether a write ever landed for the surface — the difference
    /// between `.loaded([])` and `.idle` on a restored board.
    func fetched(repo: String, surface: Surface) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let db,
              let statement = try? db.prepare(
                  "SELECT 1 FROM fetched WHERE repo = ? AND surface = ?")
        else { return false }
        statement.bind([.text(repo), .text(surface.rawValue)])
        return (try? statement.step()) == true
    }

    private func write<Row: Codable>(repo: String, surface: Surface, rows: [Row],
                                     accumulate: Bool, key: (Row) -> String) {
        lock.lock()
        defer { lock.unlock() }
        guard let db else { return }
        do {
            try db.execute("BEGIN IMMEDIATE")
            if !accumulate {
                try db.prepare("DELETE FROM history WHERE repo = ? AND surface = ?")
                    .bind([.text(repo), .text(surface.rawValue)]).run()
            }
            let insert = try db.prepare("""
                INSERT OR REPLACE INTO history (repo, surface, key, pos, payload)
                VALUES (?, ?, ?, ?, ?)
                """)
            for (pos, row) in rows.enumerated() {
                guard let payload = try? encoder.encode(row),
                      let text = String(data: payload, encoding: .utf8)
                else { continue }
                try insert.bind([.text(repo), .text(surface.rawValue),
                                 .text(key(row)), .int(Int64(pos)), .text(text)]).run()
            }
            if accumulate {
                // Whatever the merged list did not keep no longer counts
                // as remembered — ranked by key, so partial writes still
                // evict the oldest rows, never the newest.
                try db.prepare("""
                    DELETE FROM history WHERE repo = ? AND surface = ? AND key NOT IN (
                        SELECT key FROM history WHERE repo = ? AND surface = ?
                        ORDER BY CAST(key AS INTEGER) DESC LIMIT ?)
                    """).bind([.text(repo), .text(surface.rawValue), .text(repo),
                               .text(surface.rawValue), .int(Int64(Self.cap))]).run()
            }
            try db.prepare("""
                INSERT OR REPLACE INTO fetched (repo, surface) VALUES (?, ?)
                """).bind([.text(repo), .text(surface.rawValue)]).run()
            try db.execute("COMMIT")
        } catch {
            try? db.execute("ROLLBACK")
            Self.log.error("write(\(repo), \(surface.rawValue)) failed: \(error)")
        }
    }

    private func importRows<Row: Codable & Identifiable>(
        _ rows: [Row], repo: String, surface: Surface, db: SQLiteDatabase
    ) throws {
        try importRows(rows, repo: repo, surface: surface, db: db) {
            String(describing: $0.id)
        }
    }

    private func importRows<Row: Codable>(
        _ rows: [Row], repo: String, surface: Surface, db: SQLiteDatabase,
        key: (Row) -> String
    ) throws {
        let insert = try db.prepare("""
            INSERT OR IGNORE INTO history (repo, surface, key, pos, payload)
            VALUES (?, ?, ?, ?, ?)
            """)
        for (pos, row) in rows.enumerated() {
            guard let payload = try? encoder.encode(row),
                  let text = String(data: payload, encoding: .utf8)
            else { continue }
            try insert.bind([.text(repo), .text(surface.rawValue),
                             .text(key(row)), .int(Int64(pos)), .text(text)]).run()
        }
        try db.prepare("""
            INSERT OR IGNORE INTO fetched (repo, surface) VALUES (?, ?)
            """).bind([.text(repo), .text(surface.rawValue)]).run()
    }
}
