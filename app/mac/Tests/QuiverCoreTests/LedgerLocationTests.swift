import Foundation
import Testing
@testable import QuiverCore

/// The ledger moves from `cache/ledger` to `data/ledger` once, keeps every
/// row on the way, and takes a rolling daily copy of itself.
@Suite struct LedgerLocationTests {
    private func event(_ i: Int) -> LedgerRecord {
        LedgerRecord(key: "k\(i)", event: UsageEvent(
            harness: "claude", accountHome: "/h", sessionId: "s\(i % 3)", model: "m",
            timestamp: Date(timeIntervalSince1970: 1_790_000_000 + Double(i) * 60), outputTokens: 100 + i))
    }

    private func fill(_ store: LedgerStore, count: Int) throws {
        try store.db.execute("BEGIN IMMEDIATE")
        for i in 0..<count { try store.upsert(event(i)) }
        try store.db.execute("COMMIT")
    }

    private func eventCount(_ url: URL) throws -> Int64 {
        try SQLiteDatabase(readOnly: url).scalar("SELECT count(*) FROM events")
    }

    private func exists(_ url: URL, _ suffix: String = "") -> Bool {
        FileManager.default.fileExists(atPath: url.path + suffix)
    }

    @Test func movesALedgerWhoseRowsAreStillInTheWAL() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        let data = temp.path("data/ledger")
        let old = legacy.appending(path: "ledger.sqlite")
        // What a crash leaves: the files of a ledger that was still open,
        // with nothing checkpointed, so the rows sit in ledger.sqlite-wal
        // and moving the main file alone would lose them.
        let writing = temp.path("writing/ledger.sqlite")
        let store = try LedgerStore(url: writing)
        try store.db.execute("PRAGMA wal_autocheckpoint = 0")
        try fill(store, count: 40)
        try FileManager.default.createDirectory(at: legacy, withIntermediateDirectories: true)
        try withExtendedLifetime(store) {
            for suffix in ["", "-wal", "-shm"] {
                try FileManager.default.copyItem(atPath: writing.path + suffix, toPath: old.path + suffix)
            }
        }
        let walSize = try FileManager.default.attributesOfItem(atPath: old.path + "-wal")[.size] as? Int ?? 0
        #expect(walSize > 0)
        let mainOnly = temp.path("main-only.sqlite")
        try FileManager.default.copyItem(at: old, to: mainOnly)
        #expect(((try? eventCount(mainOnly)) ?? 0) < 40)

        let opened = LedgerLocation.prepare(directory: data, legacy: legacy)

