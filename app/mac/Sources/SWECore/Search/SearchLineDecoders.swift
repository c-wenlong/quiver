import Foundation

/// One harness's transcript lines → `(messages, hints)` for the search index.
///
/// Stateful per file, the same shape `FileLogParser` uses for the ledger:
/// codex cannot tell which record type carries the conversation until its
/// `session_meta` says `history_mode`, devin's `chat_message` rows join a
/// tool result to its call by id across lines, and every decoder stops
/// emitting the first-user-prompt title candidate once an explicit title
/// record lands. The index persists no decoder state, so an appended read
/// instead primes a fresh decoder from the file's head —
/// `primeForAppend()`/`prime(object:)`, which is all the appendable
/// harnesses need — and `savedState()` still round-trips the rest.
///
/// Lines that cannot carry messages are rejected on a cheap `Data.range(of:)`
/// marker before JSON parsing (the `ClaudeLogParser` trick), a malformed line
/// decodes to nothing, and nothing here ever throws or touches the file
/// system — the scan wiring is a later milestone.
public struct SearchDecoder {
    /// The JSONL transcript formats the index reads; keys match Orca's agent
    /// ids (`primeAgent` is `"prime-agent"`). `cursor`/`droid` are JSONL like
    /// the rest; `cline`/`rovo`/`kiro`/`grok`/`kimi`'s "line" is a whole
    /// document — or one of two paired documents — the way devin's is.
    public enum Harness: String, Sendable, CaseIterable {
        case claude, qoder, codebuddy, codex, gemini, copilot, cursor, grok,
             devin, hermes, jcode, kiro, rovo, pi, omp, primeAgent = "prime-agent",
             openclaw, droid, cline, kimi, muse, antigravity
    }

    private let harness: Harness
    /// Set once the file yields its first-user-prompt title candidate.
    private var promptTitleSent = false
    /// Set when an explicit title record lands; suppresses the prompt
    /// candidate wherever the two fall in the file.
    private var explicitTitleSeen = false
    private var codex = SearchCodexDecoder()
    private var devin = SearchDevinDecoder()
    private var omp = SearchOmpDecoder()
    private var qoder = SearchQoderDecoder()
    private var codebuddy = SearchCodebuddyDecoder()
    private var kimi = SearchKimiDecoder()
    private var muse = SearchMuseDecoder()

    /// Line-parser state, saved with a file's scan cursor. Round-trips
    /// through `savedState()`/`init(_:savedState:)` like `FileLogParser`'s.
    struct State: Codable, Equatable, Sendable {
        var promptTitleSent = false
        var explicitTitleSeen = false
        var codex = SearchCodexDecoder.State()
        var devin = SearchDevinDecoder.State()
        var omp = SearchOmpDecoder.State()
        var qoder = SearchQoderDecoder.State()
        var codebuddy = SearchCodebuddyDecoder.State()
        var kimi = SearchKimiDecoder.State()
        var muse = SearchMuseDecoder.State()
    }

    public init(_ harness: Harness, savedState: Data? = nil) {
        self.harness = harness
        if let savedState, let state = try? JSONDecoder().decode(State.self, from: savedState) {
            promptTitleSent = state.promptTitleSent
            explicitTitleSeen = state.explicitTitleSeen
            codex.state = state.codex
            devin.state = state.devin
            omp.state = state.omp
            qoder.state = state.qoder
            codebuddy.state = state.codebuddy
            kimi.state = state.kimi
            muse.state = state.muse
        }
    }

    public func savedState() -> Data? {
        try? JSONEncoder().encode(State(
            promptTitleSent: promptTitleSent,
            explicitTitleSeen: explicitTitleSeen,
            codex: codex.state,
            devin: devin.state,
            omp: omp.state,
            qoder: qoder.state,
            codebuddy: codebuddy.state,
            kimi: kimi.state,
            muse: muse.state))
    }

    /// The appended-span setup Python spells `prompt_title_sent = True`:
    /// the span's first user prompt must not re-title the session — only
    /// an explicit title record in the appended lines may.
    public mutating func primeForAppend() {
        promptTitleSent = true
    }

