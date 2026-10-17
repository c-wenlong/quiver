import Foundation
import Testing
@testable import QuiverCore

/// The Grok Bot persistence roster: filename keys are base32, the roster
/// blob names the user's Bots, and labelling never opens anything else.
@Suite struct GrokBotRosterTests {

    /// A temp `sand-client-persistence` folder holding one blob named by
    /// the base32 of `key` with `json` as contents.
    private func persistence(key: String = "user-1.roster.last-roster",
                             json: String,
                             extras: [(key: String, json: String)] = []) throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("grok-roster-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        func write(_ json: String, key: String) throws {
            let name = GrokBotRoster.encodeBase32(key) + ".blob"
            try json.write(to: dir.appendingPathComponent(name),
                           atomically: true, encoding: .utf8)
        }
        try write(json, key: key)
        for extra in extras { try write(extra.json, key: extra.key) }
        return dir
    }

    private let rosterJSON = """
        {"schemaVersion": 3, "value": {"rows": [
            {"id": "bot-uuid-1", "name": "Cortex",
             "lastEntry": "must never be read", "settings": {"secret": 1}},
            {"id": "bot-uuid-2", "name": "NUS", "extra": [1, 2]},
            {"id": "no-name-row"},
            {"name": "no-id-row"}
        ]}}
        """

    @Test func base32RoundTripDecodesFilenameKeys() throws {
        for key in ["user-1.roster.last-roster", "abc", "x",
                    "conversation.01234567-89ab-cdef.roster.last-roster"] {
            let stem = GrokBotRoster.encodeBase32(key)
            #expect(stem == stem.lowercased())
            #expect(!stem.contains("="))
            #expect(GrokBotRoster.decodeBase32(stem) == key)
        }
        // A key's stem keeps the roster suffix after decoding.
        let decoded = try #require(
            GrokBotRoster.decodeBase32(
                GrokBotRoster.encodeBase32("u.roster.last-roster")))
        #expect(decoded.hasSuffix(".roster.last-roster"))
        // Characters outside a–z2–7 never decode.
        #expect(GrokBotRoster.decodeBase32("not*base32!") == nil)
        #expect(GrokBotRoster.decodeBase32("0") == nil)
        #expect(GrokBotRoster.decodeBase32("") == "")
    }

    @Test func rosterParsesIdsAndNamesOnly() throws {
        let dir = try persistence(json: rosterJSON,
                                  extras: [(key: "user-1.transcripts",
                                            json: #"{"secret": "transcript data"}"#)])
        let roster = GrokBotRoster(directory: dir)
        #expect(roster.title(for: "bot-uuid-1") == "Cortex")
        #expect(roster.title(for: "bot-uuid-2") == "NUS")
    }

    @Test func aSubagentSessionTitlesAsSubagent() throws {
        let roster = GrokBotRoster(directory: try persistence(json: rosterJSON))
        #expect(roster.title(for: "sand-subagent-0f9c2d1e") == "Subagent")
    }

    @Test func anUnknownSessionFallsBackToTheShortId() throws {
        let roster = GrokBotRoster(directory: try persistence(json: rosterJSON))
        #expect(roster.title(for: "01234567-89ab-cdef-0123-456789abcdef") == "01234567…")
        // A slug-shaped id stays whole, like the shared fallback.
        #expect(roster.title(for: "friendly-session") == "friendly-session")
    }

    @Test func missingDirectoryOrMalformedJSONGivesAnEmptyMap() throws {
        let missing = GrokBotRoster(
            directory: URL(fileURLWithPath: "/nonexistent/grok-bots"))
        #expect(missing.title(for: "bot-uuid-1") == "bot-uuid-1")

        for bad in ["not json", "{}", #"{"value": {"rows": "nope"}}"#,
                    #"{"value": {"rows": [{"id": 1, "name": 2}]}}"#] {
            let roster = GrokBotRoster(directory: try persistence(json: bad))
            #expect(roster.title(for: "bot-uuid-1") == "bot-uuid-1")
        }
    }

    @Test func lookupsWithinTheIntervalNeverTouchTheDisk() throws {
        let dir = try persistence(json: rosterJSON)
        let roster = GrokBotRoster(directory: dir, recheckInterval: 3600)
        #expect(roster.title(for: "bot-uuid-1") == "Cortex")
        // With the folder gone, a cached lookup still answers: nothing was
        // listed or read for it.
        try FileManager.default.removeItem(at: dir)
        #expect(roster.title(for: "bot-uuid-1") == "Cortex")
        // A zero interval checks the disk every time and sees it's gone.
        let eager = GrokBotRoster(directory: dir, recheckInterval: 0)
        #expect(eager.title(for: "bot-uuid-1") == "bot-uuid-1")
    }

    @Test func theRosterBlobIsFoundAmongOtherBlobs() throws {
        // Only the decoded key ending `.roster.last-roster` is opened:
        // transcripts and other persistence files stay unread.
        let dir = try persistence(json: rosterJSON, extras: [
            (key: "user-1.transcripts.1",
             json: #"{"value":{"rows":[{"id":"x","name":"Wrong"}]}}"#),
            (key: "user-1.secrets", json: #"{"key":"sk-secret"}"#),
        ])
        let roster = GrokBotRoster(directory: dir)
        #expect(roster.title(for: "bot-uuid-1") == "Cortex")
    }

    /// Bots hand one task between them, so every Bot shares one lane:
    /// overlapping or near Bots merge into one "Grok Bot" block that
    /// lists each session, and other harnesses stay apart.
    @Test func everyBotSharesOneGrokBotLane() {
        let resolver = ProjectResolver(gitDir: { _ in nil })
        let grouped = resolver.group([
            ActivityBlock(harness: "grok-bot", accountHome: "", sessionId: "bot-uuid-1",
                          start: utc("2026-10-14T09:00:00Z"),
                          end: utc("2026-10-14T09:30:00Z"), responses: 1),
            ActivityBlock(harness: "grok-bot", accountHome: "", sessionId: "sand-subagent-1",
                          start: utc("2026-10-14T09:10:00Z"),
                          end: utc("2026-10-14T09:40:00Z"), responses: 1),
            ActivityBlock(harness: "grok-bot", accountHome: "", sessionId: "bot-uuid-2",
                          start: utc("2026-10-14T09:45:00Z"),
                          end: utc("2026-10-14T09:50:00Z"), responses: 2),
            ActivityBlock(harness: "claude", accountHome: "/h",
                          sessionId: "s1", start: utc("2026-10-14T09:00:00Z"),
                          end: utc("2026-10-14T09:30:00Z"), responses: 1),
        ], gap: CalendarModel.gap)
        let grok = grouped.filter { $0.harness == "grok-bot" }
        #expect(grok.count == 1)
        #expect(grok.first?.project == "Grok Bot")
        #expect(grok.first?.projectKey == "grok-bot")
        #expect(Set(grok.first?.sessions.map(\.sessionId) ?? [])
                == ["bot-uuid-1", "sand-subagent-1", "bot-uuid-2"])
        #expect(grok.first?.responses == 4)
        #expect(grouped.filter { $0.harness == "claude" }.count == 1)
    }
}
