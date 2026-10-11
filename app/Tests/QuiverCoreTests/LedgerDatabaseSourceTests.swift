import Foundation
import Testing
@testable import QuiverCore

/// Builds a database from a SQL fixture, as the harness would have written it.
func makeDatabase(_ fixture: String, at url: URL) throws {
    let sql = try String(contentsOf: try ledgerFixtureURL(fixture), encoding: .utf8)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let db = try SQLiteDatabase(url: url)
    try db.execute(sql)
}

func execute(_ sql: String, on url: URL) throws {
    try SQLiteDatabase(url: url).execute(sql)
}

@Suite struct LedgerOpenCodeTests {
    @Test func assistantMessagesBecomeEventsWithTheirRecordedCost() async throws {
        let temp = try TempDir()
        let db = temp.path("opencode/opencode.db")
        try makeDatabase("opencode.sql", at: db)
        let ledger = try Ledger(cacheDirectory: temp.path("cache"), pricing: .empty)
        try await ledger.scan(roots: LedgerRoots(openCodeDatabases: [db]))

        let events = await ledger.events(in: allTime)
        #expect(events.count == 2)
        let first = try #require(events.first)
        #expect(first.harness == "opencode")
        #expect(first.sessionId == "ses_1")
        #expect(first.cwd == "/tmp/oc")
        #expect(first.accountHome == db.deletingLastPathComponent().path)
        #expect(first.model == "claude-sonnet-4-5")
        #expect(first.timestamp == Date(timeIntervalSince1970: 1_791_100_001))
        #expect(first.inputTokens == 100)
        #expect(first.outputTokens == 300)
        #expect(first.reasoningTokens == 200)
        #expect(first.cacheReadTokens == 1_000)
        #expect(first.costUSD == 0.01)

        let old = events[1]
        #expect(old.outputTokens == 33)
        #expect(old.reasoningTokens == 51)
        #expect(old.totalTokens == 8_695)
        #expect(old.costUSD == nil)
    }

    @Test func updatedAndNewMessagesArePickedUpWithoutDoubleCounting() async throws {
        let temp = try TempDir()
        let db = temp.path("opencode/opencode.db")
        try makeDatabase("opencode.sql", at: db)
        let ledger = try Ledger(cacheDirectory: temp.path("cache"), pricing: .empty)
        try await ledger.scan(roots: LedgerRoots(openCodeDatabases: [db]))
        #expect(await ledger.totals(in: allTime).totalTokens == 1_600 + 8_695)

        try execute("""
            UPDATE message SET time_updated = 1791100009000, data = json_set(data, '$.tokens.input', 40, '$.tokens.output', 2)
            WHERE id = 'msg_a3';
            INSERT INTO message VALUES ('msg_a4', 'ses_1', 1791100010000, 1791100010000,
              '{"role":"assistant","modelID":"gpt-5.4","time":{"created":1791100010000},"tokens":{"input":7,"output":3,"reasoning":0,"cache":{"read":0,"write":0}}}');
            """, on: db)
        try await ledger.scan(roots: LedgerRoots(openCodeDatabases: [db]))
        #expect(await ledger.totals(in: allTime).totalTokens == 1_600 + 8_695 + 42 + 10)
        try await ledger.scan(roots: LedgerRoots(openCodeDatabases: [db]))
        #expect(await ledger.events(in: allTime).count == 4)
    }
}

@Suite struct LedgerDevinTests {
    @Test func eachRequestCountsOnceHoweverManyNodesCopyIt() async throws {
        let temp = try TempDir()
        let db = temp.path("devin/cli/sessions.db")
        try makeDatabase("devin.sql", at: db)
        let ledger = try Ledger(cacheDirectory: temp.path("cache"), pricing: try .bundled())
        try await ledger.scan(roots: LedgerRoots(devinDatabases: [db]))

        let events = await ledger.events(in: allTime)
        #expect(events.map(\.model) == ["swe-2-medium", "claude-fable-5-1-high"])
        let r1 = events[0]
        #expect(r1.harness == "devin")
        #expect(r1.sessionId == "dev-1")
        #expect(r1.cwd == "/tmp/dv")
        #expect(r1.inputTokens == 100)
        #expect(r1.cacheReadTokens == 1_000)
        #expect(r1.cacheWriteTokens == 10)
        #expect(r1.timestamp == utc("2026-10-04T09:00:10Z").addingTimeInterval(0.123456))

        // Devin's own models have no public price; Claude's has one under an effort suffix.
        let totals = await ledger.totals(in: allTime)
        #expect(totals.unpricedModels == ["swe-2-medium"])
        #expect(totals.unpricedTokens == 1_160)
        #expect(totals.costUSD > 0)
    }

    @Test func aRequestFinishedInPlaceIsCountedOnTheNextScan() async throws {
        let temp = try TempDir()
        let db = temp.path("devin/cli/sessions.db")
        try makeDatabase("devin.sql", at: db)
        let ledger = try Ledger(cacheDirectory: temp.path("cache"), pricing: .empty)
        try await ledger.scan(roots: LedgerRoots(devinDatabases: [db]))
        #expect(await ledger.events(in: allTime).count == 2)

        try execute("""
            UPDATE message_nodes SET chat_message = json_set(chat_message, '$.metadata.metrics',
              json('{"input_tokens":5,"output_tokens":5}')) WHERE node_id = 5;
            """, on: db)
        try await ledger.scan(roots: LedgerRoots(devinDatabases: [db]))
        #expect(await ledger.events(in: allTime).count == 3)
        #expect(await ledger.totals(in: allTime).totalTokens == 1_160 + 450 + 10)
    }
}