    /// Feed a line from before an append's offset for its decoder state
    /// only — Python's `Decoder.prime`. Codex's first `session_meta`
    /// decides which record type carries the conversation, and a fresh
    /// decoder on the tail never saw it; other records and harnesses
    /// carry nothing an append needs.
    public mutating func prime(object: [String: Any]) {
        if harness == .codex, object["type"] as? String == "session_meta" {
            _ = codex.decode(object: object)
        }
    }

    /// Whether the line's bytes pass the harness's marker gate — the check
    /// `decode(line:)` runs before parsing, exported so a caller can union
    /// the two consumers' gates and run one `JSONSerialization` for both.
    public func wants(line: Data) -> Bool {
        switch harness {
        case .claude: SearchClaudeDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .qoder: SearchQoderDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .codebuddy: SearchCodebuddyDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .codex: SearchCodexDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .gemini: SearchGeminiDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .copilot: SearchCopilotDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .cursor: SearchCursorDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .grok: SearchGrokDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .devin: SearchDevinDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .hermes: SearchHermesDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .jcode: SearchJcodeDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .kiro: SearchKiroDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .rovo: SearchRovoDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .pi: SearchPiDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .omp: SearchOmpDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .primeAgent, .openclaw:
            SearchMessageGraphDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .droid: SearchDroidDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .cline: SearchClineDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .kimi: SearchKimiDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .muse: SearchMuseDecoder.markers.contains(where: { line.range(of: $0) != nil })
        case .antigravity:
            SearchAntigravityDecoder.markers.contains(where: { line.range(of: $0) != nil })
        }
    }

    /// One already-parsed transcript line → the messages and hints it
    /// carries — the shared-parse counterpart of `decode(line:)`.
    public mutating func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        let output = switch harness {
        case .claude: SearchClaudeDecoder.decode(object: object)
        case .qoder: qoder.decode(object: object)
        case .codebuddy: codebuddy.decode(object: object)
        case .codex: codex.decode(object: object)
        case .gemini: SearchGeminiDecoder.decode(object: object)
        case .copilot: SearchCopilotDecoder.decode(object: object)
        case .cursor: SearchCursorDecoder.decode(object: object)
        case .grok: SearchGrokDecoder.decode(object: object)
        case .devin: devin.decode(object: object)
        case .hermes: SearchHermesDecoder.decode(object: object)
        case .jcode: SearchJcodeDecoder.decode(object: object)
        case .kiro: SearchKiroDecoder.decode(object: object)
        case .rovo: SearchRovoDecoder.decode(object: object)
        case .pi: SearchPiDecoder.decode(object: object)
        case .omp: omp.decode(object: object)
        case .primeAgent, .openclaw: SearchMessageGraphDecoder.decode(object: object)
        case .droid: SearchDroidDecoder.decode(object: object)
        case .cline: SearchClineDecoder.decode(object: object)
        case .kimi: kimi.decode(object: object)
        case .muse: muse.decode(object: object)
        case .antigravity: SearchAntigravityDecoder.decode(object: object)
        }
        return finish(output)
    }

    /// One transcript line → the messages and hints it carries. For devin the
    /// "line" may also be a whole `transcripts/*.json` document — those files
    /// are pretty-printed, so one blob is the natural unit there too.
    public mutating func decode(line: Data) -> (messages: [SearchMessage], hints: SearchHints) {
        guard wants(line: line),
              let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
        else { return ([], SearchHints()) }
        return decode(object: object)
    }

    /// Buffered output still held at end of file — Kimi joins streamed
    /// `content.part` chunks into one message per `step.end` and flushes a
    /// trailing unterminated step here. All other decoders hold nothing.
    public mutating func endOfFile() -> (messages: [SearchMessage], hints: SearchHints) {
        switch harness {
        case .kimi: return finish(kimi.endOfFile())
        default: return ([], SearchHints())
        }
    }

    /// The title bookkeeping `decode` applies after the per-harness walk:
    /// the first user text is the fallback session title; an explicit title
    /// record supersedes it wherever it lands, and stops later prompts from
    /// clobbering it.
    private mutating func finish(
        _ output: (messages: [SearchMessage], hints: SearchHints)
    ) -> (messages: [SearchMessage], hints: SearchHints) {
        var output = output
        // The first user text is the fallback session title; an explicit
        // title record supersedes it wherever it lands, and stops later
        // prompts from clobbering it.
        if output.hints.title != nil {
            explicitTitleSeen = true
        } else if !promptTitleSent, !explicitTitleSeen, !output.hints.injectedUserTurn,
                  let user = output.messages.first(where: { $0.role == .user }),
                  let candidate = SearchText.titleCandidate(user.text) {
            promptTitleSent = true
            output.hints.title = candidate
        }
        return output
    }

    /// One line, decoded with fresh per-file state — for single records and
    /// tests.
    public static func decode(agent: Harness, line: Data) -> (messages: [SearchMessage], hints: SearchHints) {
        var decoder = SearchDecoder(agent)
        return decoder.decode(line: line)
    }
}

