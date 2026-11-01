import Foundation
import Testing
@testable import SWECore

/// Decoders turn one transcript line into `(messages, hints)`; the fixtures
/// are inline JSONL bytes in each harness's own on-disk spelling.
@Suite struct SearchDecoderTests {
    func line(_ json: String) -> Data { Data(json.utf8) }

    // MARK: - claude

    @Test func claudeUserLineDecodesTextAndHints() {
        let out = SearchDecoder.decode(agent: .claude, line: line(
            #"{"type":"user","timestamp":"2026-10-03T10:00:00Z","sessionId":"s1","cwd":"/tmp/demo","gitBranch":"main","message":{"role":"user","content":"fix the flaky test in Ledger.swift"}}"#))
        #expect(out.messages == [SearchMessage(role: .user, text: "fix the flaky test in Ledger.swift", ts: "2026-10-03T10:00:00Z")])
        #expect(out.hints.sessionId == "s1")
        #expect(out.hints.cwd == "/tmp/demo")
        #expect(out.hints.branch == "main")
        // The first user prompt is the fallback session title.
        #expect(out.hints.title == "fix the flaky test in Ledger.swift")
    }

    @Test func claudeAssistantBlocksBecomeTextAndToolMessages() {
        let out = SearchDecoder.decode(agent: .claude, line: line(
            #"{"type":"assistant","timestamp":"2026-10-03T10:00:01Z","sessionId":"s1","message":{"role":"assistant","content":[{"type":"text","text":"I'll look at the test."},{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/demo/Ledger.swift"}},{"type":"thinking","thinking":"pondering"},{"type":"text","text":"Then run it."}]}}"#))
        // Text blocks join and lead; tool_use follows; thinking is empty here.
        #expect(out.messages == [
            SearchMessage(role: .assistant, text: "I'll look at the test.\nThen run it.", ts: "2026-10-03T10:00:01Z"),
            SearchMessage(role: .tool, text: "Read: /tmp/demo/Ledger.swift", ts: "2026-10-03T10:00:01Z"),
        ])
    }

    @Test func claudeToolResultsOnAUserRecordAreToolMessages() {
        let out = SearchDecoder.decode(agent: .claude, line: line(
            #"{"type":"user","timestamp":"2026-10-03T10:00:02Z","sessionId":"s1","message":{"role":"user","content":[{"type":"tool_result","content":"first line\nsecond line"},{"type":"tool_result","content":[{"type":"text","text":"block A"},{"type":"text","text":"block B"}]}]}}"#))
        #expect(out.messages.map(\.role) == [.tool, .tool])
        #expect(out.messages[0].text == "first line\nsecond line")
        #expect(out.messages[1].text == "block A\nblock B")
        // A tool-result-only user record is not a title candidate.
        #expect(out.hints.title == nil)
    }

    @Test func claudeNonMessageAndMalformedLinesDecodeToNothing() {
        #expect(SearchDecoder.decode(agent: .claude, line: line(#"{"type":"summary","summary":"s"}"#)).messages.isEmpty)
        #expect(SearchDecoder.decode(agent: .claude, line: line(#"{"type":"progress","sessionId":"s1","data":{}}"#)).messages.isEmpty)
        #expect(SearchDecoder.decode(agent: .claude, line: line(#"{"type":"system","subtype":"init"}"#)).messages.isEmpty)
        #expect(SearchDecoder.decode(agent: .claude, line: line(#"{"type":"user","message":{"#)).messages.isEmpty)
        #expect(SearchDecoder.decode(agent: .claude, line: line("")).messages.isEmpty)
    }

    @Test func claudeTitleRecordsSurfaceExplicitTitles() {
        var decoder = SearchDecoder(.claude)
        let first = decoder.decode(line: line(
            #"{"type":"user","sessionId":"s1","message":{"role":"user","content":"raw prompt"}}"#))
        #expect(first.hints.title == "raw prompt")
        // An explicit title lands after the prompt and wins the merge.
        let renamed = decoder.decode(line: line(
            #"{"type":"custom-title","customTitle":"Renamed session","sessionId":"s1"}"#))
        #expect(renamed.hints.title == "Renamed session")
        let auto = decoder.decode(line: line(
            #"{"type":"ai-title","aiTitle":"Auto title","sessionId":"s1"}"#))
        #expect(auto.hints.title == "Auto title")
    }

    @Test func messageTextIsCappedAt256KBOnCharacterBoundaries() {
        let blob = String(repeating: "a", count: 300_000)
        let out = SearchDecoder.decode(agent: .claude, line: line(
            #"{"type":"user","sessionId":"s1","message":{"role":"user","content":"\#(blob)"}}"#))
        let message = try? #require(out.messages.first)
        #expect(message?.text.utf16.count == SearchText.messageLimit)
    }

    @Test func toolCallArgumentsAreCappedAt2000() {
        let command = String(repeating: "x", count: 3000)
        let out = SearchDecoder.decode(agent: .claude, line: line(
            #"{"type":"assistant","sessionId":"s1","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"\#(command)"}}]}}"#))
        let tool = try? #require(out.messages.first)
        #expect(tool?.text.hasPrefix("Bash: ") == true)
        #expect(tool?.text.utf16.count == "Bash: ".utf16.count + 2000)
    }

    // MARK: - codex

    @Test func codexSessionMetaSurfacesHintsAndHistoryMode() {
        var decoder = SearchDecoder(.codex)
        let meta = decoder.decode(line: line(
            #"{"timestamp":"2026-10-20T02:55:33.500Z","type":"session_meta","payload":{"id":"thread-1","cwd":"/repo/app","history_mode":"paginated","git":{"branch":"feat/x"},"title":"Fix admission loop"}}"#))
        #expect(meta.hints.sessionId == "thread-1")
        #expect(meta.hints.cwd == "/repo/app")
        #expect(meta.hints.branch == "feat/x")
        #expect(meta.hints.title == "Fix admission loop")
        #expect(meta.hints.nonUserOrigin == false)
        // session_meta is also an explicit title, so the later prompt does
        // not override it.
        let prompt = decoder.decode(line: line(
            #"{"type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[{"type":"text","text":"the prompt"}]}}}"#))
        #expect(prompt.hints.title == nil)
    }

    @Test func codexNonUserOriginIsFlagged() {
        let subagent = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"session_meta","payload":{"id":"t","thread_source":"subagent"}}"#))
        #expect(subagent.hints.nonUserOrigin == true)
        let spawned = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"session_meta","payload":{"id":"t","source":{"subagent":{"thread_spawn":{"depth":1}}}}}"#))
        #expect(spawned.hints.nonUserOrigin == true)
        let user = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"session_meta","payload":{"id":"t","thread_source":"user"}}"#))
        #expect(user.hints.nonUserOrigin == false)
        let cli = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"session_meta","payload":{"id":"t","source":"cli"}}"#))
        #expect(cli.hints.nonUserOrigin == false)
    }

    @Test func codexResponseItemsDecodeOutsidePaginatedMode() {
        var decoder = SearchDecoder(.codex)
        let user = decoder.decode(line: line(
            #"{"type":"response_item","timestamp":"t1","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello codex"}]}}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "hello codex", ts: "t1")])
        let assistant = decoder.decode(line: line(
            #"{"type":"response_item","timestamp":"t2","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"on it"}]}}"#))
        #expect(assistant.messages == [SearchMessage(role: .assistant, text: "on it", ts: "t2")])
        // Developer context is not conversation.
        let developer = decoder.decode(line: line(
            #"{"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"injected"}]}}"#))
        #expect(developer.messages.isEmpty)
    }

    @Test func codexToolCallsAndOutputsDecode() {
        let call = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"response_item","timestamp":"t","payload":{"type":"function_call","name":"exec_command","arguments":"{\"cmd\":\"ls -la\"}"}}"#))
        #expect(call.messages == [SearchMessage(role: .tool, text: #"exec_command: {"cmd":"ls -la"}"#, ts: "t")])

        let custom = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"response_item","payload":{"type":"custom_tool_call","name":"exec","input":"text(await tools.exec_command())"}}"#))
        #expect(custom.messages.first?.text == "exec: text(await tools.exec_command())")

        let shell = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"response_item","payload":{"type":"local_shell_call","action":{"type":"exec","command":["ls","-la"]}}}"#))
        #expect(shell.messages.first?.text == "tool: ls -la")

        let stringOutput = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"response_item","payload":{"type":"function_call_output","output":"total 8"}}"#))
        #expect(stringOutput.messages.first?.text == "total 8")

        let recordOutput = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"response_item","payload":{"type":"custom_tool_call_output","output":{"content":"wrapped"}}}"#))
        #expect(recordOutput.messages.first?.text == "wrapped")

        let blocksOutput = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"response_item","payload":{"type":"custom_tool_call_output","output":[{"type":"input_text","text":"line A"},{"type":"input_text","text":"line B"}]}}"#))
        #expect(blocksOutput.messages.first?.text == "line A\nline B")
    }

    @Test func codexPaginatedModeReadsItemCompletedOnly() {
        var decoder = SearchDecoder(.codex)
        _ = decoder.decode(line: line(
            #"{"type":"session_meta","payload":{"id":"t","history_mode":"paginated"}}"#))
        // response_item is skipped entirely once history is paginated.
        let item = decoder.decode(line: line(
            #"{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"dup"}]}}"#))
        #expect(item.messages.isEmpty)

        let user = decoder.decode(line: line(
            #"{"type":"event_msg","timestamp":"t1","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[{"type":"text","text":"the real prompt"}]}}}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "the real prompt", ts: "t1")])

        let agent = decoder.decode(line: line(
            #"{"type":"event_msg","payload":{"type":"item_completed","item":{"type":"AgentMessage","content":[{"type":"text","text":"the reply"}]}}}"#))
        #expect(agent.messages == [SearchMessage(role: .assistant, text: "the reply", ts: nil)])

        let exec = decoder.decode(line: line(
            #"{"type":"event_msg","payload":{"type":"item_completed","item":{"type":"CommandExecution","command":["/bin/zsh","-lc","rg foo"],"aggregated_output":"match results"}}}"#))
        #expect(exec.messages.map(\.text) == ["shell: /bin/zsh -lc rg foo", "match results"])

        let change = decoder.decode(line: line(
            #"{"type":"event_msg","payload":{"type":"item_completed","item":{"type":"FileChange","changes":{"/a/b.swift":{"unified_diff":"@@ -1 +1 @@"}}}}}"#))
        #expect(change.messages.map(\.text) == ["apply_patch: /a/b.swift", "@@ -1 +1 @@"])

        // Bookkeeping items carry no rows.
        let reasoning = decoder.decode(line: line(
            #"{"type":"event_msg","payload":{"type":"item_completed","item":{"type":"Reasoning","content":[{"type":"text","text":"secret"}]}}}"#))
        #expect(reasoning.messages.isEmpty)
    }

    @Test func codexItemCompletedIsIgnoredWithoutPaginatedHistory() {
        let out = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"event_msg","payload":{"type":"item_completed","item":{"type":"UserMessage","content":[{"type":"text","text":"prompt"}]}}}"#))
        #expect(out.messages.isEmpty)
        // And legacy user/agent message events decode in either mode.
        let legacy = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"event_msg","payload":{"type":"user_message","message":"old prompt"}}"#))
        #expect(legacy.messages == [SearchMessage(role: .user, text: "old prompt")])
        let agent = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"event_msg","payload":{"type":"agent_message","message":"old reply"}}"#))
        #expect(agent.messages == [SearchMessage(role: .assistant, text: "old reply")])
    }

    @Test func codexStateRoundTripsThroughSavedState() {
        var decoder = SearchDecoder(.codex)
        _ = decoder.decode(line: line(
            #"{"type":"session_meta","payload":{"id":"t","history_mode":"paginated"}}"#))
        let saved = decoder.savedState()
        var resumed = SearchDecoder(.codex, savedState: saved)
        let item = resumed.decode(line: line(
            #"{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"dup"}]}}"#))
        #expect(item.messages.isEmpty)
    }

    @Test func codexTurnContextUpdatesCwd() {
        let out = SearchDecoder.decode(agent: .codex, line: line(
            #"{"type":"turn_context","payload":{"cwd":"/repo/moved"}}"#))
        #expect(out.hints.cwd == "/repo/moved")
    }

    // MARK: - pi

    @Test func piSessionLineSurfacesHints() {
        let out = SearchDecoder.decode(agent: .pi, line: line(
            #"{"type": "session", "id": "pi-1", "cwd": "/repo/pi", "timestamp": "2026-05-12T06:40:00Z", "version": 3}"#))
        #expect(out.hints.sessionId == "pi-1")
        #expect(out.hints.cwd == "/repo/pi")
    }

    @Test func piMessagesDecode() {
        var decoder = SearchDecoder(.pi)
        let user = decoder.decode(line: line(
            #"{"type": "message", "id": "m1", "timestamp": "t1", "message": {"role": "user", "content": [{"type": "text", "text": "ls the dir"}]}}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "ls the dir", ts: "t1")])
        #expect(user.hints.title == "ls the dir")

        let assistant = decoder.decode(line: line(
            #"{"type": "message", "timestamp": "t2", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "toolCall", "id": "call_1", "name": "bash", "arguments": {"command": "ls -la"}}]}}"#))
        #expect(assistant.messages == [SearchMessage(role: .tool, text: "bash: ls -la", ts: "t2")])

        let result = decoder.decode(line: line(
            #"{"type": "message", "timestamp": "t3", "message": {"role": "toolResult", "toolCallId": "call_1", "toolName": "bash", "content": [{"type": "text", "text": "AGENTS.md\nREADME.md"}], "isError": false}}"#))
        #expect(result.messages == [SearchMessage(role: .tool, text: "bash: AGENTS.md\nREADME.md", ts: "t3")])
    }

    @Test func piSkipsNonMessageLinesAndReadsSessionInfo() {
        #expect(SearchDecoder.decode(agent: .pi, line: line(
            #"{"type": "model_change", "provider": "x", "modelId": "y"}"#)).messages.isEmpty)
        #expect(SearchDecoder.decode(agent: .pi, line: line(
            #"{"type": "thinking_level_change", "thinkingLevel": "high"}"#)).messages.isEmpty)
        let info = SearchDecoder.decode(agent: .pi, line: line(
            #"{"type": "session_info", "name": "my session name"}"#))
        #expect(info.hints.title == "my session name")
    }

    // MARK: - devin

    @Test func devinChatMessageRowsDecode() {
        var decoder = SearchDecoder(.devin)
        let user = decoder.decode(line: line(
            #"{"message_id":"m1","role":"user","content":"what drives exist","metadata":{"created_at":"2026-09-15T15:48:00Z"}}"#))
        #expect(user.messages == [SearchMessage(role: .user, text: "what drives exist", ts: "2026-09-15T15:48:00Z")])

        let calls = decoder.decode(line: line(
            #"{"message_id":"m2","role":"assistant","content":"","tool_calls":[{"id":"c1","name":"exec","arguments":{"command":"ls /Drives"}}],"metadata":{"created_at":"2026-09-15T15:48:56Z"}}"#))
        #expect(calls.messages == [SearchMessage(role: .tool, text: "exec: ls /Drives", ts: "2026-09-15T15:48:56Z")])

        // A tool row is labelled with the call it answers, joined by id.
        let result = decoder.decode(line: line(
            #"{"message_id":"m3","role":"tool","content":"output here","tool_call_id":"c1","metadata":{"created_at":"2026-09-15T15:48:57Z"}}"#))
        #expect(result.messages == [SearchMessage(role: .tool, text: "exec: output here", ts: "2026-09-15T15:48:57Z")])

        #expect(decoder.decode(line: line(
            #"{"message_id":"m0","role":"system","content":"<system_info>x</system_info>"}"#)).messages.isEmpty)
    }

    @Test func devinAtifDocumentDecodes() {
        let document = line(#"""
        {
          "schema_version": "ATIF-v1.7",
          "session_id": "alluring-collard",
          "working_directory": "/repo/demo",
          "agent": {"name": "devin"},
          "steps": [
            {"step_id": 1, "timestamp": "2026-10-19T14:40:53Z", "source": "system", "message": "You are Devin..."},
            {"step_id": 9, "timestamp": "2026-10-19T14:41:15Z", "source": "user", "message": "which drives exist?"},
            {"step_id": 11, "timestamp": "2026-10-19T14:41:19Z", "source": "agent", "message": "",
             "tool_calls": [{"tool_call_id": "c1", "function_name": "exec", "arguments": {"command": "ls -la /Drives"}}],
             "observation": {"results": [{"source_call_id": "c1", "content": "finetuned official seafile"}]}},
            {"step_id": 13, "timestamp": "2026-10-19T14:41:28Z", "source": "agent", "message": "Found them: a and b."}
          ]
        }
        """#)
        let out = SearchDecoder.decode(agent: .devin, line: document)
        #expect(out.hints.sessionId == "alluring-collard")
        #expect(out.hints.cwd == "/repo/demo")
        #expect(out.messages == [
            SearchMessage(role: .user, text: "which drives exist?", ts: "2026-10-19T14:41:15Z"),
            SearchMessage(role: .tool, text: "exec: ls -la /Drives", ts: "2026-10-19T14:41:19Z"),
            SearchMessage(role: .tool, text: "finetuned official seafile", ts: "2026-10-19T14:41:19Z"),
            SearchMessage(role: .assistant, text: "Found them: a and b.", ts: "2026-10-19T14:41:28Z"),
        ])
        #expect(out.hints.title == "which drives exist?")
    }

    @Test func devinBareStepDecodes() {
        let out = SearchDecoder.decode(agent: .devin, line: line(
            #"{"step_id": 5, "timestamp": "t", "source": "user", "message": {"content": "hi there"}}"#))
        #expect(out.messages == [SearchMessage(role: .user, text: "hi there", ts: "t")])
    }

    // MARK: - title candidate ordering

    @Test func explicitTitleSuppressesThePromptCandidate() {
        var decoder = SearchDecoder(.pi)
        _ = decoder.decode(line: line(#"{"type": "session_info", "name": "chosen name"}"#))
        let prompt = decoder.decode(line: line(
            #"{"type": "message", "timestamp": "t", "message": {"role": "user", "content": [{"type": "text", "text": "typed prompt"}]}}"#))
        #expect(prompt.hints.title == nil)
    }

    @Test func promptCandidateIsEmittedOncePerFile() {
        var decoder = SearchDecoder(.claude)
        let first = decoder.decode(line: line(
            #"{"type":"user","sessionId":"s","message":{"role":"user","content":"first\nprompt\t here"}}"#))
        #expect(first.hints.title == "first prompt here")
        let second = decoder.decode(line: line(
            #"{"type":"user","sessionId":"s","message":{"role":"user","content":"second"}}"#))
        #expect(second.hints.title == nil)
    }
}
