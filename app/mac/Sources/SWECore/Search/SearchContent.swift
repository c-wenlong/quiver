import Foundation

/// Turns a provider `content` value into the messages it decodes to — Orca's
/// `session-transcript-message-content.ts` ported onto `JSONSerialization`
/// values. Shared by every harness decoder: text blocks keep the record's
/// role, while `tool_use`/`tool_result` blocks become `.tool` messages
/// whichever record carried them (Claude stores tool results on `user`
/// records), so a caller never has to know a provider's block shapes.
enum SearchContent {
    /// Blocks whose `text`/`content` belongs to the record's role. Codex
    /// writes `input_text`/`output_text`, pi writes `thinking` — that one
    /// still contributes nothing, since real thinking blocks hold their text
    /// under a `thinking` key this never reads. An untyped block gets its
    /// `text`/`content` read the same way.
    static let textBlockTypes: Set<String> = [
        "text", "input_text", "output_text", "thinking", "reasoning",
    ]

    /// The argument that identifies what a tool call actually did, in the
    /// order Orca's `TOOL_INPUT_KEYS` looks for it.
    static let toolInputKeys = ["command", "cmd", "file_path", "path", "pattern", "query", "description"]

    /// Orca's `TOOL_ARGUMENT_SCAN_LIMIT`.
    static let toolArgumentLimit = 2000

    /// `content` — a string, an array of blocks, or a single block — split
    /// into messages. `role` nil means the record's own text is suppressed
    /// (developer/system context is not the conversation) but its tool blocks
    /// still decode, matching Orca's `transcriptMessagesFromContent`.
    static func messages(role: SearchRole?, content: Any?, ts: String?) -> [SearchMessage] {
        if let string = content as? String {
            guard let role, let text = SearchText.bounded(string) else { return [] }
            return [SearchMessage(role: role, text: text, ts: ts)]
        }
        let blocks: [Any]
        if let array = content as? [Any] {
            blocks = array
        } else if let content {
            blocks = [content]
        } else {
            blocks = []
        }

        var messages: [SearchMessage] = []
        var textParts: [String] = []
        for block in blocks {
            if let string = block as? String {
                textParts.append(string)
                continue
            }
            guard let item = block as? [String: Any] else { continue }
            switch (item["type"] as? String)?.lowercased() {
            case "tool_use", "toolcall", "tool_call", "function_call":
                appendTool(&messages, toolCallText(name: item["name"], input: item["input"] ?? item["arguments"]), ts: ts)
            case "tool_result", "toolresult", "function_call_output":
                appendTool(&messages, toolResultText(item["content"] ?? item["output"]), ts: ts)
            default:
                if let type = item["type"] as? String, !textBlockTypes.contains(type.lowercased()) {
                    continue
                }
                if let text = (item["text"] ?? item["content"]) as? String, !text.isEmpty {
                    textParts.append(text)
                }
            }
        }
        // The record's own words lead; its tool blocks follow in transcript
        // order.
        if let role, let text = joined(textParts) {
            messages.insert(SearchMessage(role: role, text: text, ts: ts), at: 0)
        }
        return messages
    }

    /// `"<name>: <argument>"` for a tool call — Orca's `toolCallText`: the
    /// name plus the one argument that says what the call did, head-capped at
    /// `toolArgumentLimit`. nil when the call carries neither.
    static func toolCallText(name: Any?, input: Any?) -> String? {
        let toolName = JSONValue.string(name)
        var argument: String?
        if let record = input as? [String: Any] {
            for key in toolInputKeys {
                if let value = JSONValue.string(record[key]) {
                    argument = value
                    break
                }
            }
        } else {
            argument = JSONValue.string(input)
        }
        let bounded = argument.map { SearchText.truncate($0, limit: toolArgumentLimit) }
        switch (toolName, bounded) {
        case let (name?, argument?): return "\(name): \(argument)"
        case let (name?, nil): return name
        case let (nil, argument?): return argument
        case (nil, nil): return nil
        }
    }

    /// Flattens a tool_result body — a string, or an array of text blocks —
    /// under the message cap (Orca's `toolResultText`).
    static func toolResultText(_ content: Any?) -> String? {
        if let string = content as? String {
            return SearchText.bounded(string)
        }
        guard let array = content as? [Any] else { return nil }
        var parts: [String] = []
        var length = 0
        for item in array {
            guard let text = (item as? String) ?? (item as? [String: Any])?["text"] as? String,
                  !text.isEmpty
            else { continue }
            parts.append(text)
            length += text.utf16.count
            if length >= SearchText.messageLimit { break }
        }
        return joined(parts)
    }

    /// `\n`-joins text parts and applies the message cap once
    /// (Orca's `boundedJoinedText`).
    static func joined(_ parts: [String]) -> String? {
        if parts.count == 1 { return SearchText.bounded(parts[0]) }
        return SearchText.bounded(parts.joined(separator: "\n"))
    }

    /// Appends `text` as a `.tool` message when it survives the bound.
    static func appendTool(_ messages: inout [SearchMessage], _ text: String?, ts: String?) {
        if let bounded = text.flatMap({ SearchText.bounded($0) }) {
            messages.append(SearchMessage(role: .tool, text: bounded, ts: ts))
        }
    }
}
