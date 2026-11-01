import Foundation
import SQLite3
import os

/// Where the usage ledger lives.
///
/// The ledger is data, not cache: harnesses prune their transcripts (Claude
/// Code after 30 days by default), so its rows can be the only record of
/// that usage. It lives in `~/.swe/data/ledger/`. Earlier builds kept it
/// in `cache/ledger/`, which nothing backs up and Mac cleaners empty.
public enum LedgerLocation {
    static let fileName = "ledger.sqlite"
    private static let log = Logger(subsystem: "com.cwenlong.quiver", category: "ledger")

    /// Moves a ledger left in `legacy` into `directory`, then returns the
    /// directory to open. When `directory` has a ledger already it wins and
    /// the legacy one is left alone. When the move fails, or another Quiver
    /// still has the legacy ledger open, the legacy ledger is opened where
    /// it is and the move is tried again at the next launch: an empty
    /// ledger in `directory` would hide its history and stop every later try.
    public static func prepare(directory: URL, legacy: URL) -> URL {
        let target = directory.appending(path: fileName)
        let source = legacy.appending(path: fileName)
        let files = FileManager.default
        if !files.fileExists(atPath: target.path), files.fileExists(atPath: source.path) {
            do {
                try move(source, to: target)
                log.notice("moved the ledger from \(source.path, privacy: .public) to \(target.path, privacy: .public)")
            } catch {
                log.error("couldn't move the ledger, still reading \(source.path, privacy: .public): \(error)")
                return legacy
            }
        }
        moveArchives(from: legacy, to: directory)
        return directory
    }

    /// Copies through the backup API rather than moving the files, so rows
    /// still in the WAL come along and a copy that fails halfway leaves the
    /// source's rows as they were. The copy is checked table by table and
    /// renamed into place; only then is the source removed.
    ///
    /// The source is held in an exclusive lock from before the copy until
    /// its files are gone, so no other connection can write a row the copy
    /// misses. While any other connection has it open (an older Quiver
    /// still running) taking the lock fails after the busy timeout, and
    /// the move waits.
    static func move(_ source: URL, to target: URL) throws {
        let files = FileManager.default
        let directory = target.deletingLastPathComponent()
        // The ledger names projects and sessions, so only the user reads it.
        try files.createDirectory(at: directory, withIntermediateDirectories: true,
                                  attributes: [.posixPermissions: 0o700])
        let partial = directory.appending(path: ".\(fileName).moving")
        removeDatabase(at: partial)
        let from = try SQLiteDatabase(url: source)
        // In exclusive locking mode the lock outlives the empty transaction
        // that takes it, and is held until `from` closes.
        try from.execute("PRAGMA locking_mode = EXCLUSIVE; BEGIN EXCLUSIVE; COMMIT")
        do {
            try copy(from, to: partial, verify: true)
            try files.moveItem(at: partial, to: target)
        } catch {
            removeDatabase(at: partial)
            throw error
        }
        removeDatabase(at: source)
    }

