import Foundation

/// OpenClaw `<state>/agents/<agent>/sessions/**` and Prime Agent
/// `~/.prime/agent/sessions/*.jsonl`: both are Pi forks sharing the
/// append-only message-graph record shape Orca parses with
/// `parseMessageGraphSessionFile`. `session` records carry id + cwd,
/// `message` records hold `message.{role,content}` user/assistant turns, and
/// `model_change` carries only the model `SearchHints` has no field for.
/// Pi's own decoder is `SearchPiDecoder`; OMP adds a title fold on top
/// (`SearchOmpDecoder`).
enum SearchMessageGraphDecoder {
    /// Both harnesses write `"type": "x"` with a space, so markers are bare
    /// names; `"session` (no closing quote) covers `"session_info"` too.
    static let markers = [#""message""#, #""session"#].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        switch object["type"] as? String {
        case "session":
            hints.sessionId = JSONValue.string(object["id"])
            hints.cwd = JSONValue.string(object["cwd"])
        case "message":
            guard let message = object["message"] as? [String: Any],
                  let role = message["role"] as? String,
                  role == "user" || role == "assistant"
            else { return ([], hints) }
            return (SearchContent.messages(
                role: role == "user" ? .user : .assistant,
                content: message["content"], ts: SearchStamp.iso(object["timestamp"])), hints)
        default:
            break
        }
        return ([], hints)
    }
}

/// OMP `~/.omp/agent/sessions/<ws>/*.jsonl`: the same message-graph records
/// as `SearchMessageGraphDecoder`, plus the title fold Orca runs for OMP
/// alone (`foldOmpTranscriptTitle`): `session_info` (legacy, `name`, always
/// user-sourced) and `title`/`title_change`/`session` records' `title`;
/// `title` requires `v == 1` and stamps by `updatedAt`, the rest by
/// `timestamp`; `source`/`titleSource` picks `user` or `auto`. A user-sourced
/// title always beats auto; within one source the newer timestamp wins. The
/// folded title outranks the first-prompt fallback (`fallbackTitle`) —
/// `finish()`'s explicit-title ordering already implements that.
struct SearchOmpDecoder {
    /// The fold's current winner, saved with the scan cursor.
    struct State: Codable, Equatable, Sendable {
        var title: String?
        var titleIsUserSourced = false
        var titleUpdatedAtMs: Double?
    }

    var state = State()

    static let markers = [#""message""#, #""session"#, #""title"#].map { Data($0.utf8) }

    mutating func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var output = SearchMessageGraphDecoder.decode(object: object)
        if let title = foldTitle(object) {
            output.hints.title = title
        }
        return output
    }

    /// Orca's `foldOmpTranscriptTitle` — returns the title to emit when this
    /// record changed the fold, nil when it kept or touched nothing.
    private mutating func foldTitle(_ record: [String: Any]) -> String? {
        let type = record["type"] as? String
        let legacy = type == "session_info"
        guard legacy || type == "session" || type == "title_change" || type == "title",
              type != "title" || JSONValue.int(record["v"]) == 1,
              let title = JSONValue.string(legacy ? record["name"] : record["title"])
            .flatMap({ SearchText.titleCandidate($0, limit: 96) })
        else { return nil }
        // `source`/`titleSource` must be one of the two values when present;
        // anything else invalidates the record (Orca's `!==` guard). Missing
        // or JSON null both mean `auto`.
        var userSourced = legacy
        if !legacy {
            let raw = [record["source"], record["titleSource"]]
                .compactMap { $0 }
                .first(where: { !($0 is NSNull) })
            if let raw {
                guard let source = raw as? String, source == "user" || source == "auto"
                else { return nil }
                userSourced = source == "user"
            }
        }
        if state.titleIsUserSourced && !userSourced { return nil }
        let updatedAt = SearchStamp.milliseconds(
            type == "title" ? record["updatedAt"] : record["timestamp"])
        if state.titleIsUserSourced == userSourced, let current = state.titleUpdatedAtMs,
           let updatedAt, updatedAt < current {
            return nil
        }
        state.title = title
        state.titleIsUserSourced = userSourced
        state.titleUpdatedAtMs = updatedAt
        return title
    }
}

