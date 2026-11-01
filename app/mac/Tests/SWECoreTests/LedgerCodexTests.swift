import Foundation
import Testing
@testable import SWECore

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
        defer { withExtendedLifetime(temp) {} }
        let home = try temp.copyFixture("codex-home")
        let ledger = try Ledger(directory: temp.path("cache"))
        try await ledger.scan(roots: LedgerRoots(codexHomes: [home]))
        // The legacy rollout is also copied into archived_sessions.
        let totals = await ledger.totals(in: october2026)
        #expect(totals.totalTokens == 1_050 + 1_580 + 1_570 + 5_100 + 300_200)
        #expect(totals.eventCount == 5)
        let sessions = await ledger.bySession(in: october2026)
        #expect(Set(sessions.map(\.sessionId)) == [legacySession, forkSession, recordSession])
        #expect(await ledger.byAccount(in: october2026).keys.sorted() == [home.path])
        #expect(await ledger.harnessByAccount(in: october2026) == [home.path: "codex"])
    }

    @Test func anAppendedRolloutResumesWithItsModelAndTotals() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = temp.path("codex")
        let file = home.appendingPathComponent("sessions/2026/10/04/\(legacySession).jsonl")
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let lines = try fixtureLines("codex-home/sessions/2026/10/04/\(legacySession).jsonl")
            .map { String(decoding: $0, as: UTF8.self) + "\n" }
        try Data(lines[0..<5].joined().utf8).write(to: file)
        let ledger = try Ledger(directory: temp.path("cache"))
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

    /// A resumed thread replays `total_token_usage` snapshots from however
    /// far back it has to. The dedup used to be a 32-entry ring, so past 32
    /// turns an old snapshot fell out and its usage emitted a second time.
    /// Every seen total is remembered now, across cursor state too.
    @Test func aReplayedSnapshotFromLongAgoIsStillSkipped() throws {
        let url = URL(fileURLWithPath: "/tmp/rollout-x.jsonl")
        var parser = CodexFileParser(file: url, accountHome: "/h", savedState: nil)
        func line(total: Int, last: Int, at second: Int) -> Data {
            Data(#"{"timestamp":"2026-10-04T09:\#(String(format: "%02d", second / 60)):\#(String(format: "%02d", second % 60)).000Z","type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":\#(total),"output_tokens":10,"total_tokens":\#(total + 10)},"last_token_usage":{"input_tokens":\#(last),"output_tokens":10,"total_tokens":\#(last + 10)}}}}"#.utf8)
        }
        for i in 1...40 {
            #expect(parser.records(fromLine: line(total: i * 100, last: 100, at: i)).count == 1)
        }
        // A replay of the first snapshot emits nothing a second time.
        #expect(parser.records(fromLine: line(total: 100, last: 100, at: 55)).isEmpty)
        // ... and still does after a save/restore through cursor state.
        var restored = CodexFileParser(file: url, accountHome: "/h", savedState: parser.savedState())
        #expect(restored.records(fromLine: line(total: 200, last: 100, at: 56)).isEmpty)

        // A state written by the old ring migrates: its entries dedup.
        let legacy = Data(#"{"sessionId":"rollout-x","sawSessionMeta":false,"recentTotals":[{"input":300,"cached":0,"cacheWrite":0,"output":10,"reasoning":0,"total":310}],"usesRecords":false}"#.utf8)
        var migrated = CodexFileParser(file: url, accountHome: "/h", savedState: legacy)
        #expect(migrated.records(fromLine: line(total: 300, last: 100, at: 57)).isEmpty)
    }

    /// A `token_usage_record` that cannot be read (no `usage`, or a timestamp
    /// that does not parse) must not suppress the `token_count` lines after
    /// it: `usesRecords` used to flip before the guard, and the flag persists
    /// in the cursor, so one malformed record silenced every count for good.
    @Test func aMalformedUsageRecordDoesNotSuppressTokenCounts() throws {
        let url = URL(fileURLWithPath: "/tmp/rollout-x.jsonl")
        var parser = CodexFileParser(file: url, accountHome: "/h", savedState: nil)
        let noUsage = Data(#"{"timestamp":"2026-10-04T09:00:01.000Z","type":"token_usage_record","payload":{"turn_id":"turn-1"}}"#.utf8)
        let noTime = Data(#"{"timestamp":"not-a-date","type":"token_usage_record","payload":{"turn_id":"turn-1","usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}}}"#.utf8)
        #expect(parser.records(fromLine: noUsage).isEmpty)
        #expect(parser.records(fromLine: noTime).isEmpty)

        let count = Data(#"{"timestamp":"2026-10-04T09:00:02.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15}}}}"#.utf8)
        #expect(parser.records(fromLine: count).map(\.event.totalTokens) == [15])

        // The flag still flips on the first well-formed record: counts after
        // one are suppressed exactly as before.
        let record = Data(#"{"timestamp":"2026-10-04T09:00:03.000Z","type":"token_usage_record","payload":{"turn_id":"turn-2","response_id":"resp-1","usage":{"input_tokens":20,"output_tokens":7,"total_tokens":27}}}"#.utf8)
        #expect(parser.records(fromLine: record).map(\.event.totalTokens) == [27])
        let laterCount = Data(#"{"timestamp":"2026-10-04T09:00:04.000Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":99,"output_tokens":9,"total_tokens":108}}}}"#.utf8)
        #expect(parser.records(fromLine: laterCount).isEmpty)
    }
}