    /// Schema archives (`ledger.schema-<version>.sqlite`) are history too.
    /// Checked at every launch, not only with the move, so one that failed
    /// to move is tried again. A name taken in `directory` gets a `.N`.
    static func moveArchives(from legacy: URL, to directory: URL) {
        let files = FileManager.default
        let archives = ((try? files.contentsOfDirectory(at: legacy, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.lastPathComponent.hasPrefix("ledger.schema-") && $0.pathExtension == "sqlite" }
        for archive in archives {
            let stem = archive.deletingPathExtension().lastPathComponent
            var destination = directory.appending(path: archive.lastPathComponent)
            var copy = 1
            while files.fileExists(atPath: destination.path) {
                destination = directory.appending(path: "\(stem).\(copy).sqlite")
                copy += 1
            }
            do {
                try files.createDirectory(at: directory, withIntermediateDirectories: true,
                                          attributes: [.posixPermissions: 0o700])
                try files.moveItem(at: archive, to: destination)
            } catch {
                log.error("couldn't move \(archive.path, privacy: .public): \(error)")
            }
        }
    }

    /// `from` as one self-contained file at `destination`, with no WAL
    /// beside it. `verify` checks the copy's integrity and every table's
    /// row count against the source.
    static func copy(_ from: SQLiteDatabase, to destination: URL, verify: Bool) throws {
        try fill(destination, from: from, verify: verify)
        // The copy's connection is closed now. Leaving WAL mode leaves its
        // -shm behind, which a single-file copy has no use for.
        for suffix in ["-wal", "-shm"] { try? FileManager.default.removeItem(atPath: destination.path + suffix) }
    }

    private static func fill(_ destination: URL, from: SQLiteDatabase, verify: Bool) throws {
        let to = try SQLiteDatabase(url: destination)
        try from.backUp(into: to)
        try to.execute("PRAGMA journal_mode = DELETE")
        guard verify else { return }
        let check = try to.prepare("PRAGMA quick_check")
        guard try check.step(), check.text(0) == "ok" else {
            throw SQLiteDatabase.Failure(code: SQLITE_CORRUPT, message: "the copy failed its integrity check")
        }
        let tables = try from.prepare(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        while try tables.step() {
            guard let name = tables.text(0) else { continue }
            let quoted = "\"\(name.replacingOccurrences(of: "\"", with: "\"\""))\""
            let expected = try from.scalar("SELECT count(*) FROM \(quoted)")
            let copied = try to.scalar("SELECT count(*) FROM \(quoted)")
            guard expected == copied else {
                throw SQLiteDatabase.Failure(
                    code: SQLITE_MISMATCH, message: "\(name) has \(expected) rows but the copy has \(copied)")
            }
        }
    }

    /// The database file and the journal files SQLite keeps beside it.
    static func removeDatabase(at url: URL) {
        for suffix in ["", "-wal", "-shm", "-journal"] {
            try? FileManager.default.removeItem(atPath: url.path + suffix)
        }
    }
}

/// One copy of the ledger a day in `directory`, as `ledger-YYYY-MM-DD.sqlite`
/// (the local date), keeping the newest `keep`. Each copy comes from
/// SQLite's online backup API, so it is consistent while scans write.
public struct LedgerBackups: Sendable {
    public let directory: URL
    public let keep: Int

    public init(directory: URL, keep: Int = 7) {
        self.directory = directory
        self.keep = max(1, keep)
    }

    func name(for day: Date, calendar: Calendar) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: day)
        return String(format: "ledger-%04d-%02d-%02d.sqlite", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// The copies on disk, newest first.
    public func existing() -> [URL] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: directory.path)) ?? []
        return names.filter { $0.wholeMatch(of: /ledger-\d{4}-\d{2}-\d{2}\.sqlite/) != nil }
            .sorted(by: >)
            .map { directory.appending(path: $0) }
    }

    /// Copies `database` when `now`'s day has no copy yet, then removes the
    /// oldest past `keep`. Returns the new copy, or nil when none was due.
    @discardableResult
    func backUpIfDue(_ database: SQLiteDatabase, now: Date, calendar: Calendar = .current) throws -> URL? {
        let target = directory.appending(path: name(for: now, calendar: calendar))
        guard !FileManager.default.fileExists(atPath: target.path) else { return nil }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        // A crash mid-copy leaves a hidden partial file, never a dated one
        // that would count toward `keep`.
        let partial = directory.appending(path: ".\(target.lastPathComponent).partial")
        LedgerLocation.removeDatabase(at: partial)
        do {
            try LedgerLocation.copy(database, to: partial, verify: false)
            try FileManager.default.moveItem(at: partial, to: target)
        } catch {
            LedgerLocation.removeDatabase(at: partial)
            throw error
        }
        for old in existing().dropFirst(keep) { LedgerLocation.removeDatabase(at: old) }
        return target
    }
}
