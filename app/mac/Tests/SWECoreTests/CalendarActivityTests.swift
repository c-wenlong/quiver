import Foundation
import Testing
@testable import SWECore

/// The ledger's activity-block query: consecutive responses of one session
/// join into a block until a gap splits them.
@Suite struct CalendarActivityTests {
    /// The TempDir stays alive for the test: it deletes the database file
    /// when the test ends.
    private func store(_ events: [UsageEvent]) throws -> (TempDir, LedgerStore) {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.db.execute("BEGIN IMMEDIATE")
        for (i, event) in events.enumerated() {
            try store.upsert(LedgerRecord(key: "k\(i)", event: event))
        }
        try store.db.execute("COMMIT")
        return (temp, store)
    }

    private func event(_ ts: String, harness: String = "claude",
                       home: String = "/h", session: String = "s",
                       model: String = "m", output: Int = 200) -> UsageEvent {
        UsageEvent(harness: harness, accountHome: home, sessionId: session,
                   model: model, timestamp: utc(ts), outputTokens: output)
    }

    private let day = DateInterval(start: utc("2026-10-05T00:00:00Z"),
                                   end: utc("2026-10-06T00:00:00Z"))

    @Test func responsesInsideTheGapJoinIntoOneBlock() throws {
        let fixture = try store([
            event("2026-10-05T10:00:00Z"),
            // Exactly 30 minutes later still belongs to the same block.
            event("2026-10-05T10:30:00Z"),
            event("2026-10-05T10:35:00Z"),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-05T10:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-05T10:35:00Z"))
        #expect(blocks[0].responses == 3)
    }

    @Test func aGapLongerThanTheLimitStartsANewBlock() throws {
        let fixture = try store([
            event("2026-10-05T10:00:00Z"),
            // 30 minutes and a second: a new block.
            event("2026-10-05T10:30:01Z"),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 2)
        #expect(blocks.map(\.responses) == [1, 1])
    }

    @Test func sessionsAndAccountsStayApart() throws {
        let fixture = try store([
            event("2026-10-05T10:00:00Z", session: "a"),
            event("2026-10-05T10:05:00Z", session: "b"),
            event("2026-10-05T10:08:00Z", home: "/other", session: "a"),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 3)
        #expect(Set(blocks.map { "\($0.accountHome)|\($0.sessions[0].sessionId)" })
            == ["/h|a", "/h|b", "/other|a"])
    }

    @Test func aBlockStraddlingTheRangeEdgeStaysWhole() throws {
        let fixture = try store([
            // The first response sits before the day but inside the gap
            // look-back, so its block keeps its real start.
            event("2026-10-04T23:55:00Z"),
            event("2026-10-05T00:05:00Z"),
            // A block that ends inside the look-back but never overlaps
            // the day is dropped.
            event("2026-10-04T23:20:00Z", session: "out"),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-04T23:55:00Z"))
        #expect(blocks[0].end == utc("2026-10-05T00:05:00Z"))
    }

    @Test func aBlockThatBeganHoursBeforeTheRangeKeepsItsTrueStart() throws {
        // Responses every 29 minutes from two hours before the day into
        // it: one long block the `gap`-sized look-back would have cut at
        // the range edge.
        var events: [UsageEvent] = []
        var t = utc("2026-10-04T22:00:00Z")
        while t <= utc("2026-10-05T00:30:00Z") {
            let ts = t
            events.append(UsageEvent(harness: "claude", accountHome: "/h",
                                     sessionId: "s", model: "m",
                                     timestamp: ts, outputTokens: 1))
            t = ts.addingTimeInterval(29 * 60)
        }
        let fixture = try store(events)
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-04T22:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-05T00:25:00Z"))
    }

    @Test func sidechainResponsesCount() throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.db.execute("BEGIN IMMEDIATE")
        try store.upsert(LedgerRecord(key: "k0", event: event("2026-10-05T10:00:00Z")))
        try store.upsert(LedgerRecord(key: "k1", isSidechain: true,
                                      event: event("2026-10-05T10:05:00Z")))
        try store.db.execute("COMMIT")
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 2)
    }

    @Test func theLedgerAnswersThroughTheProtocol() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(directory: temp.path("cache"), pricing: .empty)
        #expect(try await ledger.activityBlocks(in: day, gap: 60, force: false).isEmpty)
        // The existential the CalendarModel holds works the same way.
        let provider: any ActivityProviding = ledger
        #expect(try await provider.activityBlocks(in: day, gap: 60, force: false).isEmpty)
    }

    @Test func aBlockCarriesTheSessionsWorkingDirectory() throws {
        let fixture = try store([
            UsageEvent(harness: "claude", accountHome: "/h", sessionId: "s",
                       cwd: "/work/project", model: "m",
                       timestamp: utc("2026-10-05T10:00:00Z"), outputTokens: 200),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks[0].sessions[0].cwd == "/work/project")
    }
}

extension CalendarActivityTests {
    @Test func oneSessionAcrossTwoDimsIsOneBlock() throws {
        // Alternating models every 10 minutes: two dims interleave, and
        // the merged block spans the hour like the old query's would.
        let (temp, store) = try store((0..<6).map { i in
            UsageEvent(harness: "claude", accountHome: "/h", sessionId: "s",
                       model: i.isMultiple(of: 2) ? "a" : "b",
                       timestamp: utc("2026-10-14T09:\(i)0:00Z"), outputTokens: 1)
        })
        _ = temp
        let blocks = try store.activityBlocks(
            in: DateInterval(start: utc("2026-10-14T00:00:00Z"),
                             end: utc("2026-10-15T00:00:00Z")),
            gap: CalendarModel.gap)
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 6)
        #expect(blocks[0].start == utc("2026-10-14T09:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-14T09:50:00Z"))
    }
}

extension CalendarActivityTests {
    @Test func aHelperOnlySessionDrawsNoBlock() throws {
        // Devin's summarizer/compactor sessions are bookkeeping, not work.
        let (temp, store) = try store([
            event("2026-10-05T11:02:00Z", session: "natural-riverbed",
                  model: "summarizer"),
            event("2026-10-05T11:03:00Z", session: "compacted", model: "compactor"),
            event("2026-10-05T10:00:00Z", session: "shared-kayak"),
        ])
        _ = temp
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.map(\.sessions.first?.sessionId) == ["shared-kayak"])
    }

    @Test func aRealEventOutsideTheRangeKeepsTheSessionHelperBlock() throws {
        // A real response on Oct 5 and a helper response on Oct 10 in one
        // session: the helper block is part of real work, and the day
        // query and the month query must agree.
        let (temp, store) = try store([
            event("2026-10-05T10:00:00Z", session: "shared-kayak"),
            event("2026-10-10T11:02:00Z", session: "shared-kayak",
                  model: "summarizer"),
        ])
        _ = temp
        let narrow = DateInterval(start: utc("2026-10-10T00:00:00Z"),
                                  end: utc("2026-10-11T00:00:00Z"))
        let wide = DateInterval(start: utc("2026-10-01T00:00:00Z"),
                                end: utc("2026-11-01T00:00:00Z"))
        #expect(try store.activityBlocks(in: narrow, gap: 30 * 60).count == 1)
        #expect(try store.activityBlocks(in: wide, gap: 30 * 60).count == 2)
    }

    @Test func helperCallsInsideARealSessionStillCount() throws {
        let (temp, store) = try store([
            event("2026-10-05T10:00:00Z", session: "shared-kayak"),
            event("2026-10-05T10:05:00Z", session: "shared-kayak",
                  model: "summarizer"),
            event("2026-10-05T10:10:00Z", session: "shared-kayak",
                  model: "compactor"),
        ])
        _ = temp
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 3)
    }
}

