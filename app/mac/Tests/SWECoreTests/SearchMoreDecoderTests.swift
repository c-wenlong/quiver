import Foundation
import Testing
@testable import SWECore

/// The cursor/droid/cline record shapes exactly as the harnesses write them
/// on this machine — each decoder's `decode` against a real-shaped line or
/// document, and the session hints they surface.
@Suite struct SearchMoreDecoderTests {

    // MARK: - Cursor

    private func cursorLine(_ role: String, _ content: String) -> String {
        #"{"role":"\#(role)","message":{"content":\#(content)}}"#
    }

    @Test func cursorUserRecordDecodesItsText() {
        let out = SearchDecoder.decode(
            agent: .cursor,
            line: Data(cursorLine("user", #"[{"type":"text","text":"how do I fix the build?"}]"#).utf8))
        #expect(out.messages == [SearchMessage(role: .user, text: "how do I fix the build?")])
        // The first user text is the fallback session title.
        #expect(out.hints.title == "how do I fix the build?")
    }

    @Test func cursorAssistantRecordSplitsTextAndToolUse() {
        let content = #"[{"type":"text","text":"Checking the build."},{"type":"tool_use","name":"Bash","input":{"command":"swift build"}}]"#
        let out = SearchDecoder.decode(
            agent: .cursor, line: Data(cursorLine("assistant", content).utf8))
        #expect(out.messages.map(\.role) == [.assistant, .tool])
        #expect(out.messages[0].text == "Checking the build.")
        #expect(out.messages[1].text == "Bash: swift build")
    }

    @Test func cursorReadsTopLevelContentWhenMessageIsAbsent() {
        let out = SearchDecoder.decode(
            agent: .cursor,
            line: Data(#"{"role":"user","content":"plain text body"}"#.utf8))
        #expect(out.messages == [SearchMessage(role: .user, text: "plain text body")])
    }

    @Test func cursorIgnoresNonConversationRecords() {
        for role in ["system", "turn_ended", "summary"] {
            let out = SearchDecoder.decode(
                agent: .cursor,
                line: Data(#"{"role":"\#(role)","message":{"content":[{"type":"text","text":"x"}]}}"#.utf8))
            #expect(out.messages.isEmpty)
        }
        // A record without the marker never even parses.
        var decoder = SearchDecoder(.cursor)
        #expect(!decoder.wants(line: Data(#"{"kind":"progress"}"#.utf8)))
    }

    // MARK: - Droid

    @Test func droidSessionStartCarriesIdentity() {
        let out = SearchDecoder.decode(
            agent: .droid,
            line: Data(#"{"type":"session_start","id":"d-1","title":"Fix the parser","cwd":"/repo/x"}"#.utf8))
        #expect(out.messages.isEmpty)
        #expect(out.hints.sessionId == "d-1")
        #expect(out.hints.title == "Fix the parser")
        #expect(out.hints.cwd == "/repo/x")
    }

    @Test func droidMessageRecordsDecodeNestedContent() {
        var decoder = SearchDecoder(.droid)
        let user = decoder.decode(line: Data(
            #"{"type":"message","timestamp":"2026-10-05T12:00:00Z","message":{"role":"user","content":[{"type":"text","text":"why is it failing?"}]}}"#.utf8))
        #expect(user.messages == [SearchMessage(
            role: .user, text: "why is it failing?", ts: "2026-10-05T12:00:00Z")])
        let assistant = decoder.decode(line: Data(
            #"{"type":"message","timestamp":"2026-10-05T12:00:01Z","message":{"role":"assistant","content":[{"type":"text","text":"Checking."},{"type":"tool_use","name":"Read","input":{"file_path":"/repo/x/a.swift"}}]}}"#
                .utf8))
        #expect(assistant.messages.map(\.role) == [.assistant, .tool])
        #expect(assistant.messages[1].text == "Read: /repo/x/a.swift")
        #expect(assistant.messages[1].ts == "2026-10-05T12:00:01Z")
    }

    @Test func droidCompletionAndBookkeepingRecords() {
        var decoder = SearchDecoder(.droid)
        let done = decoder.decode(line: Data(
            #"{"type":"completion","finalText":"All done.","timestamp":"2026-10-05T12:00:02Z"}"#.utf8))
        #expect(done.messages == [SearchMessage(
            role: .assistant, text: "All done.", ts: "2026-10-05T12:00:02Z")])
        // Bookkeeping records decode to nothing but may restate the id.
        let todo = decoder.decode(line: Data(
            #"{"type":"todo_state","session_id":"d-1","todos":[]}"#.utf8))
        #expect(todo.messages.isEmpty)
        #expect(todo.hints.sessionId == "d-1")
    }

    // MARK: - Cline

    /// The manifest shape on this machine (`~/.cline/data/sessions/<id>/<id>.json`).
    private let clineManifest = """
        {"version":1,"session_id":"1789471031317_jp2hv","source":"cli","pid":48300,
         "started_at":"2026-09-15T11:17:11.371Z","ended_at":null,"exit_code":0,
         "status":"done","interactive":true,"provider":"cline",
         "model":"grok-4-1-fast-reasoning","cwd":"/Users/x/ws",
         "workspace_root":"/Users/x/ws","prompt":"say hi","metadata":{},
         "messages_path":"/sessions/1789471031317_jp2hv.messages.json"}
        """

    /// The sibling's shape (`<id>.messages.json`): a whole JSON object whose
    /// `messages` array carries the turns, `ts` in epoch milliseconds.
    private let clineMessages = """
        {"agent":"cline","sessionId":"1789471031317_jp2hv","origin":"cli",
         "version":1,"system_prompt":"…","updated_at":"2026-09-15T11:17:15.000Z",
         "messages":[
           {"id":"m1","role":"user",
            "content":[{"type":"text","text":"please fix the bug"}],"ts":1789471031400},
           {"id":"m2","role":"assistant",
            "content":[{"type":"thinking","thinking":"hmm"},
                       {"type":"text","text":"On it."}],
            "ts":1789471031500,
            "modelInfo":{"id":"grok-4-1-fast-reasoning","provider":"xai"}}
         ]}
        """

    @Test func clineManifestCarriesTheSessionShell() {
        let out = SearchDecoder.decode(agent: .cline, line: Data(clineManifest.utf8))
        #expect(out.messages.isEmpty)
        #expect(out.hints.sessionId == "1789471031317_jp2hv")
        // `cwd` beats `workspace_root` (Orca's order).
        #expect(out.hints.cwd == "/Users/x/ws")
        #expect(out.hints.title == "say hi")
    }

    @Test func clineMessagesDocumentDecodesItsTurns() {
        let out = SearchDecoder.decode(agent: .cline, line: Data(clineMessages.utf8))
        #expect(out.hints.sessionId == "1789471031317_jp2hv")
        #expect(out.messages.map(\.role) == [.user, .assistant])
        // Epoch-millisecond `ts` lands as ISO on the message.
        #expect(out.messages[0].ts == "2026-09-15T11:17:11.400Z")
        #expect(out.messages[1].ts == "2026-09-15T11:17:11.500Z")
        // A `thinking` key's text is not content, so only the text block lands.
        #expect(out.messages[1].text == "On it.")
        // The first user turn names the session — emitted as an explicit
        // title so it outranks a manifest `prompt` decoded before it.
        #expect(out.hints.title == "please fix the bug")
    }

    @Test func clineManifestThenMessagesKeepsTheUserTitle() {
        // The pair decodes under one beginFile in Orca's order: manifest,
        // then the messages document — whose first user text wins the title.
        var decoder = SearchDecoder(.cline)
        _ = decoder.decode(line: Data(clineManifest.utf8))
        let out = decoder.decode(line: Data(clineMessages.utf8))
        #expect(out.hints.title == "please fix the bug")
    }

    @Test func clineManifestWithoutMessagesKeepsThePromptTitle() {
        var decoder = SearchDecoder(.cline)
        let manifest = decoder.decode(line: Data(clineManifest.utf8))
        #expect(manifest.hints.title == "say hi")
        // A messages document with no user turn leaves the prompt standing.
        let out = decoder.decode(line: Data(
            #"{"sessionId":"1789471031317_jp2hv","messages":[{"role":"assistant","content":[{"type":"text","text":"hi"}],"ts":1}]}"#.utf8))
        #expect(out.hints.title == nil)
        #expect(out.messages.map(\.role) == [.assistant])
    }

    @Test func clineFallsBackToWorkspaceRootForCwd() {
        let out = SearchDecoder.decode(agent: .cline, line: Data(
            #"{"session_id":"s9","workspace_root":"/only/root","prompt":"p"}"#.utf8))
        #expect(out.hints.cwd == "/only/root")
    }
}
