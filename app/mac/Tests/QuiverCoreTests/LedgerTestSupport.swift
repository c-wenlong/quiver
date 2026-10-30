import Foundation
import Testing
@testable import QuiverCore

/// A scratch directory that is removed when the test value goes away.
final class TempDir: @unchecked Sendable {
    let url: URL

    init() throws {
        url = FileManager.default.temporaryDirectory
            .appendingPathComponent("quiver-ledger-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: url) }

    /// Copies `Fixtures/ledger/<name>` into this directory and returns the copy.
    func copyFixture(_ name: String, as target: String? = nil) throws -> URL {
        let source = try ledgerFixtureURL(name)
        let destination = url.appendingPathComponent(target ?? name)
        try FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: source, to: destination)
        return destination
    }

    func path(_ relative: String) -> URL { url.appendingPathComponent(relative) }
}

func utc(_ text: String) -> Date {
    try! Date(text, strategy: Date.ISO8601FormatStyle())
}

let october2026 = DateInterval(start: utc("2026-10-01T00:00:00Z"), end: utc("2026-11-01T00:00:00Z"))
let allTime = DateInterval(start: utc("2000-01-01T00:00:00Z"), end: utc("2100-01-01T00:00:00Z"))

extension FileManager {
    func append(_ text: String, to url: URL) throws {
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(text.utf8))
    }
}

extension SearchStore {
    /// The rowids of the indexed messages an FTS query matches, in order.
    func ftsRowIDs(_ query: String) throws -> [Int64] {
        let statement = try db.prepare(
            "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rowid")
        statement.bind([.text(query)])
        var ids: [Int64] = []
        while try statement.step() { ids.append(statement.int(0)) }
        return ids
    }
}