/// Claude Code `~/.claude/projects/**/*.jsonl`: `type` `user`/`assistant`
/// records carry `message.content` block arrays — text blocks keep the
/// record's role, `tool_use` and `tool_result` become `.tool` messages.
/// `custom-title`/`ai-title` records are the explicit session title.
/// `sessionId`, `cwd` and `gitBranch` ride on every message line.
/// `subagents/` files are the caller's concern, not this decoder's.
enum SearchClaudeDecoder {
    /// Claude writes compact JSON, so markers carry no space after the colon.
    static let markers = [
        #""type":"user""#, #""type":"assistant""#, #""custom-title""#, #""ai-title""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        hints.sessionId = JSONValue.string(object["sessionId"])
        hints.cwd = JSONValue.string(object["cwd"])
        hints.branch = JSONValue.string(object["gitBranch"])
        let ts = object["timestamp"] as? String
        switch object["type"] as? String {
        case "custom-title":
            hints.title = JSONValue.string(object["customTitle"])
        case "ai-title":
            hints.title = JSONValue.string(object["aiTitle"])
        case "user", "assistant":
            let type = object["type"] as? String
            let message = object["message"] as? [String: Any]
            let messages = SearchContent.messages(
                role: type == "user" ? .user : .assistant,
                content: message?["content"], ts: ts)
            // Meta prompts (injected context) index like any other user turn
            // but only seed Orca's last-resort metaTitle — which SWE has
            // no slot for — so they must not seed the title candidate.
            if type == "user" {
                hints.injectedUserTurn = object["isMeta"] as? Bool == true
                    || messages.first(where: { $0.role == .user })
                        .map { SearchInjectedTurn.isKnown($0.text) } ?? false
            }
            return (messages, hints)
        default:
            break
        }
        return ([], hints)
    }
}

/// Codex `~/.codex/sessions/**/*.jsonl` rollouts. Which records hold the
/// conversation depends on `session_meta.payload.history_mode`:
///
/// - `paginated` (current Codex): `event_msg` `item_completed` items —
///   `UserMessage`/`AgentMessage` text, `CommandExecution`, `FileChange`.
///   `response_item` records carry the same turns in a lower-level shape and
///   are skipped so nothing double-indexes.
/// - anything else: `response_item` `message` / `function_call` /
///   `custom_tool_call` / `local_shell_call` / `*_output` payloads, plus
///   legacy `event_msg` `user_message`/`agent_message` events either mode
///   accepts.
struct SearchCodexDecoder {
    struct State: Codable, Equatable, Sendable {
        /// Only the first `session_meta` counts — a fork copies its parent's
        /// in after its own.
        var sawSessionMeta = false
        var historyMode: String?
    }

    var state = State()

    /// Rollout record types this reads. Codex writes `"type": "x"` with a
    /// space after the colon, so markers are the bare record names.
    static let markers = [
        #""response_item""#, #""event_msg""#, #""session_meta""#, #""turn_context""#,
    ].map { Data($0.utf8) }