/// Rovo Dev `~/.rovodev/sessions/<id>/metadata.json` + `session_context.json`
/// (Orca's `parseRovoSessionFile`/`consumeRovoSessionContext`). Two whole
/// documents arrive on one `beginFile`, manifest first:
///
/// - manifest: title `title|name|summary`, cwd from the `rovoSessionCwd` key
///   list, `created_at`/`updated_at` timeline. The session id is the session
///   directory name — the records never carry it, so the indexer derives it.
/// - context: `messages[]` `{role, content, timestamp}` user/assistant turns
///   decoded through `SearchContent`, then `message_history[]`
///   `{role|kind, parts[], timestamp}` where kind `request`→user and
///   `response`→assistant; the kept part kinds are `user-prompt|text` for
///   user, `text` for assistant (`rovoPartsText`).
enum SearchRovoDecoder {
    static let markers = [
        #""messages""#, #""message_history""#, #""workspace""#, #""cwd""#,
        #""working_directory""#, #""project_path""#, #""title""#, #""name""#,
        #""summary""#, #""created_at""#, #""createdAt""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        if object["messages"] is [Any] || object["message_history"] is [Any] {
            var messages: [SearchMessage] = []
            for value in object["messages"] as? [Any] ?? [] {
                guard let record = value as? [String: Any],
                      let role = record["role"] as? String,
                      role == "user" || role == "assistant"
                else { continue }
                messages += SearchContent.messages(
                    role: role == "user" ? .user : .assistant,
                    content: record["content"], ts: SearchStamp.iso(record["timestamp"]))
            }
            for value in object["message_history"] as? [Any] ?? [] {
                guard let record = value as? [String: Any] else { continue }
                let role = JSONValue.string(record["role"]) ?? kindRole(record["kind"])
                guard let role, role == "user" || role == "assistant",
                      let text = partsText(record["parts"] as? [Any], role: role)
                else { continue }
                messages.append(SearchMessage(
                    role: role == "user" ? .user : .assistant, text: text,
                    ts: SearchStamp.iso(record["timestamp"])))
            }
            return (messages, hints)
        }
        // The manifest — Orca's `rovoSessionCwd` key order.
        hints.title = firstString(object, keys: ["title", "name", "summary"])
            .flatMap { SearchText.titleCandidate($0, limit: 96) }
        hints.cwd = firstString(object, keys: [
            "workspace_path", "workspacePath", "workspace", "cwd",
            "working_directory", "workingDirectory", "project_path", "projectPath",
        ])
        return ([], hints)
    }

    /// The first non-blank string under any of `keys` (Orca's `firstString`).
    static func firstString(_ record: [String: Any], keys: [String]) -> String? {
        for key in keys {
            if let value = JSONValue.string(record[key]) { return value }
        }
        return nil
    }

    /// Orca's `rovoRoleFromKind`: `request`→user, `response`→assistant.
    private static func kindRole(_ kind: Any?) -> String? {
        switch kind as? String {
        case "request": "user"
        case "response": "assistant"
        default: nil
        }
    }

    /// Orca's `rovoPartsText`: per-role part kinds, `content` before `text`.
    private static func partsText(_ parts: [Any]?, role: String) -> String? {
        var texts: [String] = []
        for part in parts ?? [] {
            guard let record = part as? [String: Any] else { continue }
            let kind = JSONValue.string(record["part_kind"])
            if role == "user" && kind != "user-prompt" && kind != "text" { continue }
            if role == "assistant" && kind != "text" { continue }
            if let text = (record["content"] ?? record["text"]) as? String {
                texts.append(text)
            }
        }
        return SearchText.bounded(texts.joined(separator: " "))
    }
}

