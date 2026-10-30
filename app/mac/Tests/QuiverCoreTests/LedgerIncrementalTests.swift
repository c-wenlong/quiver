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
            ledger = try Ledger(directory: temp.path("cache"))
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

    /// One unreadable transcript must not take the rest of the scan down
    /// with it: it becomes a note, the good file still reads, and a later
    /// scan retries the file once it is readable again.
    @Test func anUnreadableFileSkipsWithANoteAndTheRestStillScans() async throws {
        let s = try Setup()
        // Sorts before s1.jsonl, so the scan hits it first.
        let bad = s.home.appendingPathComponent("projects/-p/aaa-bad.jsonl")
        try Data(claudeLine(message: "m9", request: "r9").utf8).write(to: bad)
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: bad.path)
        try s.write(claudeLine(message: "m1"))

        var report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRead == 1)
        #expect(report.notes.count == 1)
        #expect(report.notes.first?.contains("aaa-bad.jsonl") == true)
        #expect(await s.total() == 11)

        // No cursor was saved: readable again, the next scan picks it up.
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: bad.path)
        report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRead == 1)
        #expect(report.notes.isEmpty)
        #expect(await s.total() == 22)
    }

    /// A cancelled pass is not a completed one: `scan` throws so the
    /// Overview does not count it or clear the error state.
    @Test func aCancelledScanThrowsRatherThanReportingCompletion() async throws {
        let s = try Setup()
        defer { withExtendedLifetime(s) {} }
        try s.write(claudeLine(message: "m1"))
        let ledger = s.ledger
        let roots = s.roots
        // The task cancels itself first, so the check fires with no timing.
        let task = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return try await ledger.scan(roots: roots)
        }
        await #expect(throws: CancellationError.self) { try await task.value }
    }

    /// A rewrite that preserves size and mtime still moves ctime, which the
    /// unchanged check now compares.
    @Test func aMetadataPreservedRewriteIsCaughtByTheChangeTime() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1") + claudeLine(message: "m2"))
        try await s.ledger.scan(roots: s.roots)

        let mtime = try s.file.resourceValues(forKeys: [.contentModificationDateKey])
            .contentModificationDate!
        // Same inode, same length, mtime stamped back: only ctime moves.
        let handle = try FileHandle(forWritingTo: s.file)
        try handle.write(contentsOf: Data((claudeLine(message: "m5") + claudeLine(message: "m6")).utf8))
        try handle.close()
        try FileManager.default.setAttributes([.modificationDate: mtime], ofItemAtPath: s.file.path)

        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        #expect(report.filesUnchanged == 0)
        #expect(await s.total() == 11 * 4)
    }

    /// A same-length edit between the head and tail samples used to pass
    /// the fingerprint; the middle sample catches it.
    @Test func aSameLengthEditBetweenTheEndsIsCaughtByTheFingerprint() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1", padding: 9_000))
        try await s.ledger.scan(roots: s.roots)

        // Flip one byte in the middle of the padding: beyond the head
        // sample and, after the append below, short of the tail sample.
        var data = try Data(contentsOf: s.file)
        let offset = 4_500
        #expect(data[offset] == UInt8(ascii: "x"))
        data[offset] = UInt8(ascii: "y")
        try data.write(to: s.file)
        try s.append(claudeLine(message: "m3"))

        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        #expect(await s.total() == 22)
    }

    /// The samples leave blind spots, so a fully read file that changes
    /// without growing cannot resume at its end: nothing would be read and
    /// the edit would never reach the ledger. It rescans instead.
    @Test func aFullyReadFileWithAHiddenEditRescans() async throws {
        let s = try Setup()
        let lines = claudeLine(message: "m1", padding: 6_000)
            + claudeLine(message: "m2", output: 40, padding: 6_000)
            + claudeLine(message: "m3", padding: 6_000)
        try s.write(lines)
        try await s.ledger.scan(roots: s.roots)
        #expect(await s.total() == 11 + 41 + 11)

        // The edit lands between the middle and tail samples: the same
        // length, so size, both end samples and the middle all still match.
        var text = lines
        let marker = #""output_tokens":40"#
        let at = try #require(text.range(of: marker))
        let byteOffset = text.utf8.distance(from: text.startIndex, to: at.lowerBound)
        let size = text.utf8.count
        // Outside every sample: after the head, outside the centered
        // middle block, before the tail.
        #expect(byteOffset >= 4_096)
        #expect(byteOffset < (size - 4_096) / 2 || byteOffset >= (size + 4_096) / 2)
        #expect(byteOffset < size - 4_096)
        text.replaceSubrange(at, with: #""output_tokens":90"#)
        try s.write(text)

        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        #expect(await s.total() == 11 + 91 + 11)
    }

    /// A cursor whose saved tail reaches past its offset underflowed the
    /// fingerprint arithmetic. It fails the check and the file rescans.
    @Test func aMalformedCursorFailsClosedInsteadOfCrashing() async throws {
        let s = try Setup()
        try s.write(claudeLine(message: "m1") + claudeLine(message: "m2"))
        let info = try #require(FileInfo(path: s.file.path))
        // A second store on the same file, so the row can say anything:
        // an offset short of EOF keeps the unchanged fast path out, and a
        // tail length past it trips the underflow the guard now rejects.
        let store = try LedgerStore(url: s.temp.path("cache/ledger.sqlite"))
        try store.save(FileCursor(
            path: s.file.resolvingSymlinksInPath().path,
            inode: info.inode, size: info.size, modified: info.modified,
            changed: info.changed, offset: 100,
            headHash: StableHash.of(""), headLength: 0,
            tailHash: 0, tailLength: 500,
            state: nil, skippedLines: 0))

        let report = try await s.ledger.scan(roots: s.roots)
        #expect(report.filesRescanned == 1)
        #expect(await s.total() == 22)
    }

    @Test func anOversizedLineIsSkippedAndTheRestStillCounts() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = temp.path("claude")
        let file = home.appendingPathComponent("projects/-p/s1.jsonl")
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let text = claudeLine(message: "m1") + claudeLine(message: "big", padding: 5_000) + claudeLine(message: "m3")
        try Data(text.utf8).write(to: file)
        let ledger = try Ledger(directory: temp.path("cache"), maxLineBytes: 2_000)
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

        let reopened = try Ledger(directory: s.temp.path("cache"))
        #expect(await reopened.totals(in: allTime).totalTokens == 11)
    }

    @Test func theReaderStreamsLinesAcrossChunkBoundaries() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
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