extension CalendarActivityTests {
    @Test func aZeroTokenSessionDrawsNoBlock() throws {
        // Grok Bot's automation events carry no tokens at all; three of
        // them over ten minutes are still a trivial session.
        let (temp, store) = try store([
            event("2026-10-05T10:00:00Z", harness: "grok-bot", session: "auto", output: 0),
            event("2026-10-05T10:05:00Z", harness: "grok-bot", session: "auto", output: 0),
            event("2026-10-05T10:10:00Z", harness: "grok-bot", session: "auto", output: 0),
            event("2026-10-05T10:00:00Z", session: "real"),
        ])
        _ = temp
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.map(\.sessions.first?.sessionId) == ["real"])
    }

    @Test func aProbeLikeDayInsideALongSessionStays() throws {
        // "long" has two replies four days apart: the day query sees only
        // the second, but the session's whole history spans days, so it
        // is not a probe. "probe" is a one-shot and drops.
        let (temp, store) = try store([
            event("2026-10-01T10:00:00Z", session: "long", output: 10),
            event("2026-10-05T10:00:00Z", session: "long", output: 10),
            event("2026-10-05T11:00:00Z", session: "probe", output: 10),
        ])
        _ = temp
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.map(\.sessions.first?.sessionId) == ["long"])
        // With "Hide accidental sessions" off the probe is back.
        let all = try store.activityBlocks(in: day, gap: 30 * 60, hideTrivial: false)
        #expect(all.map { $0.sessions[0].sessionId }.sorted() == ["long", "probe"])
    }

    @Test func oneRepliesWithoutDetailStillDrawBlocks() throws {
        // Cursor and Droid logs carry no per-reply token detail, so a
        // single reply can be a real answer; only emptiness hides them.
        let (temp, store) = try store([
            event("2026-10-05T10:00:00Z", harness: "cursor", session: "c1", output: 5),
            event("2026-10-05T10:00:00Z", harness: "droid", session: "d1", output: 5),
        ])
        _ = temp
        #expect(try store.activityBlocks(in: day, gap: 30 * 60).count == 2)

        let (temp2, store2) = try self.store([
            event("2026-10-05T10:00:00Z", harness: "cursor", session: "c2", output: 0),
        ])
        _ = temp2
        #expect(try store2.activityBlocks(in: day, gap: 30 * 60).isEmpty)
    }

    @Test func wholeHistoryAggregatesAcrossASessionsDims() throws {
        // "split" switched models mid-run, so its events sit under two
        // dims. Each dim alone looks probe-like; judged as one session
        // the three replies inside a minute are still under the span
        // bound — but three responses is over the reply bound, so the
        // session is real. A per-dim check would wrongly drop it.
        let (temp, store) = try store([
            event("2026-10-05T10:00:00Z", session: "split", model: "opus", output: 10),
            event("2026-10-05T10:00:20Z", session: "split", model: "opus", output: 10),
            event("2026-10-05T10:00:40Z", session: "split", model: "sonnet", output: 10),
        ])
        _ = temp
        #expect(try store.activityBlocks(in: day, gap: 30 * 60)
            .map { $0.sessions[0].sessionId } == ["split"])
    }

    @Test func aProbeSessionDropsAndARealOneStays() throws {
        // Two replies inside a minute with almost nothing back: hidden.
        // Same shape plus a third reply or a minute-long span: real.
        let (temp, store) = try store([
            event("2026-10-05T10:00:00Z", session: "probe", output: 40),
            event("2026-10-05T10:00:30Z", session: "probe", output: 40),
            event("2026-10-05T10:00:00Z", session: "three", output: 40),
            event("2026-10-05T10:00:30Z", session: "three", output: 40),
            event("2026-10-05T10:00:59Z", session: "three", output: 10),
            event("2026-10-05T10:00:00Z", session: "minute", output: 40),
            event("2026-10-05T10:01:00Z", session: "minute", output: 40),
        ])
        _ = temp
        let blocks = try store.activityBlocks(in: day, gap: 30 * 60)
        #expect(blocks.map { $0.sessions[0].sessionId }.sorted() == ["minute", "three"])
    }
}