/// Kiro `~/.kiro/sessions/cli/<uuid>.json` + `<uuid>.jsonl` (Orca's
/// `parseKiroSessionFile`). The manifest arrives as one document — the id is
/// the validated UUID filename, never a field — and its transcript streams as
/// `{kind: Prompt|AssistantMessage|ToolResults, data: {content, meta}}`
/// records whose `{kind, data}` blocks restate as Claude-shaped blocks
/// (`toPreviewBlocks`): `text`→text, `json`→text of the serialized value,
/// `toolUse`→`tool_use{name,input}`, `toolResult`→`tool_result` with the
/// fold recursed. Only `Prompt` carries a timestamp — `data.meta.timestamp`,
/// epoch seconds.
enum SearchKiroDecoder {
    static let markers = [
        #""kind""#, #""session_id""#, #""session_state""#, #""cwd""#,
        #""title""#, #""parent_session_id""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        if let kind = object["kind"] as? String {
            guard let data = object["data"] as? [String: Any] else { return ([], hints) }
            let blocks = previewBlocks(data["content"])
            switch kind {
            case "Prompt":
                return (SearchContent.messages(
                    role: .user, content: blocks,
                    ts: SearchStamp.iso((data["meta"] as? [String: Any])?["timestamp"])), hints)
            case "AssistantMessage":
                return (SearchContent.messages(role: .assistant, content: blocks, ts: nil), hints)
            case "ToolResults":
                return (SearchContent.messages(role: .tool, content: blocks, ts: nil), hints)
            default:
                return ([], hints)
            }
        }
        // The manifest; a `parent_session_id` is filtered out before decode
        // (a subagent's child session is reached from its parent, not listed).
        hints.cwd = JSONValue.string(object["cwd"])
        hints.title = JSONValue.string(object["title"])
            .flatMap { SearchText.titleCandidate($0, limit: 96) }
        return ([], hints)
    }

    /// Orca's `toPreviewBlocks`: `{kind, data}` blocks restated as the
    /// Claude-shaped blocks `SearchContent` reads.
    static func previewBlocks(_ content: Any?) -> [Any] {
        var blocks: [[String: Any]] = []
        for value in content as? [Any] ?? [] {
            guard let block = value as? [String: Any] else { continue }
            switch block["kind"] as? String {
            case "text":
                if let data = block["data"] as? String, !data.isEmpty {
                    blocks.append(["type": "text", "text": data])
                }
            case "json":
                // Foundation serializes only an object or array at the top;
                // anything else raises an Objective-C exception `try?`
                // can't catch, which would crash every scan of the file.
                if let data = block["data"], JSONSerialization.isValidJSONObject(data),
                   let raw = try? JSONSerialization.data(withJSONObject: data),
                   let text = String(data: raw, encoding: .utf8) {
                    blocks.append(["type": "text", "text": text])
                }
            case "toolUse":
                let tool = block["data"] as? [String: Any]
                var use: [String: Any] = ["type": "tool_use"]
                if let name = JSONValue.string(tool?["name"]) { use["name"] = name }
                if let input = tool?["input"] { use["input"] = input }
                blocks.append(use)
            case "toolResult":
                blocks.append([
                    "type": "tool_result",
                    "content": previewBlocks((block["data"] as? [String: Any])?["content"]),
                ])
            default:
                break
            }
        }
        return blocks
    }
}

