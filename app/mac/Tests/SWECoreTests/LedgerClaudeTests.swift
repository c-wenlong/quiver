import Foundation
import Testing
@testable import SWECore

@Suite struct LedgerClaudeTests {
    /// The fixture home holds 11 usage lines for 7 responses: msg_A1 streamed
    /// as 3 lines, msg_A3 streamed twice without a request id, a resumed
    /// session that copies msg_A1 and msg_A2, and a sub-agent transcript that
    /// replays msg_A2 under a new request id. Counting lines, or keying on
    /// message id plus request id only when both exist, over-counts it.
    @Test func eachResponseCountsOnceAcrossStreamingResumesAndSubAgents() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = try temp.copyFixture("claude-home")
        let ledger = try Ledger(directory: temp.path("cache"))
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))

        let events = await ledger.events(in: allTime)
        #expect(events.count == 7)
        let october = await ledger.totals(in: october2026)
        // A1 21,130 + A2 21,563 + A3 32 + S1 4,350 + B1 22,094 + B1's advisor 340.
        #expect(october.totalTokens == 69_509)
        #expect(october.outputTokens == 40 + 60 + 30 + 200 + 90 + 40)
        #expect(october.reasoningTokens == 80)
        #expect(october.cacheWriteTokens == 1_000 + 500 + 100)

        // The resumed copies of A1 and A2 stay with the session that first logged them.
        let sessions = await ledger.bySession(in: october2026)
        let first = try #require(sessions.first { $0.sessionId == "11111111-1111-4111-8111-111111111111" })
        #expect(first.totals.totalTokens == 21_130 + 21_563 + 32 + 4_350)
        let resumed = try #require(sessions.first { $0.sessionId == "22222222-2222-4222-8222-222222222222" })
        #expect(resumed.totals.totalTokens == 22_094 + 340)
        #expect(resumed.models == ["claude-opus-5", "claude-opus-5-5"])
        #expect(resumed.cwd == "/tmp/demo")
        #expect(resumed.harness == "claude")
        #expect(resumed.accountHome == home.path)
    }

    @Test func scanningTwiceAddsNothing() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = try temp.copyFixture("claude-home")
        let ledger = try Ledger(directory: temp.path("cache"))
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))
        #expect(await ledger.events(in: allTime).count == 7)
        #expect(await ledger.totals(in: october2026).totalTokens == 69_509)
    }

    @Test func aSubAgentReplaySeenBeforeItsParentGivesWayToTheParent() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = try temp.copyFixture("claude-home")
        let ledger = try Ledger(directory: temp.path("cache"))
        // Only the sub-agent transcript exists at first.
        let main = home.appendingPathComponent("projects/-tmp-demo/11111111-1111-4111-8111-111111111111.jsonl")
        let parked = temp.path("parked.jsonl")
        try FileManager.default.moveItem(at: main, to: parked)
        try FileManager.default.removeItem(at: home.appendingPathComponent("projects/-tmp-demo/22222222-2222-4222-8222-222222222222.jsonl"))
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))
        let replay = await ledger.events(in: october2026).filter { $0.model == "claude-opus-5-5" }
        #expect(replay.count == 1)

        try FileManager.default.moveItem(at: parked, to: main)
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))
        let a2 = await ledger.events(in: october2026).filter { $0.cacheWriteTokens == 500 }
        #expect(a2.count == 1)
        #expect(await ledger.totals(in: october2026).totalTokens == 21_130 + 21_563 + 32 + 4_350)
    }

    @Test func twoAccountHomesAreKeptApart() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let personal = try temp.copyFixture("claude-home", as: "personal")
        let work = temp.path("work/home")
        try FileManager.default.createDirectory(at: work.appendingPathComponent("projects/-tmp-w"), withIntermediateDirectories: true)
        try FileManager.default.copyItem(
            at: try ledgerFixtureURL("claude-home/projects/-tmp-legacy/33333333-3333-4333-8333-333333333333.jsonl"),
            to: work.appendingPathComponent("projects/-tmp-w/s.jsonl"))
        // The same response in a second home is still one response.
        let ledger = try Ledger(directory: temp.path("cache"))
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [personal, work]))
        let accounts = await ledger.byAccount(in: allTime)
        #expect(accounts[personal.path]?.totalTokens == 69_509 + 200)
        #expect(accounts[work.path] == nil)
    }
}
