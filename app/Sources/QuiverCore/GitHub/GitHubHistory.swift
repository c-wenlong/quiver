import Foundation

/// The focus page's remembered rows, in SQLite: pulls and issues keyed
/// by (repo, number), upserted as replies land. Each row keeps its wire
/// payload as JSON — `GhPr`/`GhIssue` stay the shape's one owner — while
/// the key gives dedupe, newest-first order and per-repo removal.
///
/// Where the JSON snapshot rewrote the whole file on every change, the
/// database touches only the rows a reply moved, so a save is atomic
/// and bounded regardless of how much history accumulated. A store that
/// cannot open degrades to `openError` and empty reads — the page then
/// simply fetches as if nothing were remembered.
final class GitHubHistory: @unchecked Sendable {
    /// Rows kept per repo per surface. Reads stay instant, and even
    /// heavy repos' recent history fits well under it.
    static let cap = 500

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
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            let db = try SQLiteDatabase(url: url)
            try db.execute("""
                PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL;
                PRAGMA busy_timeout = 5000;
                CREATE TABLE IF NOT EXISTS pulls (
                    repo TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (repo, number));
                CREATE TABLE IF NOT EXISTS issues (
                    repo TEXT NOT NULL,
                    number INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (repo, number));
                """)
            self.db = db
            openError = nil
        } catch {
            db = nil
            openError = error.localizedDescription
        }
    }

    /// A repo's remembered pull requests, newest number first.
    func pulls(repo: String) -> [GhPr] { rows(table: "pulls", repo: repo) }

    /// A repo's remembered issues, newest number first.
    func issues(repo: String) -> [GhIssue] { rows(table: "issues", repo: repo) }

    /// Write a surface's merged listing: upsert the newest `cap` rows
    /// and drop whatever fell below, in one transaction — the file
    /// only ever holds a repo's newest window of history.
    func remember(repo: String, pulls: [GhPr]) { remember(repo: repo, table: "pulls", rows: pulls) }
    func remember(repo: String, issues: [GhIssue]) { remember(repo: repo, table: "issues", rows: issues) }

    /// One-shot import of rows a JSON-era snapshot carried; the same
    /// upsert-and-trim write path, so nothing special happens twice.
    func `import`(repo: String, pulls: [GhPr], issues: [GhIssue]) {
        remember(repo: repo, pulls: pulls)
        remember(repo: repo, issues: issues)
    }

    /// Forget a repo outright — an unpin drops every remembered row.
    func remove(repo: String) {
        lock.lock()
        defer { lock.unlock() }
        guard let db else { return }
        for table in ["pulls", "issues"] {
            try? db.prepare("DELETE FROM \(table) WHERE repo = ?")
                .bind([.text(repo)]).run()
        }
    }

    // MARK: - internals

    private func rows<Row: Codable & Identifiable>(table: String, repo: String) -> [Row]
    where Row.ID == Int {
        lock.lock()
        defer { lock.unlock() }
        guard let db,
              let statement = try? db.prepare(
                "SELECT payload FROM \(table) WHERE repo = ? ORDER BY number DESC")
        else { return [] }
        _ = try? statement.bind([.text(repo)])
        var out: [Row] = []
        while (try? statement.step()) == true {
            if let text = statement.text(0),
               let row = try? decoder.decode(Row.self, from: Data(text.utf8)) {
                out.append(row)
            }
        }
        return out
    }

    private func remember<Row: Codable & Identifiable>(repo: String, table: String, rows: [Row])
    where Row.ID == Int {
        lock.lock()
        defer { lock.unlock() }
        guard let db else { return }
        do {
            try db.execute("BEGIN IMMEDIATE")
            let insert = try db.prepare(
                "INSERT OR REPLACE INTO \(table) (repo, number, payload) VALUES (?, ?, ?)")
            for row in rows {
                guard let payload = try? encoder.encode(row),
                      let text = String(data: payload, encoding: .utf8)
                else { continue }
                try insert.bind([.text(repo), .int(row.id), .text(text)]).run()
            }
            // Whatever the merged list did not keep no longer counts
            // as remembered — the table mirrors the newest window.
            try db.prepare("""
                DELETE FROM \(table) WHERE repo = ? AND number NOT IN (
                    SELECT number FROM \(table) WHERE repo = ?
                    ORDER BY number DESC LIMIT ?)
                """).bind([.text(repo), .text(repo), .int(Self.cap)]).run()
            try db.execute("COMMIT")
        } catch {
            try? db.execute("ROLLBACK")
        }
    }
}
