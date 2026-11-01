import Foundation
import Testing
@testable import SWECore

/// Every line of a fixture file, as raw bytes.
func fixtureLines(_ relativePath: String) throws -> [Data] {
    let url = try ledgerFixtureURL(relativePath)
    let data = try Data(contentsOf: url)
    return data.split(separator: UInt8(ascii: "\n")).map { Data($0) }
}

func ledgerFixtureURL(_ relativePath: String) throws -> URL {
    let base = try #require(Bundle.module.url(forResource: "Fixtures", withExtension: nil))
    return base.appendingPathComponent("ledger").appendingPathComponent(relativePath)
}

@Suite struct ClaudeLogParserTests {
    let mainSession = "claude-home/projects/-tmp-demo/11111111-1111-4111-8111-111111111111.jsonl"

    @Test func anAssistantLineBecomesOneEventWithDisjointTokenKinds() throws {
        let lines = try fixtureLines(mainSession)
        // The third streamed line of msg_A1 carries the final output count.
        let records = ClaudeLogParser.records(fromLine: lines[3], accountHome: "/h/.claude")
        let record = try #require(records.first)
        #expect(records.count == 1)
        #expect(record.key == "claude:msg_A1:req_A1")
        #expect(record.messageId == "msg_A1")
        #expect(record.isSidechain == false)

        let event = record.event
        #expect(event.harness == "claude")
        #expect(event.accountHome == "/h/.claude")
        #expect(event.sessionId == "11111111-1111-4111-8111-111111111111")
        #expect(event.cwd == "/tmp/demo")
        #expect(event.model == "claude-opus-5-5")
        #expect(event.timestamp == ISO8601DateFormatter().date(from: "2026-10-03T10:00:03Z"))
        #expect(event.inputTokens == 10)
        // Thinking tokens are already inside output_tokens; split them out so the
        // five kinds never overlap.
        #expect(event.outputTokens == 40)
        #expect(event.reasoningTokens == 80)
        #expect(event.cacheReadTokens == 20_000)
        #expect(event.cacheWriteTokens == 1_000)
        #expect(event.cacheWrite1hTokens == 1_000)
        #expect(event.totalTokens == 21_130)
        #expect(event.costUSD == nil)
        #expect(event.serviceTier == nil)
    }

    @Test func linesWithoutUsageOrWithZeroUsageOrBrokenJSONAreSkipped() throws {
        let lines = try fixtureLines(mainSession)
        #expect(ClaudeLogParser.records(fromLine: lines[0], accountHome: "/h").isEmpty) // user prompt
        #expect(ClaudeLogParser.records(fromLine: lines[8], accountHome: "/h").isEmpty) // <synthetic> error
        #expect(ClaudeLogParser.records(fromLine: lines[9], accountHome: "/h").isEmpty) // truncated
    }

    @Test func absurdCountsAreCappedSoTheirSumCannotTrap() throws {
        // `1e19` once clamped to Int.max, and `totalTokens` trapped adding
        // it; the cursor was never saved, so every launch crashed again.
        let line = Data(#"{"type":"assistant","timestamp":"2026-10-03T10:00:03Z","sessionId":"s","requestId":"r","message":{"id":"m","model":"claude-opus-5","usage":{"input_tokens":1e19,"output_tokens":5,"cache_read_input_tokens":1e30}}}"#.utf8)
        let record = try #require(ClaudeLogParser.records(fromLine: line, accountHome: "/h").first)
        #expect(record.event.inputTokens == JSONValue.maxTokens)
        #expect(record.event.cacheReadTokens == JSONValue.maxTokens)
        #expect(record.event.totalTokens == 2 * JSONValue.maxTokens + 5)
    }

    @Test func aLineWithoutRequestIdIsKeyedByMessageIdAlone() throws {
        let lines = try fixtureLines(mainSession)
        let record = try #require(ClaudeLogParser.records(fromLine: lines[6], accountHome: "/h").first)
        #expect(record.key == "claude:msg_A3")
    }

    @Test func advisorIterationsBecomeTheirOwnEventsUnderTheirOwnModel() throws {
        let lines = try fixtureLines("claude-home/projects/-tmp-demo/22222222-2222-4222-8222-222222222222.jsonl")
        let records = ClaudeLogParser.records(fromLine: lines[3], accountHome: "/h")
        #expect(records.map(\.key) == ["claude:msg_B1:req_B1", "claude:msg_B1:req_B1:advisor:0"])
        #expect(records[0].event.totalTokens == 22_094)
        #expect(records[1].event.model == "claude-opus-5")
        #expect(records[1].event.inputTokens == 300)
        #expect(records[1].event.outputTokens == 40)
    }

    @Test func aSidechainLineIsFlaggedAndALoggedCostIsKept() throws {
        let sub = try fixtureLines("claude-home/projects/-tmp-demo/11111111-1111-4111-8111-111111111111/subagents/agent-a1.jsonl")
        let replay = try #require(ClaudeLogParser.records(fromLine: sub[2], accountHome: "/h").first)
        #expect(replay.isSidechain)
        #expect(replay.messageId == "msg_A2")

        let legacy = try fixtureLines("claude-home/projects/-tmp-legacy/33333333-3333-4333-8333-333333333333.jsonl")
        let logged = try #require(ClaudeLogParser.records(fromLine: legacy[0], accountHome: "/h").first)
        #expect(logged.event.costUSD == 0.25)
    }

    @Test func fastModeIsRecordedAsTheServiceTier() throws {
        let line = Data(#"{"type":"assistant","timestamp":"2026-10-03T10:00:00Z","sessionId":"s","requestId":"r","message":{"id":"m","model":"claude-opus-5","usage":{"input_tokens":1,"output_tokens":1,"speed":"fast"}}}"#.utf8)
        let record = try #require(ClaudeLogParser.records(fromLine: line, accountHome: "/h").first)
        #expect(record.event.serviceTier == "fast")
    }
}