        #expect(opened == data)
        let moved = data.appending(path: "ledger.sqlite")
        #expect(try eventCount(moved) == 40)
        // One self-contained file, and nothing left behind.
        #expect(!exists(moved, "-wal"))
        #expect(!exists(old))
        #expect(!exists(old, "-wal"))
        #expect(!exists(old, "-shm"))
        let left = try FileManager.default.contentsOfDirectory(atPath: data.path)
        #expect(left == ["ledger.sqlite"])
        // The moved file opens as the ledger and still has every row.
        let ledger = try LedgerStore(url: moved)
        #expect(try ledger.db.scalar("SELECT count(*) FROM events") == 40)
    }

    @Test func movesSchemaArchivesAlong() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        try fill(LedgerStore(url: legacy.appending(path: "ledger.sqlite")), count: 2)
        try Data("archive".utf8).write(to: legacy.appending(path: "ledger.schema-0.sqlite"))

        let data = temp.path("data/ledger")
        #expect(LedgerLocation.prepare(directory: data, legacy: legacy) == data)
        #expect(exists(data.appending(path: "ledger.schema-0.sqlite")))
        #expect(!exists(legacy.appending(path: "ledger.schema-0.sqlite")))
    }

    @Test func anOldLedgerStillOpenElsewhereWaitsForTheNextLaunch() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        let data = temp.path("data/ledger")
        let old = legacy.appending(path: "ledger.sqlite")
        // An older Quiver still running: it could write rows after the copy.
        var running: LedgerStore? = try LedgerStore(url: old)
        try fill(running!, count: 6)

        #expect(LedgerLocation.prepare(directory: data, legacy: legacy) == legacy)
        #expect(!exists(data.appending(path: "ledger.sqlite")))
        #expect(!exists(data.appending(path: ".ledger.sqlite.moving")))
        // It keeps writing to the old ledger, and nothing is lost.
        try running!.db.execute("BEGIN IMMEDIATE")
        try running!.upsert(event(6))
        try running!.db.execute("COMMIT")
        running = nil

        #expect(LedgerLocation.prepare(directory: data, legacy: legacy) == data)
        #expect(try eventCount(data.appending(path: "ledger.sqlite")) == 7)
        #expect(!exists(old))
    }

    @Test func anArchiveLeftBehindMovesAtALaterLaunch() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        let data = temp.path("data/ledger")
        try fill(LedgerStore(url: data.appending(path: "ledger.sqlite")), count: 1)
        try FileManager.default.createDirectory(at: legacy, withIntermediateDirectories: true)
        try Data("left".utf8).write(to: legacy.appending(path: "ledger.schema-0.sqlite"))
        try Data("here".utf8).write(to: data.appending(path: "ledger.schema-0.sqlite"))

        #expect(LedgerLocation.prepare(directory: data, legacy: legacy) == data)
        #expect(!exists(legacy.appending(path: "ledger.schema-0.sqlite")))
        // The name was taken, so the moved one gets a suffix.
        #expect(try Data(contentsOf: data.appending(path: "ledger.schema-0.1.sqlite")) == Data("left".utf8))
        #expect(try Data(contentsOf: data.appending(path: "ledger.schema-0.sqlite")) == Data("here".utf8))
    }

    @Test func aFailedMoveKeepsReadingTheOldFileUntouched() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        let old = legacy.appending(path: "ledger.sqlite")
        try fill(LedgerStore(url: old), count: 12)
        let before = try Data(contentsOf: old)
        // A file where the new directory should go: it can't be created.
        try FileManager.default.createDirectory(at: temp.path("data"), withIntermediateDirectories: true)
        try Data("in the way".utf8).write(to: temp.path("data/ledger"))

        let opened = LedgerLocation.prepare(directory: temp.path("data/ledger"), legacy: legacy)

        #expect(opened == legacy)
        #expect(try Data(contentsOf: old) == before)
        #expect(try eventCount(old) == 12)
    }

    @Test func aFailedCopyLeavesNoPartialFile() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        let old = legacy.appending(path: "ledger.sqlite")
        try fill(LedgerStore(url: old), count: 5)
        let before = try Data(contentsOf: old)
        // A directory where the copy's final name goes: the rename fails
        // after the copy is made.
        let data = temp.path("data/ledger")
        try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: data.appending(path: "ledger.sqlite"), withIntermediateDirectories: true)
        // The target "exists", so prepare leaves both alone; drive move directly.
        #expect(throws: (any Error).self) {
            try LedgerLocation.move(old, to: data.appending(path: "ledger.sqlite"))
        }
        #expect(!exists(data.appending(path: ".ledger.sqlite.moving")))
        #expect(try Data(contentsOf: old) == before)
    }

    @Test func anExistingNewLedgerWinsAndTheOldOneIsLeftAlone() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let legacy = temp.path("cache/ledger")
        let data = temp.path("data/ledger")
        try fill(LedgerStore(url: legacy.appending(path: "ledger.sqlite")), count: 3)
        try fill(LedgerStore(url: data.appending(path: "ledger.sqlite")), count: 7)

        #expect(LedgerLocation.prepare(directory: data, legacy: legacy) == data)
        #expect(try eventCount(data.appending(path: "ledger.sqlite")) == 7)
        #expect(try eventCount(legacy.appending(path: "ledger.sqlite")) == 3)
    }

    @Test func noOldLedgerOpensTheNewDirectory() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let data = temp.path("data/ledger")
        #expect(LedgerLocation.prepare(directory: data, legacy: temp.path("cache/ledger")) == data)
        #expect(!exists(data))
    }

    @Test func backupsAreDailyAndKeepTheNewestSeven() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let ledger = try Ledger(directory: temp.path("data/ledger"), pricing: .empty)
        try await ledger.ingest((0..<25).map(event))
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        let first = utc("2026-10-01T09:00:00Z")

        for day in 0..<9 {
            let now = first.addingTimeInterval(Double(day) * 86_400)
            let copy = try await ledger.backUpIfDue(now: now, calendar: calendar)
            #expect(copy != nil)
            // A second check the same day takes nothing.
            #expect(try await ledger.backUpIfDue(now: now.addingTimeInterval(3_600), calendar: calendar) == nil)
        }

        let backups = ledger.backups
        #expect(backups.directory.path == temp.path("data/ledger/backups").path)
        let names = backups.existing().map(\.lastPathComponent)
        #expect(names == (3...9).reversed().map { "ledger-2026-10-0\($0).sqlite" })
        for copy in backups.existing() {
            #expect(try eventCount(copy) == 25)
            #expect(!exists(copy, "-wal"))
        }
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: backups.directory.path)
        #expect(leftovers.sorted() == names.sorted())
    }

    @Test func aBackupHasRowsAddedSinceTheLastOne() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let ledger = try Ledger(directory: temp.path("data/ledger"), pricing: .empty)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC")!
        try await ledger.ingest((0..<4).map(event))
        let monday = try #require(try await ledger.backUpIfDue(now: utc("2026-10-05T12:00:00Z"), calendar: calendar))
        try await ledger.ingest((4..<10).map(event))
        let tuesday = try #require(try await ledger.backUpIfDue(now: utc("2026-10-06T12:00:00Z"), calendar: calendar))
        #expect(try eventCount(monday) == 4)
        #expect(try eventCount(tuesday) == 10)
    }
}
