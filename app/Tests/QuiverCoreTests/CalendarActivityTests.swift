import Foundation
import Testing
@testable import QuiverCore

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
                       home: String = "/h", session: String = "s") -> UsageEvent {
        UsageEvent(harness: harness, accountHome: home, sessionId: session,
                   model: "m", timestamp: utc(ts), outputTokens: 1)
    }

    private let day = DateInterval(start: utc("2026-10-05T00:00:00Z"),
                                   end: utc("2026-10-06T00:00:00Z"))

    @Test func responsesInsideTheGapJoinIntoOneBlock() throws {
        let fixture = try store([
            event("2026-10-05T10:00:00Z"),
            // Exactly 15 minutes later still belongs to the same block.
            event("2026-10-05T10:15:00Z"),
            event("2026-10-05T10:20:00Z"),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-05T10:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-05T10:20:00Z"))
        #expect(blocks[0].responses == 3)
    }

    @Test func aGapLongerThanTheLimitStartsANewBlock() throws {
        let fixture = try store([
            event("2026-10-05T10:00:00Z"),
            // 15 minutes and a second: a new block.
            event("2026-10-05T10:15:01Z"),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
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
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
        #expect(blocks.count == 3)
        #expect(Set(blocks.map { "\($0.accountHome)|\($0.sessionId)" })
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
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-04T23:55:00Z"))
        #expect(blocks[0].end == utc("2026-10-05T00:05:00Z"))
    }

    @Test func aBlockThatBeganHoursBeforeTheRangeKeepsItsTrueStart() throws {
        // Responses every 14 minutes from two hours before the day into
        // it: one long block the `gap`-sized look-back would have cut at
        // the range edge.
        var events: [UsageEvent] = []
        var t = utc("2026-10-04T22:00:00Z")
        while t <= utc("2026-10-05T00:30:00Z") {
            let ts = t
            events.append(UsageEvent(harness: "claude", accountHome: "/h",
                                     sessionId: "s", model: "m",
                                     timestamp: ts, outputTokens: 1))
            t = ts.addingTimeInterval(14 * 60)
        }
        let fixture = try store(events)
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].start == utc("2026-10-04T22:00:00Z"))
        #expect(blocks[0].end == utc("2026-10-05T00:20:00Z"))
    }

    @Test func sidechainResponsesCount() throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.db.execute("BEGIN IMMEDIATE")
        try store.upsert(LedgerRecord(key: "k0", event: event("2026-10-05T10:00:00Z")))
        try store.upsert(LedgerRecord(key: "k1", isSidechain: true,
                                      event: event("2026-10-05T10:05:00Z")))
        try store.db.execute("COMMIT")
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 2)
    }

    @Test func theLedgerAnswersThroughTheProtocol() async throws {
        let temp = try TempDir()
        let ledger = try Ledger(cacheDirectory: temp.path("cache"), pricing: .empty)
        #expect(try await ledger.activityBlocks(in: day, gap: 60, force: false).isEmpty)
        // The existential the CalendarModel holds works the same way.
        let provider: any ActivityProviding = ledger
        #expect(try await provider.activityBlocks(in: day, gap: 60, force: false).isEmpty)
    }

    @Test func aBlockCarriesTheSessionsWorkingDirectory() throws {
        let fixture = try store([
            UsageEvent(harness: "claude", accountHome: "/h", sessionId: "s",
                       cwd: "/work/project", model: "m",
                       timestamp: utc("2026-10-05T10:00:00Z"), outputTokens: 1),
        ])
        let store = fixture.1
        let blocks = try store.activityBlocks(in: day, gap: 15 * 60)
        #expect(blocks[0].cwd == "/work/project")
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