/// Antigravity `brain/<conversation>/.system_generated/logs/
/// {transcript,transcript_full}.jsonl` (Orca's `consumeAntigravityRecordLine`):
/// `{source, type, content, created_at}` records. `source` `USER_EXPLICIT`
/// or `USER` with `type` `USER_INPUT` or `REQUEST` is a user turn whose text
/// is the `<USER_REQUEST>` tag's body when that envelope is present; `source`
/// `MODEL` with `type` `PLANNER_RESPONSE` is an assistant turn. The records
/// carry no id — the conversation directory three levels up is the session
/// id, derived at index time — and no cwd/model, so no hints are invented.
enum SearchAntigravityDecoder {
    static let markers = [
        #""USER_INPUT""#, #""REQUEST""#, #""PLANNER_RESPONSE""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        let hints = SearchHints()
        let source = JSONValue.string(object["source"])
        let type = JSONValue.string(object["type"])
        let ts = SearchStamp.iso(object["created_at"])
        if (source == "USER_EXPLICIT" || source == "USER"),
           (type == "USER_INPUT" || type == "REQUEST") {
            guard let request = userRequest(object["content"] as? String),
                  let text = SearchText.bounded(request)
            else { return ([], hints) }
            return ([SearchMessage(role: .user, text: text, ts: ts)], hints)
        }
        if source == "MODEL", type == "PLANNER_RESPONSE",
           let content = JSONValue.string(object["content"]),
           let text = SearchText.bounded(content) {
            return ([SearchMessage(role: .assistant, text: text, ts: ts)], hints)
        }
        return ([], hints)
    }

    /// Orca's `extractAntigravityUserRequest`: the `<USER_REQUEST>` envelope's
    /// body (closing tag optional), else the whole content string.
    private static func userRequest(_ content: String?) -> String? {
        let opener = "<USER_REQUEST>"
        guard let content, let start = content.range(of: opener) else {
            return JSONValue.string(content)
        }
        let body = content[start.upperBound...]
        let text = body.range(of: "</USER_REQUEST>")
            .map { String(body[..<$0.lowerBound]) } ?? String(body)
        return JSONValue.string(text)
    }
}

/// Gemini `~/.gemini/tmp` keeps two session shapes side by side (Orca's
/// `parseGeminiSessionFile` dispatches on extension): a `.json` checkpoint
/// document `{sessionId, startTime, lastUpdated, messages: [...]}` fed here
/// whole (`parseGeminiJsonSessionContent`), and a `.jsonl` stream where each
/// record is a checkpoint write — `{$set: {lastUpdated}}` is bookkeeping,
/// anything else is a message record (`consumeGeminiJsonlRecordLine`).
/// Message records: `type` `user` → user turn, `type` `gemini` → assistant
/// turn (`consumeGeminiMessage`); `content` decodes through `SearchContent`
/// and `timestamp` is the record's ISO stamp. `model`/`tokens` have no
/// `SearchHints` field.
enum SearchGeminiDecoder {
    static let markers = [
        #""messages""#, #""sessionId""#, #""type""#, #""$set""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        if let messages = object["messages"] as? [Any] {
            // The whole checkpoint document.
            hints.sessionId = JSONValue.string(object["sessionId"])
            return (messages.flatMap { message($0 as? [String: Any]) }, hints)
        }
        if object["$set"] is [String: Any] { return ([], hints) }
        hints.sessionId = JSONValue.string(object["sessionId"])
        return (message(object), hints)
    }

    /// `consumeGeminiMessage`: `type` `user`/`gemini` → user/assistant turn.
    private static func message(_ record: [String: Any]?) -> [SearchMessage] {
        guard let record, let type = record["type"] as? String else { return [] }
        let ts = SearchStamp.iso(record["timestamp"])
        switch type {
        case "user":
            return SearchContent.messages(role: .user, content: record["content"], ts: ts)
        case "gemini":
            return SearchContent.messages(role: .assistant, content: record["content"], ts: ts)
        default:
            return []
        }
    }
}

