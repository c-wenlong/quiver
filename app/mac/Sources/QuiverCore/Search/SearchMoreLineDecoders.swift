import Foundation

/// Timestamps as transcripts write them. `SearchMessage.ts` is always the
/// ISO string; this is where epoch encodings end up.
enum SearchStamp {
    /// Orca's `timestampIso`: a number is epoch seconds until ~Nov 2001 in
    /// ms says otherwise (`1e12`), an ISO string is kept verbatim.
    static func iso(_ value: Any?) -> String? {
        if let string = value as? String {
            return SearchText.nonBlank(string)
        }
        guard let number = value as? NSNumber,
              !(number === kCFBooleanTrue || number === kCFBooleanFalse)
        else { return nil }
        let raw = number.doubleValue
        guard raw.isFinite, raw > 0 else { return nil }
        let seconds = raw > 1_000_000_000_000 ? raw / 1000 : raw
        return Date.ISO8601FormatStyle(includingFractionalSeconds: true)
            .format(Date(timeIntervalSince1970: seconds))
    }

    /// Orca's `timestampMs`: the same epoch encoding as `iso`, as epoch
    /// milliseconds — for comparisons (OMP's title fold, muse's 60s dedupe).
    static func milliseconds(_ value: Any?) -> Double? {
        if let string = value as? String {
            guard !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
            if let date = try? Date.ISO8601FormatStyle(includingFractionalSeconds: true).parse(string) {
                return date.timeIntervalSince1970 * 1000
            }
            if let date = try? Date.ISO8601FormatStyle(includingFractionalSeconds: false).parse(string) {
                return date.timeIntervalSince1970 * 1000
            }
            return nil
        }
        guard let number = value as? NSNumber,
              !(number === kCFBooleanTrue || number === kCFBooleanFalse)
        else { return nil }
        let raw = number.doubleValue
        guard raw.isFinite, raw > 0 else { return nil }
        return raw > 1_000_000_000_000 ? raw : raw * 1000
    }
}

/// Cursor `~/.cursor/projects/<workspace>/agent-transcripts/<id>/<id>.jsonl`:
/// `{"role":"user","message":{"content":[...]}}` lines, compact JSON, mostly
/// without timestamps. Content blocks decode through `SearchContent` exactly
/// like Claude's — text keeps the record's role, `tool_use`/`tool_result`
/// become `.tool` rows (Orca's `consumeCursorRecordLine`).
///
/// What the transcript never carries — session id (it is the filename),
/// title, cwd, timestamps — lives in `chats/<workspace-hash>/<id>/meta.json`;
/// the scan merges that sidecar after the read (`Ledger.indexCursorFile`).
enum SearchCursorDecoder {
    /// Cursor writes compact JSON, so markers carry no space after the colon.
    static let markers = [
        #""role":"user""#, #""role":"assistant""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        let hints = SearchHints()
        let role = JSONValue.string(object["role"])
        if role == "user" || role == "assistant" {
            let message = object["message"] as? [String: Any]
            return (SearchContent.messages(
                role: role == "user" ? .user : .assistant,
                content: message?["content"] ?? object["content"],
                ts: object["timestamp"] as? String), hints)
        }
        return ([], hints)
    }
}

