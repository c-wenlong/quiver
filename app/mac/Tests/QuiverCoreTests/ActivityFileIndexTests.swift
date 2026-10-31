import Foundation
import Testing
@testable import QuiverCore

@Suite struct ActivityFileIndexTests {
    /// A counter safe to capture from @Sendable parse closures.

    private func file(_ dir: TempDir, _ name: String, _ body: String) throws -> String {
        let url = dir.url.appending(path: name)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try body.write(to: url, atomically: true, encoding: .utf8)
        return url.path
    }

    private func session(_ id: String) -> @Sendable (String) throws -> [ActivityFileIndex.IndexedSession] {
        { _ in [.init(sessionId: id, accountHome: "/h", cwd: nil, timestamps: [1])] }
    }

    @Test func anUnchangedFileIsNotReparsed() async throws {
        let dir = try TempDir()
        let path = try file(dir, "a.jsonl", "one")
        let parses = Box(0)
        let parse: @Sendable (String) throws -> [ActivityFileIndex.IndexedSession] = { p in
            parses.value += 1
            return [.init(sessionId: p, accountHome: "/h", cwd: nil, timestamps: [1])]
        }
        let index = ActivityFileIndex(url: dir.url.appending(path: "cache/source.json"))
        let stamp = ActivityFileIndex.stamp(URL(fileURLWithPath: path))!
        _ = try await index.sessions(files: { [path: stamp] }, force: true, parse: parse)
        _ = try await index.sessions(files: { [path: stamp] }, force: true, parse: parse)
        #expect(parses.value == 1)
    }

    @Test func aChangedFileIsReparsedAndAVanishedOneDropped() async throws {
        let dir = try TempDir()
        let path = try file(dir, "a.jsonl", "one")
        let index = ActivityFileIndex(url: dir.url.appending(path: "cache/source.json"))
        _ = try await index.sessions(
            files: { [path: ActivityFileIndex.stamp(URL(fileURLWithPath: path))!] },
            force: true, parse: session("a"))
        // Same path, new content: the stamp differs, so re-parse.
        try "a much longer line".write(toFile: path, atomically: true, encoding: .utf8)
        let reparsed = try await index.sessions(
            files: { [path: ActivityFileIndex.stamp(URL(fileURLWithPath: path))!] },
            force: true, parse: session("a2"))
        #expect(reparsed.map(\.sessionId) == ["a2"])
        // Removed: the session drops out.
        try FileManager.default.removeItem(atPath: path)
        let after = try await index.sessions(files: { [:] }, force: true, parse: session("x"))
        #expect(after.isEmpty)
    }

    @Test func theIndexPersistsAcrossInstances() async throws {
        let dir = try TempDir()
        let path = try file(dir, "a.jsonl", "one")
        let url = dir.url.appending(path: "cache/source.json")
        let stamp = ActivityFileIndex.stamp(URL(fileURLWithPath: path))!
        let first = ActivityFileIndex(url: url)
        _ = try await first.sessions(files: { [path: stamp] }, force: true, parse: session("s1"))
        let parses = Box(0)
        let second = ActivityFileIndex(url: url)
        let sessions = try await second.sessions(files: { [path: stamp] }, force: true) { @Sendable _ in
            parses.value += 1
            return [ActivityFileIndex.IndexedSession]()
        }
        #expect(sessions.map(\.sessionId) == ["s1"])
        #expect(parses.value == 0)
    }

    @Test func theThrottleHoldsForThirtySecondsAndForceBypassesIt() async throws {
        let dir = try TempDir()
        let path = try file(dir, "a.jsonl", "one")
        let now = Box(Date(timeIntervalSince1970: 1_000))
        let index = ActivityFileIndex(url: dir.url.appending(path: "cache/s.json"),
                                      now: { now.value })
        let stamp = ActivityFileIndex.stamp(URL(fileURLWithPath: path))!
        let parseCalls = Box(0)
        let parse: @Sendable (String) throws -> [ActivityFileIndex.IndexedSession] = { _ in
            parseCalls.value += 1
            return [.init(sessionId: "s", accountHome: "/h", cwd: nil, timestamps: [2])]
        }
        _ = try await index.sessions(files: { [path: stamp] }, force: true, parse: parse)
        // First refresh done; drop the file and re-query inside the
        // window: cached sessions still answer, no re-parse.
        try FileManager.default.removeItem(atPath: path)
        now.value += 10
        let cached = try await index.sessions(files: { [:] }, parse: parse)
        #expect(cached.count == 1)
        #expect(parseCalls.value == 1)
        // force ignores the throttle and drops the vanished file.
        let forced = try await index.sessions(files: { [:] }, force: true, parse: parse)
        #expect(forced.isEmpty)
        #expect(parseCalls.value == 1)
        // Past the window a plain query refreshes too.
        now.value += 31
        let later = try await index.sessions(files: { [:] }, parse: parse)
        #expect(later.isEmpty)
    }
}

extension ActivityFileIndexTests {
    @Test func aCorruptFileIsSkippedAndRetriedAfterTheThrottle() async throws {
        let dir = try TempDir()
        let good = try file(dir, "good.jsonl", "one")
        let bad = try file(dir, "bad.jsonl", "corrupt")
        let clock = Box(Date(timeIntervalSince1970: 1_000))
        let index = ActivityFileIndex(url: dir.url.appending(path: "cache/s.json"),
                                      now: { clock.value })
        let attempts = Box(0)
        let parse: @Sendable (String) throws -> [ActivityFileIndex.IndexedSession] = { path in
            if path.hasSuffix("bad.jsonl") {
                attempts.value += 1
                struct Corrupt: Error {}
                throw Corrupt()
            }
            return [.init(sessionId: path, accountHome: "/h", cwd: nil, timestamps: [1])]
        }
        var files: [String: ActivityFileIndex.FileStamp] = [:]
        for p in [good, bad] { files[p] = ActivityFileIndex.stamp(URL(fileURLWithPath: p)) }
        // The corrupt file fails alone; the good one still answers.
        let sessions = try await index.sessions(files: { files }, force: true, parse: parse)
        #expect(sessions.map(\.sessionId) == [good])
        #expect(attempts.value == 1)
        // After the throttle the bad file is parsed again.
        clock.value += 31
        _ = try await index.sessions(files: { files }, parse: parse)
        #expect(attempts.value == 2)
    }
}

extension ActivityFileIndexTests {
    @Test func theThrottleSkipsFileDiscoveryToo() async throws {
        let dir = try TempDir()
        let discoveries = Box(0)
        let clock = Box(Date(timeIntervalSince1970: 1_000))
        let index = ActivityFileIndex(url: dir.url.appending(path: "cache/s.json"),
                                      now: { clock.value })
        let parse: @Sendable (String) throws -> [ActivityFileIndex.IndexedSession] = { _ in [] }
        let files: @Sendable () -> [String: ActivityFileIndex.FileStamp] = {
            discoveries.value += 1
            return [:]
        }
        _ = try await index.sessions(files: files, force: true, parse: parse)
        _ = try await index.sessions(files: files, parse: parse)
        // Inside the window the directory walk never runs.
        #expect(discoveries.value == 1)
        _ = try await index.sessions(files: files, force: true, parse: parse)
        #expect(discoveries.value == 2)
        clock.value += 31
        _ = try await index.sessions(files: files, parse: parse)
        #expect(discoveries.value == 3)
    }
}