/// Grok `~/.grok/sessions/<ws>/<id>/summary.json` + `chat_history.jsonl`
/// (Orca's `parseGrokSessionFile`). Two shapes arrive under one `beginFile`:
///
/// - the summary document: `info.id`/`info.cwd`, `generated_title` then
///   `session_summary` for the explicit title, `head_branch`;
/// - history records `{type: "user"|"assistant", content, timestamp}` — a
///   user turn's text is the `<user_query>` envelope's body
///   (`extractGrokFirstUserPromptText`); bootstrap `<user_info>`-only rows
///   publish nothing, and assistant text is the envelope-stripped string or
///   the content blocks (`extractGrokContentText`). Orca caps the emitted
///   turn at preview width; the index keeps the full bounded body.
enum SearchGrokDecoder {
    static let markers = [
        #""type""#, #""info""#, #""generated_title""#, #""session_summary""#,
        #""head_branch""#, #""user_query""#, #""user_info""#, #""content""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        if let type = object["type"] as? String, type == "user" || type == "assistant" {
            let ts = SearchStamp.iso(object["timestamp"])
            if type == "user" {
                guard let body = firstUserPromptText(object["content"]),
                      let text = SearchText.bounded(body)
                else { return ([], hints) }
                return ([SearchMessage(role: .user, text: text, ts: ts)], hints)
            }
            if let content = object["content"] as? String {
                // A string body is envelope-stripped like the user side's.
                let unwrapped = stripUserQueryEnvelope(content)
                guard let text = SearchText.bounded(unwrapped) else { return ([], hints) }
                return ([SearchMessage(role: .assistant, text: text, ts: ts)], hints)
            }
            return (SearchContent.messages(
                role: .assistant, content: object["content"], ts: ts), hints)
        }
        // The summary document.
        let info = object["info"] as? [String: Any]
        hints.sessionId = JSONValue.string(info?["id"])
        hints.cwd = JSONValue.string(info?["cwd"])
        hints.branch = JSONValue.string(object["head_branch"])
        hints.title = JSONValue.string(object["generated_title"])
            .flatMap { SearchText.titleCandidate($0, limit: 96) }
            ?? JSONValue.string(object["session_summary"])
            .flatMap { SearchText.titleCandidate($0, limit: 96) }
        return ([], hints)
    }

    /// Orca's `extractGrokFirstUserPromptText`: the `<user_query>` body's
    /// text, or nil for bootstrap-only rows and anything unflattenable.
    private static func firstUserPromptText(_ value: Any?) -> String? {
        guard let raw = flattenUserContent(value), !isBootstrapContext(raw) else { return nil }
        let unwrapped = stripUserQueryEnvelope(raw)
        let trimmed = unwrapped.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isBootstrapContext(trimmed) else { return nil }
        // Still a bare `<user_info>` dump with no `<user_query>` tag → not the
        // typed ask.
        if startsWithTag(trimmed, "user_info"), !trimmed.lowercased().contains("<user_query>") {
            return nil
        }
        return trimmed
    }

    /// Orca's `flattenGrokUserContent`: a string as-is, or the `text` values
    /// of an array's untyped/`text`-typed blocks joined by newlines.
    private static func flattenUserContent(_ value: Any?) -> String? {
        if let string = value as? String { return string }
        guard let array = value as? [Any] else { return nil }
        var parts: [String] = []
        for item in array {
            if let string = item as? String {
                parts.append(string)
                continue
            }
            guard let record = item as? [String: Any] else { continue }
            if let type = record["type"] as? String, type != "text" { continue }
            if let text = record["text"] as? String { parts.append(text) }
        }
        return parts.isEmpty ? nil : parts.joined(separator: "\n")
    }

    /// Orca's `stripGrokUserQueryEnvelope` (case-insensitive; a missing
    /// closer still yields the tail).
    static func stripUserQueryEnvelope(_ text: String) -> String {
        guard let start = text.range(of: "<user_query>", options: .caseInsensitive) else {
            return text
        }
        let body = text[start.upperBound...]
        let stripped = body.range(of: "</user_query>", options: .caseInsensitive)
            .map { String(body[..<$0.lowerBound]) } ?? String(body)
        return stripped.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Orca's `isGrokBootstrapContextText`: a `<user_info>`-led dump whose
    /// remainder is empty or only a `<git_status>` snapshot is bootstrap
    /// noise, not a prompt.
    private static func isBootstrapContext(_ text: String) -> Bool {
        let normalized = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard normalized.hasPrefix("<user_info>") else { return false }
        guard let end = normalized.range(of: "</user_info>") else {
            return !normalized.contains("<user_query>")
        }
        let remainder = normalized[end.upperBound...]
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return remainder.isEmpty
            || (remainder.hasPrefix("<git_status>") && remainder.hasSuffix("</git_status>"))
    }

    /// The first tag check Orca runs on the unwrapped text.
    private static func startsWithTag(_ text: String, _ tag: String) -> Bool {
        let head = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return head.hasPrefix("<\(tag)>") || head.hasPrefix("<\(tag) ")
    }
}

