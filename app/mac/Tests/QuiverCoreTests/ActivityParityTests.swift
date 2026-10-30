import Foundation
import Testing
@testable import QuiverCore

/// The Calendar's file sources against made-up logs whose blocks the web
/// app's Python twin (`tests/test_webapp_calendar_sources.py`) checks
/// from the same `Fixtures/activity/expected.json`: a parsing rule changed
/// on one side fails the other side's copy of this test.
@Suite struct ActivityParityTests {
    struct Expected: Decodable {
        struct Source: Decodable {
            var root: String
            var account: String
            var blocks: [Block]
        }
        struct Block: Decodable {
            var session: String
            var cwd: String?
            var start: Double
            var end: Double
            var responses: Int
        }
        var range: [Double]
        var gap: Double
        var sources: [String: Source]
    }

    private static func base() throws -> URL {
        try #require(Bundle.module.url(forResource: "Fixtures", withExtension: nil))
            .appendingPathComponent("activity")
    }

    private static func expected() throws -> Expected {
        try JSONDecoder().decode(
            Expected.self, from: Data(contentsOf: base().appendingPathComponent("expected.json")))
    }

    private func provider(_ harness: String, root: URL, account: String,
                          cache: TempDir) throws -> any ActivityProviding {
        let index = ActivityFileIndex(url: cache.url.appending(path: "\(harness).json"))
        switch harness {
        case "pi": return PiActivity(sessionsRoot: root, accountHome: account, index: index)
        case "droid": return DroidActivity(sessionsRoot: root, accountHome: account, index: index)
        default:
            Issue.record("no provider for \(harness)")
            throw CancellationError()
        }
    }

    @Test(arguments: ["pi", "droid"])
    func blocksMatchTheSharedExpectation(_ harness: String) async throws {
        let expected = try Self.expected()
        let source = try #require(expected.sources[harness])
        let root = try Self.base().appendingPathComponent(source.root)
        let cache = try TempDir()
        let range = DateInterval(start: Date(timeIntervalSince1970: expected.range[0]),
                                 end: Date(timeIntervalSince1970: expected.range[1]))
        let blocks = try await provider(harness, root: root, account: source.account, cache: cache)
            .activityBlocks(in: range, gap: expected.gap, force: true)
        // A Pi session id is the enumerated file path; compare it relative
        // to the root, both sides resolved.
        let prefix = root.resolvingSymlinksInPath().path + "/"
        let got = blocks.map { block -> (session: String, block: ActivityBlock) in
            let id = block.sessions[0].sessionId
            let resolved = id.hasPrefix("/")
                ? URL(fileURLWithPath: id).resolvingSymlinksInPath().path : id
            return (resolved.hasPrefix(prefix) ? String(resolved.dropFirst(prefix.count)) : id, block)
        }.sorted {
            $0.session != $1.session ? $0.session < $1.session : $0.block.start < $1.block.start
        }
        #expect(got.count == source.blocks.count, "\(got.map(\.session))")
        for (actual, want) in zip(got, source.blocks) {
            #expect(actual.session == want.session)
            #expect(actual.block.harness == harness)
            #expect(actual.block.accountHome == source.account)
            #expect(actual.block.sessions[0].cwd == want.cwd)
            #expect(abs(actual.block.start.timeIntervalSince1970 - want.start) < 0.001)
            #expect(abs(actual.block.end.timeIntervalSince1970 - want.end) < 0.001)
            #expect(actual.block.responses == want.responses)
        }
    }
}
