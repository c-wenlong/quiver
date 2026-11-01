import Foundation
import SQLite3

/// The search index's schema and the version-gated open that creates it.
///
/// Adapted from Orca's `session-search-schema.ts` and
/// `versioned-cache-database.ts`: a versioned *cache* database, rebuilt
/// whenever what is on disk cannot be trusted — a different schema version,
/// a version SQLite cannot report, or a file torn badly enough that opening
/// or recovery fails. Throwing away a bad one costs a re-read and nothing
/// else; refusing to open would strand the feature until someone deleted the
/// file by hand.
enum SearchSchema {
    /// Bump to drop and rebuild. SWE's own version, unrelated to Orca's:
    /// a mismatch unlinks the database and the next open recreates it, so
    /// bump only when the old rows are wrong.
    static let version = 1

    /// The tables and indexes. Every statement is `IF NOT EXISTS`: the DDL
    /// runs on every open, so a later version can add columns or tables
    /// without a rebuild.
    static let tablesSQL = """
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          -- AUTOINCREMENT, because this id names rows in messages for longer
          -- than the session row itself lives: a replace cuts the row loose in
          -- one transaction and the drain reclaims its messages over many. A
          -- plain rowid is reissued as max+1, so a session created inside that
          -- window would be handed a freed id and adopt whatever of the old
          -- conversation the drain had not reached.
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          agent TEXT NOT NULL, session_id TEXT NOT NULL, file_path TEXT NOT NULL,
          title TEXT NOT NULL DEFAULT '', cwd TEXT, cwd_key TEXT,
          created_at TEXT, updated_at TEXT, message_count INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS sessions_agent ON sessions(agent);
        CREATE INDEX IF NOT EXISTS sessions_updated_at ON sessions(updated_at);
        CREATE INDEX IF NOT EXISTS sessions_cwd_key ON sessions(cwd_key);
        CREATE TABLE IF NOT EXISTS files(
          path TEXT PRIMARY KEY, dev INTEGER, ino INTEGER,
          -- -1 = partial: a chunk of a read that never reached the end. Not a
          -- byte offset, so nothing may append to it; the file is re-read whole.
          byte_offset INTEGER NOT NULL,
          mtime_ms REAL NOT NULL, size_bytes INTEGER,
          session_row_id INTEGER,
          -- What this row still owes a reader. 'current': the rows match the
          -- file at the stat recorded here. 'due': the index is behind on a
          -- span it cannot reach by appending, so the next pass reads the file
          -- whole. 'failed': the last read did not commit, and the two columns
          -- below are what stop it being retried for ever.
          state TEXT NOT NULL DEFAULT 'current',
          fail_count INTEGER NOT NULL DEFAULT 0, failed_mtime_ms REAL);
        CREATE INDEX IF NOT EXISTS files_mtime ON files(mtime_ms);
        CREATE TABLE IF NOT EXISTS messages(
          id INTEGER PRIMARY KEY, session_row_id INTEGER NOT NULL,
          role TEXT NOT NULL, ts TEXT);
        -- Both the replace's orphan set and the drain walk a session's rows
        -- through this.
        CREATE INDEX IF NOT EXISTS messages_session ON messages(session_row_id);
        """

    /// The FTS table, separate so a build without FTS5 still gets the rest of
    /// the schema and reports `fts5Available = false` instead of failing open.
    /// unicode61 keeps `_ . - / +` inside tokens so paths and identifiers
    /// match exactly; `+` keeps `C++` a token of its own, and `#` is left out
    /// so `#123` still answers a search for `123`.
    static let ftsSQL = """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
          user_text, assistant_text, tool_text, identifiers,
          tokenize="unicode61 tokenchars '_.-/+'", detail=full);
        """

    /// Opens the index at `url`, creating its directory and file when needed
    /// and rebuilding when what is there cannot be trusted.
    /// - Returns: the open connection and whether FTS5 works on this build.
    static func open(url: URL) throws -> (connection: SQLiteDatabase, fts5Available: Bool) {
        // Transcript text is personal, so only the user reads the index.
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        do {
            return try openExisting(url: url)
        } catch let error as SQLiteDatabase.Failure where isUnusable(error) {
            // One retry only: a second failure on a file just created is not
            // corruption.
            SearchLocation.removeDatabase(at: url)
            return try openExisting(url: url)
        }
    }