/// Kimi `~/.kimi-code/sessions/wd_*/session_*/state.json` +
/// `agents/<id>/wire.jsonl` (Orca's `parseKimiSessionFile`). The manifest's
/// `title` is the explicit title; `lastPrompt` is the last-resort fallback
/// the indexer gap-fills, so a first real user turn still wins. Wire records:
/// `context.append_message` publishes `role: "user"` only when
/// `origin.kind == "user"` — Kimi's synthetic `role: "user"` injections are
/// machinery, not turns; `context.append_loop_event` `content.part` text
/// chunks accumulate until `step.end` releases them as one assistant
/// message, and `endOfFile()` flushes a trailing unterminated step
/// (`consumeKimiWireTranscript`'s trailing `flushAssistant`).
struct SearchKimiDecoder {
    struct State: Codable, Equatable, Sendable {
        /// `content.part` text chunks since the last `step.end`, capped at
        /// the message bound — a runaway wire cannot out-allocate it.
        var pendingAssistant: [String] = []
        var pendingChars: Int = 0
    }

    var state = State()

    static let markers = [
        #""context.append_message""#, #""context.append_loop_event""#,
        #""title""#, #""lastPrompt""#, #""agents""#,
        #""createdAt""#, #""updatedAt""#,
    ].map { Data($0.utf8) }

    mutating func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        switch object["type"] as? String {
        case "context.append_message":
            guard let message = object["message"] as? [String: Any],
                  message["role"] as? String == "user",
                  (message["origin"] as? [String: Any])?["kind"] as? String == "user"
            else { return ([], hints) }
            return (SearchContent.messages(
                role: .user, content: message["content"], ts: nil), hints)
        case "context.append_loop_event":
            guard let event = object["event"] as? [String: Any] else { return ([], hints) }
            switch event["type"] as? String {
            case "content.part":
                let part = event["part"] as? [String: Any]
                if part?["type"] as? String == "text", let text = part?["text"] as? String,
                   state.pendingChars < SearchText.messageLimit {
                    state.pendingAssistant.append(text)
                    state.pendingChars += text.utf16.count
                }
            case "step.end":
                return (flushAssistant(), hints)
            default:
                break
            }
        case nil:
            // The state.json manifest — `lastPrompt` is the indexer's overlay
            // gap-fill, and `createdAt`/`updatedAt` ride it too.
            hints.title = JSONValue.string(object["title"])
                .flatMap { SearchText.titleCandidate($0, limit: 96) }
        default:
            break
        }
        return ([], hints)
    }

    /// The trailing flush Orca runs after the wire stream ends.
    mutating func endOfFile() -> (messages: [SearchMessage], hints: SearchHints) {
        (flushAssistant(), SearchHints())
    }

    /// Joined `content.part` chunks → one assistant message; chunks join raw
    /// so inter-chunk spacing survives (Orca's `pendingAssistantText`).
    private mutating func flushAssistant() -> [SearchMessage] {
        let joined = state.pendingAssistant.joined()
        state.pendingAssistant = []
        state.pendingChars = 0
        guard let text = SearchText.bounded(joined) else { return [] }
        return [SearchMessage(role: .assistant, text: text)]
    }
}
