import Foundation
import Testing
@testable import QuiverCore

/// One fixture per source, written into a scratch directory and read
/// through the provider: the parsers' file conventions and session ids
/// are what swe uses.
@Suite struct ActivitySourcesTests {
    private let range = DateInterval(start: utc("2026-08-10T00:00:00Z"),
                                     end: utc("2026-08-11T00:00:00Z"))

    private func write(_ dir: TempDir, _ name: String, _ body: String) throws -> String {
        let url = dir.url.appending(path: name)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        // JSONLReader only emits newline-terminated lines; keep one.
        try (body + "\n").write(to: url, atomically: true, encoding: .utf8)
        return url.path
    }

    private func index(_ dir: TempDir, _ name: String) -> ActivityFileIndex {
        ActivityFileIndex(url: dir.url.appending(path: "cache/\(name).json"))
    }

    @Test func droidReadsAMessageLog() async throws {
        let dir = try TempDir()
        _ = try write(dir, "sessions/proj/11111111-2222-3333-4444-555555555555.jsonl", """
            {"type":"session_start","id":"11111111-2222-3333-4444-555555555555","cwd":"/work/app"}
            {"type":"message","timestamp":"2026-08-10T08:37:16.298Z"}
            {"type":"message","timestamp":"2026-08-10T08:40:00.000Z"}
            """)
        _ = try write(dir, "sessions/proj/x.settings.json", "{}")
        let provider = DroidActivity(sessionsRoot: dir.url.appending(path: "sessions"),
                                     accountHome: "/factory",
                                     index: index(dir, "droid"))
        let blocks = try await provider.activityBlocks(in: range, gap: CalendarModel.gap,
                                                       force: true)
        let block = try #require(blocks.first)
        #expect(blocks.count == 1)
        #expect(block.harness == "droid")
        #expect(block.sessions[0].sessionId == "11111111-2222-3333-4444-555555555555")
        #expect(block.sessions[0].cwd == "/work/app")
        #expect(block.responses == 2)
        #expect(abs(block.start.timeIntervalSince(utc("2026-08-10T08:37:00Z")) - 16.298) < 0.001)
    }

