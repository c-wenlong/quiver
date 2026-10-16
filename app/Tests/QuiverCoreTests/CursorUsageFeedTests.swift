import Foundation
import Testing
@testable import QuiverCore

/// The Cursor usage feed: `swe harness usage-events cursor --json` mapped
/// into ledger records, deduplicated across overlapping fetches.
@Suite struct CursorUsageFeedTests {
    let allTime = DateInterval(start: .distantPast, end: .distantFuture)
    let account = "/home/user/Library/Application Support/Cursor"

    private func reply(_ status: String, events: String, code: Int32 = 0) -> CommandResult {
        CommandResult(status: code, stdout: Data("""
            {"schema": 1, "data": {"status": "\(status)", "events": [\(events)]}}
            """.utf8), stderr: "")
    }

    private let twoEvents = """
        {"timestamp": 1791700000.5, "model": "composer-1", "kind": "USAGE_EVENT_KIND_INCLUDED",
         "conversation_id": "conv-1", "headless": false,
         "input_tokens": 1000, "output_tokens": 50,
         "cache_read_tokens": 200, "cache_write_tokens": 30, "cost_usd": 0.04},
        {"timestamp": 1791700100, "model": "grok-bot-default", "kind": "USAGE_EVENT_KIND_FREE_CREDIT",
         "conversation_id": null, "headless": true,
         "input_tokens": 500, "output_tokens": 20,
         "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": null}
        """

    private func feed(_ runner: any CommandRunning, ledger: Ledger,
                      accounts: [CursorUsageFeed.Account]) -> CursorUsageFeed {
        CursorUsageFeed(ledger: ledger, swe: SweClient(runner: runner), accounts: { accounts })
    }

    private func accts() -> [CursorUsageFeed.Account] { [CursorUsageFeed.Account(home: account)] }

    @Test func eventsMapToLedgerRecords() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", events: twoEvents)),
        ])
        await feed(runner, ledger: ledger, accounts: accts()).poll()

        let events = await ledger.events(in: allTime).sorted { $0.timestamp < $1.timestamp }
        #expect(events.count == 2)
        let composer = events[0]
        #expect(composer.harness == "cursor")
        #expect(composer.accountHome == account)
        #expect(composer.sessionId == "conv-1")
        #expect(composer.model == "composer-1")
        #expect(composer.inputTokens == 1000)
        #expect(composer.outputTokens == 50)
        #expect(composer.cacheReadTokens == 200)
        #expect(composer.cacheWriteTokens == 30)
        #expect(composer.costUSD == 0.04)
        let grok = events[1]
        #expect(grok.harness == "grok-bot")
        #expect(grok.sessionId.hasPrefix("cursor-"))
        #expect(grok.costUSD == nil)
        #expect(grok.isSessionTotal == false)
    }

    @Test func anOverlappingRefetchDoesNotDuplicate() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", events: twoEvents)),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts())
        await feed.poll()
        await feed.poll()
        let events = await ledger.events(in: allTime)
        #expect(events.count == 2)
        let totals = await ledger.totals(in: allTime)
        #expect(totals.inputTokens == 1500)
        #expect(totals.costUSD == 0.04)
    }

    @Test func theWindowStartsAt180DaysThenOverlapsTwo() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", events: twoEvents)),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts())

        let before = Date().addingTimeInterval(-180 * 86_400).timeIntervalSince1970
        await feed.poll()
        var arg = runner.calls[0].first { $0.hasPrefix("--since=") }!.dropFirst("--since=".count)
        // First poll asks for the whole 180-day window (--since is whole
        // seconds, so it truncates up to a second).
        #expect((before - 1)...before + 60 ~= Double(arg)!)

        await feed.poll()
        arg = runner.calls[1].first { $0.hasPrefix("--since=") }!.dropFirst("--since=".count)
        // Later polls resume from the newest stored event minus two days.
        let expected = 1791700100 - 2 * 86_400
        #expect(abs(Double(arg)! - Double(expected)) < 2)
    }

    @Test func authRequiredAndSignedOutKeepTheStoredRows() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        try await ledger.ingest([CursorUsageFeed.record(for: CursorUsageEvent(
            timestamp: 1791700000, model: "composer-1", kind: "k", conversationId: "c",
            headless: false, inputTokens: 5, outputTokens: 1,
            cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: nil), accountHome: account)])
        for status in ["auth-required", "signed-out", "unavailable"] {
            let runner = LockedRunner([
                (["harness", "usage-events", "cursor"],
                 // `unavailable` exits non-zero but still answers JSON.
                 reply(status, events: "", code: status == "unavailable" ? 1 : 0)),
            ])
            await feed(runner, ledger: ledger, accounts: accts()).poll()
        }
        let events = await ledger.events(in: allTime)
        #expect(events.count == 1)
        #expect(events[0].inputTokens == 5)
    }

    @Test func anOlderSweIsAskedOnceThenLeftAlone() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"],
             CommandResult(status: 1, stdout: Data(),
                           stderr: "Unknown harness subcommand: usage-events")),
        ])
        let feed = feed(runner, ledger: ledger, accounts: accts())
        await feed.poll()
        await feed.poll()
        #expect(runner.calls.count == 1)
    }

    @Test func extraAccountsPassTheirAccountDir() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let runner = LockedRunner([
            (["harness", "usage-events", "cursor"], reply("ok", events: "")),
        ])
        let extra = CursorUsageFeed.Account(home: "/t/cursor-side/desktop",
                                            accountDir: "/t/cursor-side/desktop")
        await feed(runner, ledger: ledger, accounts: accts() + [extra]).poll()
        #expect(runner.calls.contains {
            $0.contains("--account-dir=/t/cursor-side/desktop") })
        #expect(runner.calls.contains { !$0.contains { $0.hasPrefix("--account-dir") } })
    }

    @Test func grokBotIsOnTheCalendarButCursorIsNot() async throws {
        #expect(Ledger.calendarHarnesses.contains("grok-bot"))
        #expect(!Ledger.calendarHarnesses.contains("cursor"))
        #expect(HarnessMark("grok-bot") == HarnessMark(icon: "grok", word: nil, name: "Grok Bot"))

        // End to end: an ingested grok-bot event becomes an activity block.
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        try await ledger.ingest([CursorUsageFeed.record(for: CursorUsageEvent(
            timestamp: Date().timeIntervalSince1970 - 60, model: "grok-bot-cua",
            kind: "k", conversationId: "c9", headless: true,
            inputTokens: 10, outputTokens: 5,
            cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0.001), accountHome: account)])
        let range = DateInterval(start: .now.addingTimeInterval(-3600), duration: 3600)
        let blocks = try ledger.activityBlocks(in: range, gap: 900, force: true)
        #expect(blocks.contains { $0.harness == "grok-bot" })
    }
}