    /// The record an append's head re-scan looks for — the one line a
    /// fresh decoder needs from before the offset (`Decoder.prime`).
    static let sessionMetaMarker = Data(#""session_meta""#.utf8)

    mutating func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        let ts = object["timestamp"] as? String
        let payload = object["payload"] as? [String: Any] ?? [:]
        switch object["type"] as? String {
        case "session_meta":
            guard !state.sawSessionMeta else { return ([], hints) }
            state.sawSessionMeta = true
            state.historyMode = JSONValue.string(payload["history_mode"])
            hints.sessionId = JSONValue.string(payload["id"]) ?? JSONValue.string(payload["session_id"])
            hints.cwd = JSONValue.string(payload["cwd"])
            if let git = payload["git"] as? [String: Any] {
                hints.branch = JSONValue.string(git["branch"]) ?? JSONValue.string(git["current_branch"])
            }
            hints.title = JSONValue.string(payload["title"])
                ?? JSONValue.string(payload["thread_name"])
                ?? JSONValue.string(payload["threadName"])
            hints.nonUserOrigin = Self.nonUserOrigin(payload)
            return ([], hints)
        case "turn_context":
            hints.cwd = JSONValue.string(payload["cwd"])
            return ([], hints)
        case "response_item":
            return (state.historyMode == "paginated" ? [] : responseItem(payload, ts: ts), hints)
        case "event_msg":
            return (eventMessage(payload, ts: ts), hints)
        default:
            return ([], hints)
        }
    }

    /// A `response_item` payload → messages (non-paginated history only).
    /// `developer`/`system` roles are injected context, not conversation.
    private func responseItem(_ payload: [String: Any], ts: String?) -> [SearchMessage] {
        switch payload["type"] as? String {
        case "message":
            return SearchContent.messages(
                role: SearchRole(rawValue: payload["role"] as? String ?? ""),
                content: payload["content"], ts: ts)
        case "function_call", "custom_tool_call", "local_shell_call":
            var input = payload["arguments"] ?? payload["input"] ?? payload["action"]
            // `action` wraps shell calls as {"type":"exec","command":[...]};
            // Orca joins the argv into one searchable command string.
            if let action = input as? [String: Any], let argv = action["command"] as? [Any] {
                var normalized = action
                normalized["command"] = argv.compactMap { $0 as? String }.joined(separator: " ")
                input = normalized
            }
            var messages: [SearchMessage] = []
            SearchContent.appendTool(&messages, SearchContent.toolCallText(
                name: payload["name"] ?? "tool", input: input), ts: ts)
            return messages
        case "function_call_output", "custom_tool_call_output":
            var output = payload["output"]
            if let record = output as? [String: Any] {
                output = record["content"] ?? record["output"]
            }
            var messages: [SearchMessage] = []
            SearchContent.appendTool(&messages, SearchContent.toolResultText(output), ts: ts)
            return messages
        default:
            // reasoning, agent_message, additional_tools, ghost snapshots, …
            return []
        }
    }

    /// An `event_msg` payload → messages. `item_completed` is the paginated
    /// history's displayed unit and is ignored in other modes, where
    /// `response_item` already holds the same content.
    private func eventMessage(_ payload: [String: Any], ts: String?) -> [SearchMessage] {
        switch payload["type"] as? String {
        case "item_completed":
            guard state.historyMode == "paginated" else { return [] }
            return completedItem(payload["item"] as? [String: Any], ts: ts)
        case "user_message":
            return SearchContent.messages(role: .user, content: payload["message"], ts: ts)
        case "agent_message":
            return SearchContent.messages(role: .assistant, content: payload["message"], ts: ts)
        default:
            return []
        }
    }

    /// The conversational `item_completed` kinds; `Reasoning`,
    /// `SubAgentActivity`, `McpToolCall`, `ContextCompaction` and friends are
    /// bookkeeping Orca skips too.
    private func completedItem(_ item: [String: Any]?, ts: String?) -> [SearchMessage] {
        guard let item else { return [] }
        var messages: [SearchMessage] = []
        switch item["type"] as? String {
        case "UserMessage", "user_message":
            return SearchContent.messages(role: .user, content: item["content"], ts: ts)
        case "AgentMessage", "agent_message":
            return SearchContent.messages(role: .assistant, content: item["content"], ts: ts)
        case "CommandExecution", "command_execution":
            let command: Any? = (item["command"] as? [Any]).map {
                $0.compactMap { $0 as? String }.joined(separator: " ")
            } ?? item["command"]
            SearchContent.appendTool(&messages, SearchContent.toolCallText(name: "shell", input: command), ts: ts)
            SearchContent.appendTool(&messages, SearchContent.toolResultText(
                item["aggregated_output"] ?? item["aggregatedOutput"]), ts: ts)
        case "FileChange", "file_change":
            for (path, value) in item["changes"] as? [String: Any] ?? [:] {
                let change = value as? [String: Any]
                SearchContent.appendTool(&messages, SearchContent.toolCallText(
                    name: "apply_patch", input: ["path": path]), ts: ts)
                SearchContent.appendTool(&messages, SearchContent.toolResultText(
                    change?["unified_diff"] ?? change?["content"]), ts: ts)
            }
        default:
            return []
        }
        return messages
    }