/// Droid `~/.factory/{sessions,projects}/<workspace>/<id>.jsonl` (Orca's
/// `consumeDroidRecordLine`):
///
/// - `session_start` carries `id`, `title`, `cwd` — the session's identity.
/// - `system` restates `cwd` (and `model`, which `SearchHints` has no field
///   for). Any record may also restate the id via `session_id`/`sessionId`.
/// - `message` records hold `message.{role,content}` — user and assistant
///   turns. `record.text` wins when present; otherwise `message.content`'s
///   blocks decode through `SearchContent`, so droid's `tool_use` and
///   `tool_result` blocks index as `.tool` rows like every other harness's.
///   (Orca publishes a 220-char preview string here instead, which would
///   leave the tool calls and most of each reply unsearchable.)
/// - `completion` records carry the finished reply as `finalText`.
///
/// `todo_state`/`compaction_state` records are bookkeeping and decode to
/// nothing.
enum SearchDroidDecoder {
    /// Droid writes compact JSON; the record kinds a line can be carrying.
    static let markers = [
        #""session_start""#, #""message""#, #""completion""#, #""system""#,
        #""session_id""#, #""sessionId""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        let type = object["type"] as? String
        if type == "session_start" {
            hints.sessionId = JSONValue.string(object["id"])
            // "New Session" is Droid's untouched placeholder — Orca treats it
            // as no title, so the first user prompt still wins instead of
            // being locked out as an explicit title.
            hints.title = JSONValue.string(object["title"])
                .flatMap { $0 == "New Session" ? nil : SearchText.titleCandidate($0, limit: 96) }
            hints.cwd = JSONValue.string(object["cwd"])
            return ([], hints)
        }
        if type == "system" {
            hints.cwd = JSONValue.string(object["cwd"])
        }
        hints.sessionId = JSONValue.string(object["session_id"])
            ?? JSONValue.string(object["sessionId"])
        let ts = object["timestamp"] as? String
        switch type {
        case "message":
            let message = object["message"] as? [String: Any]
            let role = JSONValue.string(object["role"]) ?? JSONValue.string(message?["role"])
            guard role == "user" || role == "assistant" else { return ([], hints) }
            if let text = SearchText.bounded(object["text"] as? String ?? "") {
                return ([SearchMessage(
                    role: role == "user" ? .user : .assistant, text: text, ts: ts)], hints)
            }
            return (SearchContent.messages(
                role: role == "user" ? .user : .assistant,
                content: message?["content"], ts: ts), hints)
        case "completion":
            guard let text = SearchText.bounded(object["finalText"] as? String ?? "")
            else { return ([], hints) }
            return ([SearchMessage(role: .assistant, text: text, ts: ts)], hints)
        default:
            return ([], hints)
        }
    }
}

/// Cline `~/.cline/data/sessions/<id>/<id>.json` + `<id>.messages.json`
/// (Orca's `parseClineSessionContent`). Both files arrive at this decoder as
/// whole JSON documents on one `beginFile` — the manifest first, then the
/// messages document — because the manifest decides the session and the
/// sibling supplies the turns:
///
/// - the manifest holds `session_id`, `cwd`/`workspace_root`, `model`,
///   `started_at` and `prompt` — `prompt` is the fallback title;
/// - the messages document is `{sessionId, updated_at, messages: [...]}`
///   where each message is `{id, role, content: [blocks], ts: <epoch ms>,
///   modelInfo: {id}}`. `ts` lands on `SearchMessage.ts` as ISO.
///
/// Orca's title order: the first user turn's text wins, then `prompt`. The
/// manifest's `prompt` is emitted as a title hint at manifest-decode time,
/// so when the messages document later emits its own explicit title the
/// indexer's latest-non-nil merge lands the user text on top.
enum SearchClineDecoder {
    /// `"session_id"` marks the manifest, `"messages"`/`"sessionId"` the
    /// messages document; either is worth parsing.
    static let markers = [
        #""session_id""#, #""sessionId""#, #""messages""#,
    ].map { Data($0.utf8) }

    static func decode(object: [String: Any]) -> (messages: [SearchMessage], hints: SearchHints) {
        var hints = SearchHints()
        if let messages = object["messages"] as? [Any] {
            // The document's own `sessionId` — a malformed manifest's still
            // leaves the session named for itself rather than the stem.
            hints.sessionId = JSONValue.string(object["sessionId"])
            var decoded: [SearchMessage] = []
            for value in messages {
                guard let message = value as? [String: Any],
                      let role = message["role"] as? String,
                      role == "user" || role == "assistant"
                else { continue }
                decoded += SearchContent.messages(
                    role: role == "user" ? .user : .assistant,
                    content: message["content"], ts: SearchStamp.iso(message["ts"]))
            }
            // Emitting the first user text as an explicit title is what keeps
            // it ahead of the manifest's `prompt` hint: the merge is
            // latest-non-nil, and this decode runs after the manifest's.
            if let candidate = decoded.first(where: { $0.role == .user })
                .flatMap({ SearchText.titleCandidate($0.text, limit: 96) }) {
                hints.title = candidate
            }
            return (decoded, hints)
        }
        // The `<id>.json` manifest.
        hints.sessionId = JSONValue.string(object["session_id"])
        hints.cwd = JSONValue.string(object["cwd"]) ?? JSONValue.string(object["workspace_root"])
        hints.title = JSONValue.string(object["prompt"])
            .flatMap { SearchText.titleCandidate($0, limit: 96) }
        return ([], hints)
    }
}
