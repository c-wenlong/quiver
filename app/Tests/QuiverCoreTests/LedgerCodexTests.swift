import Foundation
import Testing
@testable import QuiverCore

@Suite struct LedgerCodexTests {
    let legacySession = "rollout-2026-10-04T09-00-00-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    let forkSession = "rollout-2026-10-04T09-30-00-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    let recordSession = "rollout-2026-10-05T10-00-00-cccccccc-cccc-4ccc-8ccc-cccccccccccc"

    func parse(_ relative: String) throws -> [LedgerRecord] {
        let url = try ledgerFixtureURL("codex-home/" + relative)
        var parser = CodexFileParser(file: url, accountHome: "/h/.codex", savedState: nil)
        return try fixtureLines("codex-home/" + relative).flatMap { parser.records(fromLine: $0) }
    }

    @Test func tokenCountsBecomePerTurnEventsWithCachedInputSplitOut() throws {
        let records = try parse("sessions/2026/10/04/\(legacySession).jsonl")
        // The repeated snapshot and the info-less rate-limit ping add nothing.
        #expect(records.count == 2)
        let first = records[0].event
        #expect(first.harness == "codex")
        #expect(first.accountHome == "/h/.codex")
        #expect(first.sessionId == legacySession)
        #expect(first.cwd == "/tmp/codex-demo")
        #expect(first.model == "gpt-5.6-sol")
        // Codex's input count includes cached tokens and its output count
        // includes reasoning; both are split so nothing is priced twice.
        #expect(first.inputTokens == 200)
        #expect(first.cacheReadTokens == 800)
        #expect(first.outputTokens == 30)
        #expect(first.reasoningTokens == 20)
        #expect(first.totalTokens == 1_050)
        #expect(first.serviceTier == nil)

        let second = records[1].event
        #expect(second.totalTokens == 1_580)
        #expect(second.inputTokens == 300)
        #expect(second.serviceTier == "priority")
        #expect(records[0].key != records[1].key)
    }

    @Test func aForkSkipsTheParentHistoryItReplays() throws {
        let records = try parse("sessions/2026/10/04/\(forkSession).jsonl")
        #expect(records.map(\.event.totalTokens) == [1_570])
        #expect(records[0].event.sessionId == forkSession)
        #expect(records[0].event.inputTokens == 500)
    }

    @Test func usageRecordsWinOverTokenCountsAndAreKeyedByResponse() throws {
        let records = try parse("archived_sessions/\(recordSession).jsonl")
        #expect(records.map(\.key) == ["codex:resp:resp_1", "codex:resp:resp_2", "codex:resp:resp_1"])
        #expect(records[0].event.model == "gpt-6.1-sol")
        #expect(records[0].event.inputTokens == 1_000)
        #expect(records[0].event.cacheReadTokens == 4_000)
        #expect(records[0].event.outputTokens == 60)
        #expect(records[0].event.reasoningTokens == 40)
        #expect(records[1].event.totalTokens == 300_200)
    }

    @Test func theWholeHomeCountsEachTurnOnce() async throws {
        let temp = try TempDir()
        let home = try temp.copyFixture("codex-home")
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        try await ledger.scan(roots: LedgerRoots(codexHomes: [home]))
        // The legacy rollout is also copied into archived_sessions.
        let totals = await ledger.totals(in: october2026)
        #expect(totals.totalTokens == 1_050 + 1_580 + 1_570 + 5_100 + 300_200)
        #expect(totals.eventCount == 5)
        let sessions = await ledger.bySession(in: october2026)
        #expect(Set(sessions.map(\.sessionId)) == [legacySession, forkSession, recordSession])
        #expect(await ledger.byAccount(in: october2026).keys.sorted() == [home.path])
    }

    @Test func anAppendedRolloutResumesWithItsModelAndTotals() async throws {
        let temp = try TempDir()
        let home = temp.path("codex")
        let file = home.appendingPathComponent("sessions/2026/10/04/\(legacySession).jsonl")
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let lines = try fixtureLines("codex-home/sessions/2026/10/04/\(legacySession).jsonl")
            .map { String(decoding: $0, as: UTF8.self) + "\n" }
        try Data(lines[0..<5].joined().utf8).write(to: file)
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        try await ledger.scan(roots: LedgerRoots(codexHomes: [home]))
        #expect(await ledger.totals(in: october2026).totalTokens == 1_050)

        try FileManager.default.append(lines[5...].joined(), to: file)
        let report = try await ledger.scan(roots: LedgerRoots(codexHomes: [home]))
        #expect(report.filesRescanned == 0)
        let events = await ledger.events(in: october2026)
        #expect(events.map(\.totalTokens) == [1_050, 1_580])
        #expect(events.last?.model == "gpt-5.6-sol")
        #expect(events.last?.serviceTier == "priority")
    }

    @Test func aTokenCountWithoutLastUsageIsTheDeltaOfItsTotals() throws {
        let url = URL(fileURLWithPath: "/tmp/rollout-x.jsonl")
        var parser = CodexFileParser(file: url, accountHome: "/h", savedState: nil)
        func line(_ total: Int, _ cached: Int, _ output: Int, at second: Int) -> Data {
            Data(#"{"timestamp":"2026-10-04T09:00:\#(String(format: "%02d", second)).000Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":\#(total),"cached_input_tokens":\#(cached),"output_tokens":\#(output),"reasoning_output_tokens":0,"total_tokens":\#(total + output)}}}}"#.utf8)
        }
        let first = parser.records(fromLine: line(100, 0, 10, at: 1))
        let second = parser.records(fromLine: line(300, 150, 30, at: 2))
        #expect(first.map(\.event.totalTokens) == [110])
        #expect(second.map(\.event.inputTokens) == [50])
        #expect(second.map(\.event.cacheReadTokens) == [150])
        #expect(second.map(\.event.outputTokens) == [20])
        // A session with no model metadata is priced as gpt-5, as ccusage does.
        #expect(first.first?.event.model == "gpt-5")
    }
}
