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
        #expect(roster.labels(for: "bot-uuid-1").projectName == "Cortex")
        #expect(roster.labels(for: "bot-uuid-2").projectName == "NUS")
    }

    @Test func aRosteredSessionLabelsAsItsBot() throws {
        let roster = GrokBotRoster(directory: try persistence(json: rosterJSON))
        let labels = roster.labels(for: "bot-uuid-1")
        #expect(labels.projectKey == "grok-bot:bot-uuid-1")
        #expect(labels.projectName == "Cortex")
        #expect(labels.sessionTitle == "Cortex")
    }

    @Test func aSubagentSessionGroupsAsSubagents() throws {
        let roster = GrokBotRoster(directory: try persistence(json: rosterJSON))
        let labels = roster.labels(for: "sand-subagent-0f9c2d1e")
        #expect(labels.projectKey == "Subagents")
        #expect(labels.projectName == "Subagents")
        #expect(labels.sessionTitle == "Subagent")
    }

    @Test func anUnknownSessionFallsBackToGrokBotAndTheShortId() throws {
        let roster = GrokBotRoster(directory: try persistence(json: rosterJSON))
        let uuid = "01234567-89ab-cdef-0123-456789abcdef"
        let labels = roster.labels(for: uuid)
        #expect(labels.projectKey == "Grok Bot")
        #expect(labels.projectName == "Grok Bot")
        #expect(labels.sessionTitle == "01234567…")
        // A slug-shaped id stays whole, like the shared fallback.
        #expect(roster.labels(for: "friendly-session").sessionTitle == "friendly-session")
    }

    @Test func missingDirectoryOrMalformedJSONGivesAnEmptyMap() throws {
        let missing = GrokBotRoster(
            directory: URL(fileURLWithPath: "/nonexistent/grok-bots"))
        #expect(missing.labels(for: "bot-uuid-1").projectName == "Grok Bot")

        for bad in ["not json", "{}", #"{"value": {"rows": "nope"}}"#,
                    #"{"value": {"rows": [{"id": 1, "name": 2}]}}"#] {
            let roster = GrokBotRoster(directory: try persistence(json: bad))
            #expect(roster.labels(for: "bot-uuid-1").projectName == "Grok Bot")
        }
    }

    @Test func lookupsWithinTheIntervalNeverTouchTheDisk() throws {
        let dir = try persistence(json: rosterJSON)
        let roster = GrokBotRoster(directory: dir, recheckInterval: 3600)
        #expect(roster.labels(for: "bot-uuid-1").projectName == "Cortex")
        // With the folder gone, a cached lookup still answers: nothing was
        // listed or read for it.
        try FileManager.default.removeItem(at: dir)
        #expect(roster.labels(for: "bot-uuid-1").projectName == "Cortex")
        // A zero interval checks the disk every time and sees it's gone.
        let eager = GrokBotRoster(directory: dir, recheckInterval: 0)
        #expect(eager.labels(for: "bot-uuid-1").projectName == "Grok Bot")
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
        #expect(roster.labels(for: "bot-uuid-1").sessionTitle == "Cortex")
    }

    @Test func groupingUsesTheRosterPerSession() {
        let resolver = ProjectResolver(gitDir: { _ in nil },
                                       grokBot: { id in
            id == "bot-uuid-1" ? ("grok-bot:bot-uuid-1", "Cortex")
                               : ("Grok Bot", "Grok Bot")
        })
        let grouped = resolver.group([
            ActivityBlock(harness: "grok-bot", accountHome: "", sessionId: "bot-uuid-1",
                          start: utc("2026-10-14T09:00:00Z"),
                          end: utc("2026-10-14T09:30:00Z"), responses: 1),
            ActivityBlock(harness: "grok-bot", accountHome: "", sessionId: "other-id",
                          start: utc("2026-10-14T09:10:00Z"),
                          end: utc("2026-10-14T09:40:00Z"), responses: 1),
            ActivityBlock(harness: "claude", accountHome: "/h",
                          sessionId: "s1", start: utc("2026-10-14T09:00:00Z"),
                          end: utc("2026-10-14T09:30:00Z"), responses: 1),
        ], gap: CalendarModel.gap)
        let byProject = Dictionary(grouping: grouped, by: \.project)
        #expect(byProject["Cortex"]?.count == 1)
        #expect(byProject["Cortex"]?.first?.projectKey == "grok-bot:bot-uuid-1")
        #expect(byProject["Grok Bot"]?.count == 1)
        #expect(byProject["No folder"]?.count == 1)  // the claude block, no cwd
        // The two grok-bot projects do not merge even overlapping in time.
        #expect(grouped.filter { $0.harness == "grok-bot" }.count == 2)
    }
}