    @Test func piUsesTheFullFilePathAsTheSessionId() async throws {
        let dir = try TempDir()
        let path = try write(dir, "sessions/proj/1786332436_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl", """
            {"type":"session","id":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee","timestamp":"2026-08-10T08:30:00.000Z","cwd":"/work/pi"}
            {"type":"model_change","timestamp":"2026-08-10T08:31:00.000Z"}
            {"type":"message","timestamp":"2026-08-10T08:32:00.000Z"}
            {"type":"thinking_level_change","timestamp":"2026-08-10T08:33:00.000Z"}
            {"type":"message","timestamp":"2026-08-10T08:34:00.000Z"}
            """)
        let provider = PiActivity(sessionsRoot: dir.url.appending(path: "sessions"),
                                  accountHome: "/pi", index: index(dir, "pi"))
        let blocks = try await provider.activityBlocks(in: range, gap: CalendarModel.gap,
                                                       force: true)
        let block = try #require(blocks.first)
        #expect(blocks.count == 1)
        // Only the two message lines count; model/thinking changes don't.
        #expect(block.responses == 2)
        // swe's Pi session id is the full path; temp dirs resolve through
        // /var -> /private/var, so compare resolved.
        #expect(URL(fileURLWithPath: block.sessions[0].sessionId).resolvingSymlinksInPath()
                == URL(fileURLWithPath: path).resolvingSymlinksInPath())
        #expect(block.sessions[0].cwd == "/work/pi")
    }

    @Test func freebuffReadsMillisecondTimestampsAndTheChatDirId() async throws {
        let dir = try TempDir()
        let body = """
            {"sessionState":{"initialCwd":"/fallback",
             "mainAgentState":{"cwd":"/work/fb","messageHistory":[
               {"sentAt":1784969499622},{"sentAt":1784969520000},{"noTime":true}]}}}
            """
        _ = try write(dir, "projects/p/chats/2026-07-25T08-51-14.778Z/run-state.json", body)
        // A stray run-state.json outside chats/ is not listed.
        _ = try write(dir, "projects/p/run-state.json", body)
        let provider = FreebuffActivity(projectsRoot: dir.url.appending(path: "projects"),
                                        accountHome: "/manicode",
                                        index: index(dir, "freebuff"))
        // 1784969499622 ms is 2026-07-25, outside the August range.
        let july = DateInterval(start: utc("2026-07-01T00:00:00Z"),
                                end: utc("2026-09-01T00:00:00Z"))
        let blocks = try await provider.activityBlocks(in: july, gap: CalendarModel.gap,
                                                       force: true)
        let block = try #require(blocks.first)
        #expect(blocks.count == 1)
        #expect(block.sessions[0].sessionId == "2026-07-25T08-51-14.778Z")
        #expect(block.sessions[0].cwd == "/work/fb")
        #expect(block.responses == 2)
        #expect(abs(block.start.timeIntervalSince1970 - 1784969499.622) < 0.001)
    }

    @Test func freebuffFallsBackToTheInitialCwd() throws {
        let dir = try TempDir()
        let path = try write(dir, "chats/c1/run-state.json", """
            {"sessionState":{"initialCwd":"/fallback",
             "mainAgentState":{"messageHistory":[{"sentAt":1784969499622}]}}}
            """)
        let sessions = try FreebuffActivity.parse(file: path, accountHome: "/m")
        #expect(sessions.first?.cwd == "/fallback")
    }

    @Test func cursorReadsBubbleCreatedAtTimes() async throws {
        let dir = try TempDir()
        let dbURL = dir.url.appending(path: "User/globalStorage/state.vscdb")
        try FileManager.default.createDirectory(
            at: dbURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let db = try SQLiteDatabase(url: dbURL)
        try db.execute("CREATE TABLE cursorDiskKV(key TEXT, value TEXT)")
        try db.execute("""
            INSERT INTO cursorDiskKV VALUES
            ('bubbleId:composer-1:b1', '{"createdAt":"2026-08-10T08:37:16.298Z"}'),
            ('bubbleId:composer-1:b2', '{"createdAt":"2026-08-10T08:40:00.000Z"}'),
            ('bubbleId:composer-2:b1', '{"createdAt":1786352400000}'),
            ('bubbleId:task-call_1:b1', '{"createdAt":"2026-08-10T09:00:00.000Z"}'),
            ('bubbleId:nosuffix', '{"createdAt":"2026-08-10T09:00:00.000Z"}'),
            ('otherKey:x:y', '{"createdAt":"2026-08-10T09:00:00.000Z"}')
            """)
        let provider = CursorAppActivity(userDataDirs: { [dir.url] },
                                         index: index(dir, "cursor"))
        let blocks = try await provider.activityBlocks(in: range, gap: CalendarModel.gap,
                                                       force: true)
        // composer-1's two bubbles join one block; composer-2's numeric
        // createdAt is another; task- keys and colon-less keys are out.
        #expect(blocks.count == 2)
        let one = try #require(blocks.first { $0.sessions[0].sessionId == "composer-1" })
        #expect(one.responses == 2)
        #expect(one.accountHome == dir.url.path)
        #expect(abs(one.start.timeIntervalSince(utc("2026-08-10T08:37:00Z")) - 16.298) < 0.001)
        let two = try #require(blocks.first { $0.sessions[0].sessionId == "composer-2" })
        #expect(two.responses == 1)
        #expect(two.start.timeIntervalSince1970 == 1_786_352_400)
        #expect(blocks.contains { $0.sessions[0].sessionId.hasPrefix("task-") } == false)
    }

    /// The bubble read seeks the key's index (Cursor's table is
    /// `key TEXT UNIQUE`) rather than scanning every row.
    @Test func cursorBubbleQueryUsesTheKeyIndex() throws {
        let dir = try TempDir()
        let db = try SQLiteDatabase(url: dir.url.appending(path: "state.vscdb"))
        try db.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
        let plan = try db.prepare("EXPLAIN QUERY PLAN " + CursorAppActivity.bubbleQuery)
        var details: [String] = []
        while try plan.step() { details.append(plan.text(3) ?? "") }
        #expect(details.contains { $0.hasPrefix("SEARCH") && $0.contains("INDEX") }, "\(details)")
    }

    /// Two account roots naming one database read it once instead of
    /// trapping in Dictionary(uniqueKeysWithValues:).
    @Test func cursorReadsADatabaseListedTwiceOnce() async throws {
        let dir = try TempDir()
        let dbURL = dir.url.appending(path: "User/globalStorage/state.vscdb")
        try FileManager.default.createDirectory(
            at: dbURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let db = try SQLiteDatabase(url: dbURL)
        try db.execute("CREATE TABLE cursorDiskKV(key TEXT, value TEXT)")
        try db.execute("""
            INSERT INTO cursorDiskKV VALUES
            ('bubbleId:composer-1:b1', '{"createdAt":"2026-08-10T08:37:16.298Z"}')
            """)
        let provider = CursorAppActivity(userDataDirs: { [dir.url, dir.url] },
                                         index: index(dir, "cursor"))
        let blocks = try await provider.activityBlocks(in: range, gap: CalendarModel.gap,
                                                       force: true)
        #expect(blocks.map { $0.sessions[0].sessionId } == ["composer-1"])
    }
}

/// Pi and Droid sessions reach the calendar through their own log files,
/// so the ledger's block query never judges them; the file sources take
/// the same whole-history trivial-session drop as a filter on the index,
/// under the same "Hide accidental sessions" setting.
extension ActivitySourcesTests {
    private func ledger(_ events: [UsageEvent], in dir: TempDir) async throws -> Ledger {
        let ledger = try Ledger(directory: dir.path("cache/ledger"), pricing: .empty)
        try await ledger.ingest(events.enumerated().map {
            LedgerRecord(key: "k\($0.offset)", event: $0.element)
        })
        return ledger
    }

    /// Pi's session id is the file path as the index enumerated it, and
    /// temp dirs resolve /var to /private/var along the way; read it back
    /// through an unfiltered provider so the ledger key matches exactly.
    private func piSession(_ dir: TempDir, _ name: String) async throws -> String {
        _ = try write(dir, "sessions/proj/\(name)", """
            {"type":"session","id":"\(name)","timestamp":"2026-08-10T08:30:00.000Z","cwd":"/work/pi"}
            {"type":"message","timestamp":"2026-08-10T08:32:00.000Z"}
            """)
        let probe = PiActivity(sessionsRoot: dir.url.appending(path: "sessions"),
                               accountHome: "/pi", index: index(dir, "pi-probe"))
        let blocks = try await probe.activityBlocks(in: range, gap: CalendarModel.gap,
                                                    force: true)
        return try #require(blocks.first?.sessions.first?.sessionId)
    }

    private func droidFile(_ dir: TempDir, _ session: String) throws -> String {
        try write(dir, "sessions/proj/\(session).jsonl", """
            {"type":"session_start","id":"\(session)","cwd":"/work/app"}
            {"type":"message","timestamp":"2026-08-10T08:37:16.298Z"}
            """)
    }

    private func piProvider(_ dir: TempDir, _ ledger: Ledger) -> PiActivity {
        PiActivity(sessionsRoot: dir.url.appending(path: "sessions"),
                   accountHome: "/pi", index: index(dir, "pi"),
                   filter: { await ledger.filterTrivialActivitySessions($0, harness: "pi") })
    }

    private func droidProvider(_ dir: TempDir, _ ledger: Ledger) -> DroidActivity {
        DroidActivity(sessionsRoot: dir.url.appending(path: "sessions"),
                      accountHome: "/factory", index: index(dir, "droid"),
                      filter: { await ledger.filterTrivialActivitySessions($0, harness: "droid") })
    }

    @Test func aTrivialPiSessionDrawsNoCalendarBlock() async throws {
        let dir = try TempDir()
        let session = try await piSession(dir, "1786332436_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl")
        let ledger = try await ledger([
            UsageEvent(harness: "pi", accountHome: "/pi", sessionId: session,
                       model: "m", timestamp: utc("2026-08-10T08:32:00Z"),
                       outputTokens: 10),
        ], in: dir)
        let blocks = try await piProvider(dir, ledger)
            .activityBlocks(in: range, gap: CalendarModel.gap, force: true)
        #expect(blocks.isEmpty)
    }

    @Test func realPiHistoryOutsideTheRangeKeepsTheBlock() async throws {
        // Only the third reply sits in the queried day; the session's
        // whole history is three replies over days, so it is real work.
        let dir = try TempDir()
        let session = try await piSession(dir, "1786332436_bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl")
        let ledger = try await ledger([
            UsageEvent(harness: "pi", accountHome: "/pi", sessionId: session,
                       model: "m", timestamp: utc("2026-08-05T10:00:00Z"),
                       outputTokens: 10),
            UsageEvent(harness: "pi", accountHome: "/pi", sessionId: session,
                       model: "m", timestamp: utc("2026-08-06T10:00:00Z"),
                       outputTokens: 10),
            UsageEvent(harness: "pi", accountHome: "/pi", sessionId: session,
                       model: "m", timestamp: utc("2026-08-10T08:32:00Z"),
                       outputTokens: 10),
        ], in: dir)
        let blocks = try await piProvider(dir, ledger)
            .activityBlocks(in: range, gap: CalendarModel.gap, force: true)
        #expect(blocks.count == 1)
        #expect(blocks[0].responses == 1)
    }

    @Test func aPositiveDroidReplyStaysAndAZeroTokenSessionDrops() async throws {
        // Droid events carry no per-reply token detail, so only an empty
        // session is trivial; a lone reply with tokens is real work.
        let dir = try TempDir()
        _ = try droidFile(dir, "11111111-2222-3333-4444-555555555555")
        _ = try droidFile(dir, "66666666-7777-8888-9999-000000000000")
        let ledger = try await ledger([
            UsageEvent(harness: "droid", accountHome: "/factory",
                       sessionId: "11111111-2222-3333-4444-555555555555",
                       model: "m", timestamp: utc("2026-08-10T08:37:16Z"),
                       outputTokens: 5),
            UsageEvent(harness: "droid", accountHome: "/factory",
                       sessionId: "66666666-7777-8888-9999-000000000000",
                       model: "m", timestamp: utc("2026-08-10T08:37:16Z"),
                       outputTokens: 0),
        ], in: dir)
        let blocks = try await droidProvider(dir, ledger)
            .activityBlocks(in: range, gap: CalendarModel.gap, force: true)
        #expect(blocks.map { $0.sessions[0].sessionId }
            == ["11111111-2222-3333-4444-555555555555"])
    }

    @Test func disablingTheSettingRestoresTheCachedSessions() async throws {
        let dir = try TempDir()
        let session = try await piSession(dir, "1786332436_cccccccc-cccc-dddd-eeeeeeeeeeee.jsonl")
        let ledger = try await ledger([
            UsageEvent(harness: "pi", accountHome: "/pi", sessionId: session,
                       model: "m", timestamp: utc("2026-08-10T08:32:00Z"),
                       outputTokens: 10),
        ], in: dir)
        let provider = piProvider(dir, ledger)
        #expect(try await provider
            .activityBlocks(in: range, gap: CalendarModel.gap, force: true).isEmpty)
        ledger.setHidesTrivialSessions(false)
        // Inside the index's refresh window this is the cached session
        // list: the filter runs on it, not a re-parse.
        let blocks = try await provider
            .activityBlocks(in: range, gap: CalendarModel.gap, force: false)
        #expect(blocks.count == 1)
    }

    @Test func aSessionWithoutUsageRowsStaysVisible() async throws {
        // An unscanned or unledgered session proves nothing; its file
        // still shows real responses.
        let dir = try TempDir()
        _ = try await piSession(dir, "1786332436_dddddddd-dddd-dddd-eeeeeeeeeeee.jsonl")
        let ledger = try await ledger([
            UsageEvent(harness: "claude", accountHome: "/claude", sessionId: "other",
                       model: "m", timestamp: utc("2026-08-10T09:00:00Z"),
                       outputTokens: 200),
        ], in: dir)
        let blocks = try await piProvider(dir, ledger)
            .activityBlocks(in: range, gap: CalendarModel.gap, force: true)
        #expect(blocks.count == 1)
    }

    @Test func usageUnderAnotherAccountDoesNotHideTheSession() async throws {
        let dir = try TempDir()
        let session = try await piSession(dir, "1786332436_eeeeeeee-eeee-dddd-eeeeeeeeeeee.jsonl")
        let ledger = try await ledger([
            UsageEvent(harness: "pi", accountHome: "/other-account", sessionId: session,
                       model: "m", timestamp: utc("2026-08-10T08:32:00Z"),
                       outputTokens: 10),
        ], in: dir)
        let blocks = try await piProvider(dir, ledger)
            .activityBlocks(in: range, gap: CalendarModel.gap, force: true)
        #expect(blocks.count == 1)
        #expect(blocks[0].accountHome == "/pi")
    }
}