    private static func openExisting(url: URL) throws -> (SQLiteDatabase, Bool) {
        var db: SQLiteDatabase? = try SQLiteDatabase(url: url)
        do {
            try applyPragmas(to: db!)
            if try isStale(db!) {
                // DROP TABLE on a multi-GB index takes minutes; unlinking is
                // instant.
                db = nil
                SearchLocation.removeDatabase(at: url)
                db = try SQLiteDatabase(url: url)
                try applyPragmas(to: db!)
            }
            guard let db else {
                throw SQLiteDatabase.Failure(code: SQLITE_CANTOPEN, message: "reopened index is missing")
            }
            try db.execute(tablesSQL)
            let fts5 = probeFTS5(db)
            if fts5 { try db.execute(ftsSQL) }
            try db.execute("""
                INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '\(version)');
                INSERT OR IGNORE INTO meta(key, value) VALUES('index_incarnation', '\(UUID().uuidString)');
                """)
            return (db, fts5)
        } catch {
            db = nil
            throw error
        }
    }

    /// In the order Orca measured: `busy_timeout` first so switching to WAL
    /// waits out another process's lock instead of failing, `auto_vacuum`
    /// before any table exists (it only takes effect on an empty file, and it
    /// is what lets `incremental_vacuum(N)` hand pages back in bounded steps),
    /// then WAL so a reader on another handle sees the last committed state.
    /// The last three are tuning Orca never set: a bigger page cache keeps a
    /// cold build's B-tree inserts off disk, temp tables stay in memory, and
    /// the file is mmap'd so reads skip the page cache copy.
    private static func applyPragmas(to db: SQLiteDatabase) throws {
        try db.execute("PRAGMA busy_timeout = 5000")
        try db.execute("PRAGMA auto_vacuum = INCREMENTAL")
        try db.execute("PRAGMA journal_mode = WAL")
        try db.execute("PRAGMA synchronous = NORMAL")
        try db.execute("PRAGMA journal_size_limit = 8388608")
        try db.execute("PRAGMA cache_size = -64000")
        try db.execute("PRAGMA temp_store = MEMORY")
        try db.execute("PRAGMA mmap_size = 268435456")
    }

    /// Whether what is on disk has to be thrown away. No `meta` table at all
    /// is a file with nothing in it to lose — removing it would make the
    /// first open of every fresh directory a create-remove-create. A meta
    /// table whose version row is missing or unparseable is a damaged cache
    /// rather than a new one: stamping the current version over it would keep
    /// whatever rows the old schema left.
    private static func isStale(_ db: SQLiteDatabase) throws -> Bool {
        let table = try db.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'")
        guard try table.step() else { return false }
        // Scoped: an unfinalized statement that read a row keeps a cursor on
        // meta open, which locks the writes above with SQLITE_LOCKED.
        let stored: String? = try {
            let version = try db.prepare("SELECT value FROM meta WHERE key = 'schema_version'")
            return try version.step() ? version.text(0) : nil
        }()
        return stored.flatMap(Int.init) != version
    }

    /// A smoke test for the one feature the index cannot work without. The
    /// probe table is `temp`, so it never persists.
    static func probeFTS5(_ db: SQLiteDatabase) -> Bool {
        do {
            try db.execute("CREATE VIRTUAL TABLE temp.search_fts5_probe USING fts5(x)")
            try db.execute("DROP TABLE temp.search_fts5_probe")
            return true
        } catch {
            return false
        }
    }

    /// SQLite reports a torn file at the first statement that has to read a
    /// page, so this matches on the message as well as the code.
    private static func isUnusable(_ error: SQLiteDatabase.Failure) -> Bool {
        if error.code == SQLITE_CORRUPT || error.code == SQLITE_NOTADB { return true }
        let message = error.message.lowercased()
        return message.contains("file is not a database") || message.contains("malformed")
    }
}
