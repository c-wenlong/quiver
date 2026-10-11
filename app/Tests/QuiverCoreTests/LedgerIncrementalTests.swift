import Foundation
import Testing
@testable import QuiverCore

/// A synthetic Claude assistant line.
func claudeLine(
    message: String, request: String = "req", session: String = "s1", output: Int = 10,
    input: Int = 1, timestamp: String = "2026-10-05T12:00:00.000Z", model: String = "claude-opus-5-5",
    padding: Int = 0
) -> String {
    let pad = String(repeating: "x", count: padding)
    return #"{"type":"assistant","isSidechain":false,"timestamp":"\#(timestamp)","cwd":"/tmp/p","sessionId":"\#(session)","version":"2.1.300","requestId":"\#(request)","message":{"id":"\#(message)","model":"\#(model)","role":"assistant","content":[{"type":"text","text":"\#(pad)"}],"usage":{"input_tokens":\#(input),"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":\#(output)}}}"#
        + "\n"
}

@Suite struct LedgerIncrementalTests {
    struct Setup {
        let temp: TempDir
        let home: URL
        let file: URL
        let ledger: Ledger
        var roots: LedgerRoots { LedgerRoots(claudeHomes: [home]) }

        init() throws {
            temp = try TempDir()
            home = temp.path("claude")
            file = home.appendingPathComponent("projects/-p/s1.jsonl")
            try FileManager.default.createDirectory(
                at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            ledger = try Ledger(cacheDirectory: temp.path("cache"))
        }

        func write(_ text: String) throws { try Data(text.utf8).write(to: file) }
        func append(_ text: String) throws { try FileManager.default.append(text, to: file) }
        func total() async -> Int { await ledger.totals(in: allTime).totalTokens }
    }

    @Test func anAppendedFileResumesFromItsOffset() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1"))
        var report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRead == 1)
        #expect(await s.total() == 11)

        report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesUnchanged == 1)
        #expect(report.filesRead == 0)

        try s.append(claudeLine(message: "m2", output: 20))
        report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRead == 1)
        #expect(report.filesRescanned == 0)
        #expect(await s.total() == 11 + 21)
    }

    @Test func aHalfWrittenLineIsReadOnceItIsComplete() async throws {
        let s = try Setup()
        let line = claudeLine(message: "m1")
        let cut = line.index(line.startIndex, offsetBy: 40)
        try s.write(String(line[..<cut]))
        try await s.ledger.scan(roots: s.roots)
        #expect(await s.total() == 0)

        try s.append(String(line[cut...]))
        try await s.ledger.scan(roots: s.roots)
        #expect(await s.total() == 11)
    }

    @Test func aReplacedFileIsReadAgainFromTheStart() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1") + claudeLine(message: "m2"))
        try await s.ledger.scan(roots: s.roots)

        // Same size, different content, new inode: an atomic rewrite.
        let replacement = s.temp.path("new.jsonl")
        try Data((claudeLine(message: "m3") + claudeLine(message: "m4")).utf8).write(to: replacement)
        _ = try FileManager.default.replaceItemAt(s.file, withItemAt: replacement)
        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        // m1 and m2 stay: the ledger keeps history the log no longer has.
        #expect(await s.total() == 44)
    }

    @Test func aTruncatedFileIsReadAgainAndAlreadyCountedLinesAreNotDoubled() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1") + claudeLine(message: "m2"))
        try await s.ledger.scan(roots: s.roots)

        let handle = try FileHandle(forWritingTo: s.file)
        try handle.truncate(atOffset: 0)
        try handle.close()
        try s.append(claudeLine(message: "m1"))
        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        #expect(await s.total() == 22)
    }

    @Test func anInPlaceEditBeforeTheOffsetIsCaughtByTheFingerprint() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1") + claudeLine(message: "m2"))
        try await s.ledger.scan(roots: s.roots)

        // Rewrite in place (same inode) with the same length, then append.
        let handle = try FileHandle(forWritingTo: s.file)
        try handle.seek(toOffset: 0)
        try handle.write(contentsOf: Data((claudeLine(message: "m5") + claudeLine(message: "m6")).utf8))
        try handle.close()
        try s.append(claudeLine(message: "m7"))
        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        #expect(await s.total() == 11 * 5)
    }

    @Test func anOversizedLineIsSkippedAndTheRestStillCounts() async throws {
        let temp = try TempDir()
        let home = temp.path("claude")
        let file = home.appendingPathComponent("projects/-p/s1.jsonl")
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let text = claudeLine(message: "m1") + claudeLine(message: "big", padding: 5_000) + claudeLine(message: "m3")
        try Data(text.utf8).write(to: file)
        let ledger = try Ledger(cacheDirectory: temp.path("cache"), maxLineBytes: 2_000)
        let report = try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))
        #expect(report.skippedLines == 1)
        #expect(await ledger.totals(in: allTime).totalTokens == 22)
    }

    @Test func historySurvivesTheHarnessDeletingItsLogsAndAReopenedLedger() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1"))
        try await s.ledger.scan(roots: s.roots)
        try FileManager.default.removeItem(at: s.file)
        try await s.ledger.scan(roots: s.roots)
        #expect(await s.total() == 11)

        let reopened = try Ledger(cacheDirectory: s.temp.path("cache"))
        #expect(await reopened.totals(in: allTime).totalTokens == 11)
    }

    @Test func theReaderStreamsLinesAcrossChunkBoundaries() throws {
        let temp = try TempDir()
        let file = temp.path("lines.jsonl")
        let lines = (0..<50).map { "line-\($0)-" + String(repeating: "y", count: $0 * 7) }
        try Data((lines.joined(separator: "\n") + "\npartial").utf8).write(to: file)
        var seen: [String] = []
        let result = try JSONLReader(chunkSize: 16, maxLineBytes: 1 << 20).read(file, from: 0) { line in
            seen.append(String(decoding: line, as: UTF8.self))
        }
        #expect(seen == lines)
        #expect(result.endOffset == UInt64(lines.joined(separator: "\n").utf8.count + 1))
    }
}
