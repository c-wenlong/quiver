import Foundation
import Testing
@testable import QuiverCore

/// The record shapes the remaining Orca harnesses write — each decoder's
/// `decode` against a real-shaped line or whole document, the session hints
/// it surfaces, and the injected-turn rules that keep machinery text out of
/// the title without dropping it from the index.
@Suite struct SearchNewHarnessDecoderTests {
    func line(_ json: String) -> Data { Data(json.utf8) }

    // MARK: - qoder

    @Test func qoderDecodesClaudeShapedTurnsAndWorkspaceCwd() {
        var decoder = SearchDecoder(.qoder)
        let dirs = decoder.decode(line: line(
            #"{"type":"workspace-directories","directories":["/repo/qoder"]}"#))
        #expect(dirs.hints.cwd == "/repo/qoder")
        let user = decoder.decode(line: line(
            #"{"type":"user","timestamp":"t1","sessionId":"q-1","message":{"role":"user","content":[{"type":"text","text":"fix the flaky test"},{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}"#))
        // Content arrays filter to text blocks — tool blocks never decode.
        #expect(user.messages == [SearchMessage(role: .user, text: "fix the flaky test", ts: "t1")])
        #expect(user.hints.sessionId == "q-1")
        #expect(user.hints.title == "fix the flaky test")
        let assistant = decoder.decode(line: line(
            #"{"type":"assistant","timestamp":"t2","message":{"role":"assistant","content":"on it"}}"#))
        #expect(assistant.messages == [SearchMessage(role: .assistant, text: "on it", ts: "t2")])
        // A resumed record's own cwd does not displace the start cwd.
        let moved = decoder.decode(line: line(
            #"{"type":"user","sessionId":"q-1","cwd":"/elsewhere","message":{"role":"user","content":"again"}}"#))
        #expect(moved.hints.cwd == nil)
    }

    @Test func qoderTitleRecordsAndMetaTurns() {
        var decoder = SearchDecoder(.qoder)
        let meta = decoder.decode(line: line(
            #"{"type":"user","sessionId":"q","isMeta":true,"message":{"role":"user","content":"<system-reminder>env</system-reminder>"}}"#))
        #expect(meta.hints.injectedUserTurn)
        #expect(meta.hints.title == nil)
        let title = decoder.decode(line: line(
            #"{"type":"custom-title","customTitle":"Named"}"#))
        #expect(title.hints.title == "Named")
    }

    // MARK: - codebuddy

    @Test func codebuddyHintsAndMessageBlocks() {
        var decoder = SearchDecoder(.codebuddy)
        let user = decoder.decode(line: line(
            #"{"type":"message","sessionId":"cb-1","cwd":"/repo/cb","gitBranch":"main","timestamp":"t1","role":"user","content":[{"type":"input_text","text":"why failing?"},{"type":"input_text","text":"part two"}]}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "why failing?\n\npart two", ts: "t1")])
        #expect(user.hints.sessionId == "cb-1")
        #expect(user.hints.cwd == "/repo/cb")
        #expect(user.hints.branch == "main")
        // The title candidate folds the joined text to one line.
        #expect(user.hints.title == "why failing? part two")
        let assistant = decoder.decode(line: line(
            #"{"type":"message","role":"assistant","timestamp":"t2","content":[{"type":"output_text","text":"checking"}]}"#))
        #expect(assistant.messages == [SearchMessage(role: .assistant, text: "checking", ts: "t2")])
        // A string content body is the text itself.
        let string = decoder.decode(line: line(
            #"{"type":"message","role":"user","content":"plain body"}"#))
        #expect(string.messages == [SearchMessage(role: .user, text: "plain body")])
    }

    @Test func codebuddyTitleOrderSummaryOverAiTitleOverPrompt() {
        var decoder = SearchDecoder(.codebuddy)
        _ = decoder.decode(line: line(
            #"{"type":"ai-title","aiTitle":"Generated name"}"#))
        let prompt = decoder.decode(line: line(
            #"{"type":"message","role":"user","content":"typed ask"}"#))
        // The generated title outranks the raw first prompt.
        #expect(prompt.hints.title == nil)
        let summary = decoder.decode(line: line(
            #"{"type":"summary","summary":"The CLI's title"}"#))
        #expect(summary.hints.title == "The CLI's title")
        // A later ai-title cannot revise a summary.
        let late = decoder.decode(line: line(
            #"{"type":"ai-title","aiTitle":"revised"}"#))
        #expect(late.hints.title == nil)
    }

    @Test func codebuddySkipRunAndKnownTagsMarkInjectedTurns() {
        var decoder = SearchDecoder(.codebuddy)
        let skipped = decoder.decode(line: line(
            #"{"type":"message","role":"user","content":[{"type":"input_text","text":"queued work"}],"providerData":{"skipRun":true}}"#))
        #expect(skipped.messages.map(\.role) == [.user])
        #expect(skipped.hints.injectedUserTurn)
        #expect(skipped.hints.title == nil)
        let tagged = decoder.decode(line: line(
            #"{"type":"message","role":"user","content":"<task-notification>done</task-notification>"}"#))
        #expect(tagged.hints.injectedUserTurn)
        let real = decoder.decode(line: line(
            #"{"type":"message","role":"user","content":"a real prompt"}"#))
        #expect(real.hints.title == "a real prompt")
    }

    // MARK: - gemini

    @Test func geminiJsonDocumentDecodes() {
        let document = line(#"""
        {"sessionId":"g-1","startTime":"2026-10-01T09:00:00Z","lastUpdated":"2026-10-01T09:05:00Z",
         "messages":[
           {"type":"user","content":[{"text":"summarise the diff"}],"timestamp":"2026-10-01T09:00:10Z"},
           {"type":"gemini","content":"Two files changed.","timestamp":"2026-10-01T09:00:30Z"},
           {"type":"info","content":"a notice"}
         ]}
        """#)
        let out = SearchDecoder.decode(agent: .gemini, line: document)
        #expect(out.hints.sessionId == "g-1")
        #expect(out.messages.map(\.role) == [.user, .assistant])
        #expect(out.messages[0].ts == "2026-10-01T09:00:10Z")
        #expect(out.hints.title == "summarise the diff")
    }

    @Test func geminiJsonlRecordsDecodeAndBookkeepingSkips() {
        var decoder = SearchDecoder(.gemini)
        let set = decoder.decode(line: line(
            #"{"$set":{"lastUpdated":"2026-10-01T09:05:00Z"}}"#))
        #expect(set.messages.isEmpty)
        let user = decoder.decode(line: line(
            #"{"type":"user","content":"the ask","timestamp":"t1","sessionId":"g-2"}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "the ask", ts: "t1")])
        #expect(user.hints.sessionId == "g-2")
        let reply = decoder.decode(line: line(
            #"{"type":"gemini","content":[{"text":"the answer"}],"timestamp":"t2"}"#))
        #expect(reply.messages == [SearchMessage(role: .assistant, text: "the answer", ts: "t2")])
    }

    // MARK: - copilot

    @Test func copilotSessionRecordsDecode() {
        var decoder = SearchDecoder(.copilot)
        let start = decoder.decode(line: line(
            #"{"type":"session.start","data":{"sessionId":"cp-1"},"timestamp":"t0"}"#))
        #expect(start.hints.sessionId == "cp-1")
        let info = decoder.decode(line: line(
            #"{"type":"session.info","data":{"kind":"folder_trust","message":"Folder /repo/app has been added to trusted folders."}}"#))
        #expect(info.hints.cwd == "/repo/app")
        let user = decoder.decode(line: line(
            #"{"type":"user.message","data":{"content":"raw","transformedContent":"the typed ask"},"timestamp":"t1"}"#))
        // transformedContent is the copy Orca previews and titles from.
        #expect(user.messages == [SearchMessage(role: .user, text: "the typed ask", ts: "t1")])
        #expect(user.hints.title == "the typed ask")
        let assistant = decoder.decode(line: line(
            #"{"type":"assistant.message","data":{"content":"the reply"},"timestamp":"t2"}"#))
        #expect(assistant.messages == [SearchMessage(role: .assistant, text: "the reply", ts: "t2")])
        // System informational rows are not transcript turns.
        let shutdown = decoder.decode(line: line(
            #"{"type":"session.shutdown","data":{"currentModel":"x","currentTokens":10}}"#))
        #expect(shutdown.messages.isEmpty)
    }

    // MARK: - grok

    @Test func grokSummaryCarriesIdentityAndTitle() {
        let out = SearchDecoder.decode(agent: .grok, line: line(
            #"{"info":{"id":"gk-1","cwd":"/repo/g"},"generated_title":"Ship it","session_summary":"fallback","head_branch":"feat/x","current_model_id":"grok-4"}"#))
        #expect(out.hints.sessionId == "gk-1")
        #expect(out.hints.cwd == "/repo/g")
        #expect(out.hints.branch == "feat/x")
        #expect(out.hints.title == "Ship it")
        let summaryOnly = SearchDecoder.decode(agent: .grok, line: line(
            #"{"info":{"id":"gk-2"},"session_summary":"The summary"}"#))
        #expect(summaryOnly.hints.title == "The summary")
    }

    @Test func grokHistoryUserRecordsUnwrapTheQueryEnvelope() {
        let out = SearchDecoder.decode(agent: .grok, line: line(
            #"{"type":"user","timestamp":"t1","content":"<user_info>who am i</user_info>\n<user_query>how do I deploy?</user_query>"}"#))
        #expect(out.messages == [SearchMessage(role: .user, text: "how do I deploy?", ts: "t1")])
        #expect(out.hints.title == "how do I deploy?")
    }

    @Test func grokBootstrapRowsAndAssistantText() {
        // A <user_info>-only row (plus optional git_status) is bootstrap
        // context — it publishes nothing and cannot seed the title.
        let bootstrap = SearchDecoder.decode(agent: .grok, line: line(
            #"{"type":"user","timestamp":"t0","content":"<user_info>ctx</user_info>"}"#))
        #expect(bootstrap.messages.isEmpty)
        #expect(bootstrap.hints.title == nil)
        let withGit = SearchDecoder.decode(agent: .grok, line: line(
            #"{"type":"user","content":"<user_info>ctx</user_info><git_status>clean</git_status>"}"#))
        #expect(withGit.messages.isEmpty)
        let assistant = SearchDecoder.decode(agent: .grok, line: line(
            #"{"type":"assistant","timestamp":"t2","content":"the answer"}"#))
        #expect(assistant.messages == [SearchMessage(role: .assistant, text: "the answer", ts: "t2")])
        let blocks = SearchDecoder.decode(agent: .grok, line: line(
            #"{"type":"assistant","content":[{"type":"text","text":"block one"},{"type":"text","text":"block two"}]}"#))
        #expect(blocks.messages == [SearchMessage(role: .assistant, text: "block one\nblock two")])
    }

    // MARK: - hermes

    @Test func hermesDocumentDecodes() {
        let out = SearchDecoder.decode(agent: .hermes, line: line(#"""
        {"session_id":"h-1","model":"opus","cwd":"/repo/h",
         "session_start":"2026-09-01T08:00:00Z","last_updated":"2026-09-01T08:30:00Z",
         "messages":[
           {"role":"user","content":"where is the config?"},
           {"role":"assistant","content":[{"type":"text","text":"In settings.json."}]},
           {"role":"system","content":"skip me"}
         ]}
        """#))
        #expect(out.hints.sessionId == "h-1")
        #expect(out.hints.cwd == "/repo/h")
        #expect(out.messages.map(\.role) == [.user, .assistant])
        #expect(out.messages[1].text == "In settings.json.")
        #expect(out.hints.title == "where is the config?")
    }

    // MARK: - jcode

    @Test func jcodeDocumentSkipsInternalTurns() {
        let out = SearchDecoder.decode(agent: .jcode, line: line(#"""
        {"id":"jc-1","working_dir":"/repo/j","custom_title":"Chosen",
         "messages":[
           {"role":"user","display_role":"system","content":"internal context","timestamp":"t0"},
           {"role":"user","content":"[Scheduled task] nightly","timestamp":"t1"},
           {"role":"user","content":"<system-reminder>heads up</system-reminder>","timestamp":"t2"},
           {"role":"system","content":"a system row","timestamp":"t3"},
           {"role":"user","content":"the real prompt","timestamp":"t4"},
           {"role":"assistant","content":"the reply","timestamp":"t5"},
           {"role":"tool","content":"tool output","timestamp":"t6"},
           {"role":"mystery","content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}],"timestamp":"t7"}
         ]}
        """#))
        #expect(out.hints.sessionId == "jc-1")
        #expect(out.hints.cwd == "/repo/j")
        #expect(out.hints.title == "Chosen")
        #expect(out.messages.map(\.role) == [.user, .assistant, .tool, .tool])
        #expect(out.messages[0].text == "the real prompt")
        #expect(out.messages[0].ts == "t4")
        // The unknown role publishes its tool blocks only.
        #expect(out.messages[3].text == "Bash: ls")
    }

    @Test func jcodeTitleFallsBackThroughTitleThenFirstPrompt() {
        var decoder = SearchDecoder(.jcode)
        let titled = decoder.decode(line: line(
            #"{"title":"Doc title","messages":[{"role":"user","content":"p"}]}"#))
        #expect(titled.hints.title == "Doc title")
        var untitled = SearchDecoder(.jcode)
        let prompt = untitled.decode(line: line(
            #"{"messages":[{"role":"user","content":"the first prompt","timestamp":"t1"}]}"#))
        #expect(prompt.hints.title == "the first prompt")
    }

    // MARK: - kiro

    @Test func kiroManifestAndTranscriptRecords() {
        var decoder = SearchDecoder(.kiro)
        let manifest = decoder.decode(line: line(
            #"{"cwd":"/repo/kiro","title":"Named chat","session_state":{},"created_at":"t0"}"#))
        #expect(manifest.hints.cwd == "/repo/kiro")
        #expect(manifest.hints.title == "Named chat")
        let prompt = decoder.decode(line: line(
            #"{"kind":"Prompt","data":{"content":[{"kind":"text","data":"run the suite"}],"meta":{"timestamp":1789471031}}}"#))
        // data.meta.timestamp is epoch seconds → ISO on the message.
        #expect(prompt.messages == [SearchMessage(
            role: .user, text: "run the suite", ts: "2026-09-15T11:17:11.000Z")])
        let assistant = decoder.decode(line: line(
            #"{"kind":"AssistantMessage","data":{"content":[{"kind":"text","data":"running"},{"kind":"toolUse","data":{"name":"Bash","input":{"command":"swift test"}}}]}}"#))
        #expect(assistant.messages.map(\.role) == [.assistant, .tool])
        #expect(assistant.messages[1].text == "Bash: swift test")
        let results = decoder.decode(line: line(
            #"{"kind":"ToolResults","data":{"content":[{"kind":"toolResult","data":{"content":[{"kind":"text","data":"ok"}]}}]}}"#))
        #expect(results.messages.map(\.role) == [.tool])
        let json = decoder.decode(line: line(
            #"{"kind":"Prompt","data":{"content":[{"kind":"json","data":{"a":1}}]}}"#))
        #expect(json.messages.first?.text == #"{"a":1}"#)
    }

    @Test func kiroScalarJsonBlocksAreSkippedNotFatal() {
        // A `json` block whose data is a scalar or null used to reach
        // JSONSerialization, which raises an uncatchable exception.
        var decoder = SearchDecoder(.kiro)
        for data in [#""hello""#, "42", "true", "null"] {
            let decoded = decoder.decode(line: line(
                #"{"kind":"Prompt","data":{"content":[{"kind":"json","data":"# + data
                    + #"},{"kind":"text","data":"kept"}]}}"#))
            #expect(decoded.messages.map(\.text) == ["kept"])
        }
    }

    // MARK: - rovo

    @Test func rovoManifestThenContext() {
        var decoder = SearchDecoder(.rovo)
        let manifest = decoder.decode(line: line(
            #"{"title":"Rovo chat","workspace_path":"/repo/r","created_at":"t0","updated_at":"t9"}"#))
        #expect(manifest.hints.title == "Rovo chat")
        #expect(manifest.hints.cwd == "/repo/r")
        let context = decoder.decode(line: line(#"""
        {"messages":[{"role":"user","content":"first ask","timestamp":"t1"},
                     {"role":"assistant","content":"first reply","timestamp":"t2"}],
         "message_history":[
           {"kind":"request","timestamp":"t3","parts":[
              {"part_kind":"user-prompt","content":"the typed prompt"},
              {"part_kind":"tool-output","content":"noise not kept"}]},
           {"kind":"response","timestamp":"t4","parts":[
              {"part_kind":"text","content":"the reply"},
              {"part_kind":"reasoning","content":"noise"}]},
           {"kind":"request","timestamp":"t5","parts":[{"part_kind":"tool-output","content":"only noise"}]}
         ]}
        """#))
        #expect(context.messages.map(\.role) == [.user, .assistant, .user, .assistant])
        // Parts filter per role; a parts-less/noise-only entry is skipped.
        #expect(context.messages[2].text == "the typed prompt")
        #expect(context.messages[3].text == "the reply")
        #expect(context.messages[2].ts == "t3")
    }

    @Test func rovoPromptSeedsTitleWhenManifestHasNone() {
        var decoder = SearchDecoder(.rovo)
        _ = decoder.decode(line: line(#"{"workspace":"/repo/r"}"#))
        let context = decoder.decode(line: line(
            #"{"message_history":[{"role":"user","parts":[{"part_kind":"user-prompt","content":"the ask"}]}]}"#))
        #expect(context.hints.title == "the ask")
    }

    // MARK: - message graph (openclaw / prime-agent)

    @Test func openclawAndPrimeAgentDecodeMessageGraphRecords() {
        for harness in [SearchDecoder.Harness.openclaw, .primeAgent] {
            var decoder = SearchDecoder(harness)
            let session = decoder.decode(line: line(
                #"{"type": "session", "id": "s-1", "cwd": "/repo/x", "timestamp": "t0"}"#))
            #expect(session.hints.sessionId == "s-1")
            #expect(session.hints.cwd == "/repo/x")
            let user = decoder.decode(line: line(
                #"{"type": "message", "timestamp": "t1", "message": {"role": "user", "content": [{"type": "text", "text": "the ask"}]}}"#))
            #expect(user.messages == [SearchMessage(role: .user, text: "the ask", ts: "t1")])
            #expect(user.hints.title == "the ask")
            // model_change carries only the model — no turn, no hints slot.
            let model = decoder.decode(line: line(
                #"{"type": "model_change", "modelId": "m-9"}"#))
            #expect(model.messages.isEmpty)
            let assistant = decoder.decode(line: line(
                #"{"type": "message", "timestamp": "t2", "message": {"role": "assistant", "content": "the reply"}}"#))
            #expect(assistant.messages == [SearchMessage(role: .assistant, text: "the reply", ts: "t2")])
        }
    }

    // MARK: - omp

    @Test func ompTitleFoldPrefersUserThenNewer() {
        var decoder = SearchDecoder(.omp)
        let session = decoder.decode(line: line(
            #"{"type": "session", "id": "o-1", "cwd": "/repo/o", "timestamp": "2026-10-01T00:00:00Z"}"#))
        #expect(session.hints.sessionId == "o-1")
        // An auto title lands.
        let auto = decoder.decode(line: line(
            #"{"type": "title_change", "title": "auto name", "source": "auto", "timestamp": "2026-10-01T00:01:00Z"}"#))
        #expect(auto.hints.title == "auto name")
        // An older auto title does not displace it.
        let older = decoder.decode(line: line(
            #"{"type": "title_change", "title": "older auto", "source": "auto", "timestamp": "2026-10-01T00:00:30Z"}"#))
        #expect(older.hints.title == nil)
        // A user rename always beats auto — even stamped earlier.
        let userTitle = decoder.decode(line: line(
            #"{"type": "title", "v": 1, "title": "my rename", "source": "user", "updatedAt": "2026-10-01T00:00:10Z"}"#))
        #expect(userTitle.hints.title == "my rename")
        // Auto can never displace a user title again.
        let lateAuto = decoder.decode(line: line(
            #"{"type": "title_change", "title": "late auto", "source": "auto", "timestamp": "2026-10-01T00:02:00Z"}"#))
        #expect(lateAuto.hints.title == nil)
    }

    @Test func ompTitleFoldRejectsBadRecordsAndReadsLegacy() {
        var decoder = SearchDecoder(.omp)
        // title v != 1 is not a title record.
        let badVersion = decoder.decode(line: line(
            #"{"type": "title", "v": 2, "title": "nope"}"#))
        #expect(badVersion.hints.title == nil)
        // An unrecognised source invalidates the record.
        let badSource = decoder.decode(line: line(
            #"{"type": "title_change", "title": "nope", "source": "robot"}"#))
        #expect(badSource.hints.title == nil)
        // Legacy session_info `name` is a user-sourced title.
        let legacy = decoder.decode(line: line(
            #"{"type": "session_info", "name": "legacy name", "timestamp": "t1"}"#))
        #expect(legacy.hints.title == "legacy name")
        // A missing source means auto — user titles still outrank it.
        let noSource = decoder.decode(line: line(
            #"{"type": "title_change", "title": "untagged", "timestamp": "t2"}"#))
        #expect(noSource.hints.title == nil)
        // A newer user title replaces an older one.
        let newer = decoder.decode(line: line(
            #"{"type": "title", "v": 1, "title": "final name", "source": "user", "updatedAt": "2026-10-02T00:00:00Z"}"#))
        #expect(newer.hints.title == "final name")
    }

    @Test func ompMessagesDecodeLikePi() {
        var decoder = SearchDecoder(.omp)
        let user = decoder.decode(line: line(
            #"{"type": "message", "timestamp": "t1", "message": {"role": "user", "content": [{"type": "text", "text": "omp ask"}]}}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "omp ask", ts: "t1")])
        // The first prompt is only the fallback title — emitted, but any
        // folded title record overrides it wherever it lands.
        #expect(user.hints.title == "omp ask")
        let folded = decoder.decode(line: line(
            #"{"type": "title_change", "title": "chosen", "source": "user", "timestamp": "t2"}"#))
        #expect(folded.hints.title == "chosen")
    }

    @Test func ompTitleStateRoundTrips() {
        var decoder = SearchDecoder(.omp)
        _ = decoder.decode(line: line(
            #"{"type": "title_change", "title": "kept", "source": "user", "timestamp": "t1"}"#))
        var resumed = SearchDecoder(.omp, savedState: decoder.savedState())
        // The saved user-source flag keeps a later auto title out.
        let auto = resumed.decode(line: line(
            #"{"type": "title_change", "title": "auto", "source": "auto", "timestamp": "t2"}"#))
        #expect(auto.hints.title == nil)
    }

    // MARK: - kimi

    @Test func kimiManifestTitleAndWireTurns() {
        var decoder = SearchDecoder(.kimi)
        let manifest = decoder.decode(line: line(
            #"{"title":"Named session","lastPrompt":"the last ask","createdAt":"t0","updatedAt":"t9"}"#))
        #expect(manifest.hints.title == "Named session")
        // Only user-origin user records count — synthetic injections are
        // machinery, not turns.
        let injected = decoder.decode(line: line(
            #"{"type":"context.append_message","message":{"role":"user","origin":{"kind":"injection"},"content":[{"type":"text","text":"system note"}]}}"#))
        #expect(injected.messages.isEmpty)
        let user = decoder.decode(line: line(
            #"{"type":"context.append_message","message":{"role":"user","origin":{"kind":"user"},"content":[{"type":"text","text":"the typed prompt"}]}}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "the typed prompt")])
        // Streamed parts join at step.end into one assistant message.
        _ = decoder.decode(line: line(
            #"{"type":"context.append_loop_event","event":{"type":"content.part","part":{"type":"text","text":"part one, "}}}"#))
        _ = decoder.decode(line: line(
            #"{"type":"context.append_loop_event","event":{"type":"content.part","part":{"type":"text","text":"part two"}}}"#))
        let step = decoder.decode(line: line(
            #"{"type":"context.append_loop_event","event":{"type":"step.end"}}"#))
        #expect(step.messages == [SearchMessage(role: .assistant, text: "part one, part two")])
    }

    @Test func kimiEndOfFileFlushesATrailingStep() {
        var decoder = SearchDecoder(.kimi)
        _ = decoder.decode(line: line(
            #"{"type":"context.append_loop_event","event":{"type":"content.part","part":{"type":"text","text":"unfinished"}}}"#))
        let flushed = decoder.endOfFile()
        #expect(flushed.messages == [SearchMessage(role: .assistant, text: "unfinished")])
        // A second flush has nothing left.
        #expect(decoder.endOfFile().messages.isEmpty)
    }

    @Test func kimiUntitledSessionKeepsLastPromptForTheOverlay() {
        var decoder = SearchDecoder(.kimi)
        let manifest = decoder.decode(line: line(
            #"{"lastPrompt":"the last ask","agents":{"main":{"type":"main"}}}"#))
        // lastPrompt is only the last-resort overlay — it is not the record
        // title, so a first real user turn still seeds it.
        #expect(manifest.hints.title == nil)
        let user = decoder.decode(line: line(
            #"{"type":"context.append_message","message":{"role":"user","origin":{"kind":"user"},"content":"typed"}}"#))
        #expect(user.hints.title == "typed")
    }

    // MARK: - muse

    @Test func museEnvelopesFold() {
        var decoder = SearchDecoder(.muse)
        let meta = decoder.decode(line: line(
            #"{"record_type":"snapshot","payload_type":"runtime.session.metadata","recorded_at":1789471031400000,"payload":{"record":{"workspace_root":"/repo/m"}}}"#))
        #expect(meta.hints.cwd == "/repo/m")
        let intent = decoder.decode(line: line(
            #"{"record_type":"event","payload_type":"runtime.user_intent.accepted","recorded_at":1789471031500000,"payload":{"refill_blocks":[{"text":"build the thing"}]}}"#))
        // recorded_at is microseconds → ISO on the message.
        #expect(intent.messages == [SearchMessage(
            role: .user, text: "build the thing", ts: "2026-09-15T11:17:11.500Z")])
        #expect(intent.hints.title == "build the thing")
        // The paired run.started ~ms later carries the same prompt — deduped.
        let started = decoder.decode(line: line(
            #"{"record_type":"event","payload_type":"runtime.session","recorded_at":1789471031600000,"payload":{"event":{"kind":"started","prompt":"build the thing"}}}"#))
        #expect(started.messages.isEmpty)
        let committed = decoder.decode(line: line(
            #"{"record_type":"event","payload_type":"runtime.session","recorded_at":1789471031700000,"payload":{"event":{"kind":"assistant_message_committed","text":"done"}}}"#))
        #expect(committed.messages == [SearchMessage(
            role: .assistant, text: "done", ts: "2026-09-15T11:17:11.700Z")])
    }

    @Test func museRetainedFrameUnwrapsChildren() {
        let frame = line(#"""
        {"record_type":"retained_frame","children":[
          {"record_json":"{\"payload_type\":\"runtime.user_intent.accepted\",\"recorded_at\":1789471031400000,\"payload\":{\"model_messages\":[{\"content\":[{\"text\":\"nested prompt\"}]}]}}"},
          {"record_json":"{\"payload_type\":\"runtime.session\",\"recorded_at\":1789471031500000,\"payload\":{\"event\":{\"kind\":\"assistant_message_committed\",\"text\":\"nested reply\"}}}"},
          {"record_json":"not json"}
        ]}
        """#)
        let out = SearchDecoder.decode(agent: .muse, line: frame)
        #expect(out.messages.map(\.role) == [.user, .assistant])
        #expect(out.messages[0].text == "nested prompt")
    }

    @Test func museBranchAndDeliberateRepeats() {
        var decoder = SearchDecoder(.muse)
        let branch = decoder.decode(line: line(
            #"{"payload_type":"session.workspace_branch.observed","payload":{"record":{"reference":{"name":"feat/m"}}}}"#))
        #expect(branch.hints.branch == "feat/m")
        // A repeated prompt outside the 60s window still counts.
        _ = decoder.decode(line: line(
            #"{"payload_type":"runtime.user_intent.accepted","recorded_at":1789471031400000,"payload":{"refill_blocks":[{"text":"same ask"}]}}"#))
        let repeated = decoder.decode(line: line(
            #"{"payload_type":"runtime.session","recorded_at":1789471091400000,"payload":{"event":{"kind":"started","prompt":"same ask"}}}"#))
        #expect(repeated.messages.map(\.text) == ["same ask"])
    }

    // MARK: - antigravity

    @Test func antigravityUserRequestsAndPlannerResponses() {
        var decoder = SearchDecoder(.antigravity)
        let wrapped = decoder.decode(line: line(
            #"{"source":"USER_EXPLICIT","type":"USER_INPUT","content":"<USER_REQUEST>rewrite the parser</USER_REQUEST>","created_at":"t1"}"#))
        #expect(wrapped.messages == [SearchMessage(
            role: .user, text: "rewrite the parser", ts: "t1")])
        #expect(wrapped.hints.title == "rewrite the parser")
        let bare = decoder.decode(line: line(
            #"{"source":"USER","type":"REQUEST","content":"plain ask","created_at":"t2"}"#))
        #expect(bare.messages == [SearchMessage(role: .user, text: "plain ask", ts: "t2")])
        let plan = decoder.decode(line: line(
            #"{"source":"MODEL","type":"PLANNER_RESPONSE","content":"step one","created_at":"t3"}"#))
        #expect(plan.messages == [SearchMessage(role: .assistant, text: "step one", ts: "t3")])
        // System/checkpoint/history rows carry no turns.
        for record in [
            #"{"source":"SYSTEM","type":"CHECKPOINT","content":"x"}"#,
            #"{"source":"MODEL","type":"HISTORY","content":"x"}"#,
            #"{"source":"USER","type":"OTHER","content":"x"}"#,
        ] {
            #expect(decoder.decode(line: line(record)).messages.isEmpty)
        }
    }

    // MARK: - markers gate

    @Test func markerGatesRejectForeignLines() {
        let checks: [(SearchDecoder.Harness, String, String)] = [
            (.qoder, #"{"type":"user","message":{"content":"x"}}"#, #"{"random":true}"#),
            (.codebuddy, #"{"type":"message","role":"user","content":"x"}"#, #"{"random":true}"#),
            (.gemini, #"{"sessionId":"s","messages":[]}"#, #"{"random":true}"#),
            (.copilot, #"{"type":"session.start","data":{}}"#, #"{"random":true}"#),
            (.grok, #"{"generated_title":"t"}"#, #"{"random":true}"#),
            (.hermes, #"{"session_id":"s","messages":[]}"#, #"{"random":true}"#),
            (.jcode, #"{"custom_title":"t","messages":[]}"#, #"{"random":true}"#),
            (.kiro, #"{"kind":"Prompt","data":{}}"#, #"{"random":true}"#),
            (.rovo, #"{"title":"t","cwd":"/x"}"#, #"{"random":true}"#),
            (.omp, #"{"type": "title"}"#, #"{"random":true}"#),
            (.openclaw, #"{"type": "session", "id": "s"}"#, #"{"random":true}"#),
            (.kimi, #"{"title":"t","agents":{}}"#, #"{"random":true}"#),
            (.muse, #"{"payload_type":"x"}"#, #"{"random":true}"#),
            (.antigravity, #"{"source":"USER","type":"REQUEST","content":"x"}"#, #"{"random":true}"#),
        ]
        for (harness, wanted, foreign) in checks {
            let decoder = SearchDecoder(harness)
            #expect(decoder.wants(line: Data(wanted.utf8)), "\(harness) accepts \(wanted)")
            #expect(!decoder.wants(line: Data(foreign.utf8)), "\(harness) rejects \(foreign)")
        }
    }
}