    /// Codex's own classification of a thread as other than the user's own —
    /// `source: {subagent|internal: …}` or a `thread_source` that is not
    /// `"user"` (Orca's `readCodexNonUserOrigin`: a stated `thread_source`
    /// outranks `source`, and an unreadable shape is never treated as a
    /// spawn, since dropping the user's own thread is the worse error).
    static func nonUserOrigin(_ payload: [String: Any]) -> Bool {
        if let threadSource = JSONValue.string(payload["thread_source"])
            ?? JSONValue.string(payload["threadSource"]) {
            return threadSource.lowercased() != "user"
        }
        guard let tag = unionTag(payload["source"]) else { return false }
        return tag == "subagent" || tag == "internal"
    }

    /// The outer tag of a Codex externally-tagged union: a bare string
    /// (`"cli"`) or a one-key object (`{"subagent": …}`).
    private static func unionTag(_ value: Any?) -> String? {
        if let bare = JSONValue.string(value) { return bare }
        if let record = value as? [String: Any], record.count == 1, let key = record.keys.first {
            return JSONValue.string(key)
        }
        return nil
    }
}

/// Pi `~/.pi/agent/sessions/<project>/*.jsonl`: `type` `message` records hold
/// `message.{role, content}` — `user`/`assistant` text blocks and `toolCall`
/// blocks decode through `SearchContent`, while `toolResult` is a role of its
/// own that names the call it answers. `session` records carry id + cwd, and
/// a `session_info` record is a `/name` rename — pi's only title source.
enum SearchPiDecoder {
    /// Pi writes `"type": "x"` with a space, so markers are bare names;
    /// `"session` (no closing quote) covers both `"session"` and
    /// `"session_info"`.
    static let markers = [#""message""#, #""session"#].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        switch object["type"] as? String {
        case "session":
            hints.sessionId = JSONValue.string(object["id"])
            hints.cwd = JSONValue.string(object["cwd"]) ?? JSONValue.string(object["workspace"])
        case "session_info":
            hints.title = JSONValue.string(object["name"])
        case "message":
            guard let message = object["message"] as? [String: Any] else { return ([], hints) }
            let ts = object["timestamp"] as? String
            if message["role"] as? String == "toolResult" {
                // The result record names the call it answers, so its row is
                // labelled the way other harnesses' call rows are.
                guard let text = SearchContent.toolResultText(message["content"]) else {
                    return ([], hints)
                }
                let labeled = JSONValue.string(message["toolName"]).map { "\($0): \(text)" } ?? text
                var messages: [SearchMessage] = []
                SearchContent.appendTool(&messages, labeled, ts: ts)
                return (messages, hints)
            }
            return (SearchContent.messages(
                role: SearchRole(rawValue: message["role"] as? String ?? ""),
                content: message["content"], ts: ts), hints)
        default:
            break
        }
        return ([], hints)
    }
}

/// Devin — `~/.local/share/devin/cli`. Three record shapes decode here:
///
/// - a whole ATIF transcript document (`transcripts/*.json`, fed as one
///   blob): `steps[]` with `source` user/agent/system plus `session_id` and
///   `working_directory` hints. Agent steps can carry text, `tool_calls`
///   (`function_name`/`arguments`) and the calls' `observation.results`.
/// - one ATIF `steps` element on its own.
/// - one `message_nodes.chat_message` record the way `sessions.db` stores it
///   (`{role, content, tool_calls, tool_call_id, metadata.created_at}`) —
///   `role: "tool"` rows carry no name, so the call they answer is
///   remembered by id, mirroring swe's `_read_devin`.
///
/// System steps and `system`/`developer` rows are setup noise and skipped.
struct SearchDevinDecoder {
    struct State: Codable, Equatable, Sendable {
        /// `tool_call_id` → call name for `role: "tool"` chat_messages.
        var callNames: [String: String] = [:]
    }

    var state = State()

