import Foundation
import SQLite3

/// A thin wrapper over the system SQLite C API. Not thread-safe: each
/// connection belongs to one actor or one call.
final class SQLiteDatabase: @unchecked Sendable {
    struct Failure: Error, CustomStringConvertible, LocalizedError {
        let code: Int32
        let message: String
        var description: String { "sqlite \(code): \(message)" }
        var errorDescription: String? { description }
    }

    private(set) var handle: OpaquePointer?

    /// A scratch in-memory database: a probe can ask questions of SQLite
    /// without leaving a file behind.
    init() throws {
        try open(":memory:", flags: SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX)
        sqlite3_busy_timeout(handle, 2_000)
    }

    /// Opens (and creates) a read-write database.
    init(url: URL) throws {
        try open(url.path, flags: SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX)
        // Same grace the read-only connections get: without it a WAL
        // reader's snapshot fails the write at once with SQLITE_BUSY.
        sqlite3_busy_timeout(handle, 2_000)
    }

    /// Opens another app's database strictly read-only. Nothing is created,
    /// migrated or checkpointed in it.
    init(readOnly url: URL) throws {
        try open(url.path, flags: SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX)
        sqlite3_busy_timeout(handle, 2_000)
    }

    /// A `file:` URI form of `readOnly`, for paths that need URI params
    /// (for example `?mode=ro` on a live WAL database).
    init(readOnlyURI uri: String) throws {
        try open(uri, flags: SQLITE_OPEN_READONLY | SQLITE_OPEN_URI | SQLITE_OPEN_NOMUTEX)
        sqlite3_busy_timeout(handle, 2_000)
    }

    private func open(_ path: String, flags: Int32) throws {
        var db: OpaquePointer?
        let code = sqlite3_open_v2(path, &db, flags, nil)
        guard code == SQLITE_OK, let db else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "cannot open"
            if let db { sqlite3_close(db) }
            throw Failure(code: code, message: "\(message) (\(path))")
        }
        handle = db
    }

    deinit {
        if let handle { sqlite3_close_v2(handle) }
    }

    /// How long a statement waits out another connection's lock before
    /// failing with SQLITE_BUSY. Every connection opens with two seconds.
    func setBusyTimeout(milliseconds: Int32) {
        sqlite3_busy_timeout(handle, milliseconds)
    }

    func execute(_ sql: String) throws {
        var error: UnsafeMutablePointer<CChar>?
        let code = sqlite3_exec(handle, sql, nil, nil, &error)
        if code != SQLITE_OK {
            let message = error.map { String(cString: $0) } ?? "unknown"
            sqlite3_free(error)
            throw Failure(code: code, message: message)
        }
    }

    func prepare(_ sql: String) throws -> Statement {
        var statement: OpaquePointer?
        let code = sqlite3_prepare_v2(handle, sql, -1, &statement, nil)
        guard code == SQLITE_OK, let statement else { throw lastError(code) }
        return Statement(statement, database: self)
    }

    func lastError(_ code: Int32) -> Failure {
        Failure(code: code, message: String(cString: sqlite3_errmsg(handle)))
    }

    var lastInsertRowID: Int64 { sqlite3_last_insert_rowid(handle) }

    /// Copies the whole database into `destination` with SQLite's online
    /// backup API: one consistent snapshot, rows still in the WAL included,
    /// while other connections keep reading and writing.
    func backUp(into destination: SQLiteDatabase) throws {
        guard let backup = sqlite3_backup_init(destination.handle, "main", handle, "main") else {
            throw destination.lastError(sqlite3_errcode(destination.handle))
        }
        var code = sqlite3_backup_step(backup, -1)
        var retries = 0
        while code == SQLITE_BUSY || code == SQLITE_LOCKED, retries < 50 {
            sqlite3_sleep(100)
            retries += 1
            code = sqlite3_backup_step(backup, -1)
        }
        let finished = sqlite3_backup_finish(backup)
        guard code == SQLITE_DONE else { throw Failure(code: code, message: String(cString: sqlite3_errstr(code))) }
        guard finished == SQLITE_OK else { throw destination.lastError(finished) }
    }

    /// The single integer the query returns, such as a `count(*)`.
    func scalar(_ sql: String) throws -> Int64 {
        let statement = try prepare(sql)
        guard try statement.step() else { throw Failure(code: SQLITE_ERROR, message: "no row: \(sql)") }
        return statement.int(0)
    }
}

final class Statement {
    private let statement: OpaquePointer
    private unowned let database: SQLiteDatabase
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    init(_ statement: OpaquePointer, database: SQLiteDatabase) {
        self.statement = statement
        self.database = database
    }

    deinit { sqlite3_finalize(statement) }

    @discardableResult
    func bind(_ values: [SQLiteValue?]) -> Statement {
        sqlite3_reset(statement)
        sqlite3_clear_bindings(statement)
        for (offset, value) in values.enumerated() {
            let index = Int32(offset + 1)
            switch value {
            case nil, .null?: sqlite3_bind_null(statement, index)
            case .int(let v)?: sqlite3_bind_int64(statement, index, v)
            case .double(let v)?: sqlite3_bind_double(statement, index, v)
            case .text(let v)?: sqlite3_bind_text(statement, index, v, -1, Self.transient)
            case .blob(let v)?:
                _ = v.withUnsafeBytes { raw in
                    sqlite3_bind_blob(statement, index, raw.baseAddress, Int32(v.count), Self.transient)
                }
            }
        }
        return self
    }

    /// Advances to the next row; false when done.
    func step() throws -> Bool {
        let code = sqlite3_step(statement)
        switch code {
        case SQLITE_ROW: return true
        case SQLITE_DONE: return false
        default: throw database.lastError(code)
        }
    }

    func run() throws {
        while try step() {}
        sqlite3_reset(statement)
    }

    func isNull(_ column: Int32) -> Bool { sqlite3_column_type(statement, column) == SQLITE_NULL }
    func int(_ column: Int32) -> Int64 { sqlite3_column_int64(statement, column) }
    func double(_ column: Int32) -> Double { sqlite3_column_double(statement, column) }
    func text(_ column: Int32) -> String? {
        guard let pointer = sqlite3_column_text(statement, column) else { return nil }
        return String(cString: pointer)
    }
    func blob(_ column: Int32) -> Data? {
        guard let pointer = sqlite3_column_blob(statement, column) else { return nil }
        return Data(bytes: pointer, count: Int(sqlite3_column_bytes(statement, column)))
    }
}

enum SQLiteValue {
    case null
    case int(Int64)
    case double(Double)
    case text(String)
    case blob(Data)
}

extension SQLiteValue {
    static func int(_ value: Int) -> SQLiteValue { .int(Int64(value)) }
    static func optionalDouble(_ value: Double?) -> SQLiteValue { value.map(SQLiteValue.double) ?? .null }
}

/// FNV-1a, 64-bit: a hash that is the same in every process, unlike `Hasher`.
enum StableHash {
    static func of<S: Sequence>(_ bytes: S) -> UInt64 where S.Element == UInt8 {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in bytes {
            hash ^= UInt64(byte)
            hash = hash &* 0x0000_0100_0000_01b3
        }
        return hash
    }

    static func of(_ text: String) -> Int64 { Int64(bitPattern: of(text.utf8)) }
}