    /// `"steps"` marks a transcript document, `"role"` a chat_message record,
    /// `"source"`/`"step_id"` a bare step.
    static let markers = [
        #""steps""#, #""role""#, #""source""#, #""step_id""#,
    ].map { Data($0.utf8) }

    mutating func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        if let steps = object["steps"] as? [Any] {
            hints.sessionId = JSONValue.string(object["session_id"]) ?? JSONValue.string(object["sessionId"])
            hints.cwd = JSONValue.string(object["working_directory"])
            return (steps.flatMap { atifStep($0 as? [String: Any]) }, hints)
        }
        if object["role"] != nil {
            return (chatMessage(object), hints)
        }
        if object["source"] != nil || object["step_id"] != nil {
            return (atifStep(object), hints)
        }
        return ([], hints)
    }

    /// `message` as a plain string, `{content}` or a parts array — ATIF
    /// allows all three — falling back to `content`/`text` (Orca's
    /// `extractDevinStepText`).
    private func stepText(_ step: [String: Any]) -> Any? {
        if let text = step["message"] as? String { return text }
        if let record = step["message"] as? [String: Any] {
            return record["content"] ?? record["text"]
        }
        return step["message"] ?? step["content"] ?? step["text"]
    }

    /// One ATIF step (Orca's `consumeDevinSessionStep`, extended to the tool
    /// calls and observations a search index wants): `is_user_input`
    /// overrides `source`, `system` steps are dropped, and an `agent` step —
    /// or any step carrying `tool_calls` — decodes its text, its calls and
    /// the results `observation` embeds in the same step.
    private func atifStep(_ step: [String: Any]?) -> [SearchMessage] {
        guard let step else { return [] }
        let metadata = step["metadata"] as? [String: Any]
        let ts = (step["timestamp"] as? String) ?? (metadata?["created_at"] as? String)
        let source = step["source"] as? String
        if source == "system" { return [] }
        if source == "user" || metadata?["is_user_input"] as? Bool == true {
            return SearchContent.messages(role: .user, content: stepText(step), ts: ts)
        }
        guard source == "agent" || step["role"] as? String == "assistant" || step["tool_calls"] != nil
        else { return [] }
        var messages = SearchContent.messages(role: .assistant, content: stepText(step), ts: ts)
        for call in step["tool_calls"] as? [Any] ?? [] {
            guard let record = call as? [String: Any] else { continue }
            SearchContent.appendTool(&messages, SearchContent.toolCallText(
                name: record["function_name"] ?? record["name"], input: record["arguments"]), ts: ts)
        }
        if let results = (step["observation"] as? [String: Any])?["results"] as? [Any] {
            for result in results {
                SearchContent.appendTool(&messages, SearchContent.toolResultText(
                    (result as? [String: Any])?["content"] ?? result), ts: ts)
            }
        }
        return messages
    }

    /// One `chat_message` record (swe's `_read_devin` shape): user and
    /// assistant text, assistant `tool_calls` labelled by `name`, and
    /// `role: "tool"` results labelled with the remembered call's name.
    private mutating func chatMessage(_ record: [String: Any]) -> [SearchMessage] {
        let ts = (record["metadata"] as? [String: Any])?["created_at"] as? String
        switch record["role"] as? String {
        case "user":
            return SearchContent.messages(role: .user, content: record["content"], ts: ts)
        case "assistant":
            var messages = SearchContent.messages(role: .assistant, content: record["content"], ts: ts)
            for call in record["tool_calls"] as? [Any] ?? [] {
                guard let call = call as? [String: Any] else { continue }
                if let id = JSONValue.string(call["id"]), let name = JSONValue.string(call["name"]) {
                    state.callNames[id] = name
                }
                SearchContent.appendTool(&messages, SearchContent.toolCallText(
                    name: call["name"], input: call["arguments"]), ts: ts)
            }
            return messages
        case "tool":
            guard let text = SearchContent.toolResultText(record["content"]) else { return [] }
            let name = JSONValue.string(record["tool_call_id"]).flatMap { state.callNames[$0] }
            let labeled = name.map { "\($0): \(text)" } ?? text
            var messages: [SearchMessage] = []
            SearchContent.appendTool(&messages, labeled, ts: ts)
            return messages
        default:
            return []
        }
    }
}
