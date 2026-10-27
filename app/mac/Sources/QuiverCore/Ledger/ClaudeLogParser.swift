import Foundation

/// Turns Claude Code transcript lines (`<home>/projects/**/*.jsonl`) into
/// ledger records. Stateless: every line carries its own session id and cwd.
///
/// Dedup keys follow ccusage. Claude Code writes one line per content block
/// (thinking, text, tool use) of a single response, every one with the same
/// `message.id` and `requestId` and a usage block whose output count grows,
/// and a resumed session copies earlier responses into its new file. So the
/// key is `message.id` plus `requestId`, or `message.id` alone when the line
/// has no request id (sub-agents of some versions, third-party endpoints).
/// The ledger keeps the copy with the largest total, which is the final line.
/// Sidechain lines additionally collide on `message.id` alone, because a
/// sub-agent transcript can replay a parent message under a new request id.
enum ClaudeLogParser {
    static let harness = "claude"
    /// Lines without this byte string cannot carry usage and are not decoded.
    static let usageMarker = Data(#""usage""#.utf8)

    static func records(fromLine line: Data, accountHome: String) -> [LedgerRecord] {
        guard line.range(of: usageMarker) != nil,
              let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
        else { return [] }
        return records(fromObject: object, accountHome: accountHome)
    }

    static func records(fromObject object: [String: Any], accountHome: String) -> [LedgerRecord] {
        guard let message = object["message"] as? [String: Any],
              let usage = message["usage"] as? [String: Any],
              usage["input_tokens"] is NSNumber, usage["output_tokens"] is NSNumber,
              let timestampText = object["timestamp"] as? String,
              let timestamp = Timestamp.parse(timestampText),
              isValid(object, message: message)
        else { return [] }

        let messageId = JSONValue.string(message["id"])
        let requestId = JSONValue.string(object["requestId"])
        let sessionId = JSONValue.string(object["sessionId"]) ?? "unknown"
        let cwd = JSONValue.string(object["cwd"])
        let isSidechain = object["isSidechain"] as? Bool ?? false
        let model = JSONValue.string(message["model"]) ?? "unknown"
        let speed = usage["speed"] as? String
        let tier = speed == "fast" ? "fast" : nil
        let loggedCost = (object["costUSD"] as? NSNumber)?.doubleValue

        let baseKey: String
        if let messageId {
            baseKey = requestId.map { "claude:\(messageId):\($0)" } ?? "claude:\(messageId)"
        } else {
            // No id to collide on: key by position so a rescan of the same file stays idempotent.
            baseKey = "claude:anon:\(sessionId):\(timestampText):\(model)"
        }

        var records: [LedgerRecord] = []
        var event = tokens(from: usage, into: UsageEvent(
            harness: harness, accountHome: accountHome, sessionId: sessionId, cwd: cwd,
            model: model, timestamp: timestamp, costUSD: loggedCost, serviceTier: tier
        ))
        if event.totalTokens > 0 || (loggedCost ?? 0) > 0 {
            records.append(LedgerRecord(key: baseKey, messageId: messageId, isSidechain: isSidechain, event: event))
        }

        // Advisor calls run a second model inside one response and are not in
        // the parent's usage. Ordinary message iterations are, so they are skipped.
        if let iterations = usage["iterations"] as? [[String: Any]] {
            var advisorIndex = 0
            for iteration in iterations where iteration["type"] as? String == "advisor_message" {
                guard let advisorModel = JSONValue.string(iteration["model"]) else { continue }
                event = tokens(from: iteration, into: UsageEvent(
                    harness: harness, accountHome: accountHome, sessionId: sessionId, cwd: cwd,
                    model: advisorModel, timestamp: timestamp, serviceTier: tier
                ))
                if event.totalTokens > 0 {
                    records.append(LedgerRecord(
                        key: "\(baseKey):advisor:\(advisorIndex)",
                        messageId: messageId.map { "\($0):advisor:\(advisorIndex)" },
                        isSidechain: isSidechain, event: event))
                }
                advisorIndex += 1
            }
        }
        return records
    }

    private static func tokens(from usage: [String: Any], into base: UsageEvent) -> UsageEvent {
        var event = base
        let output = JSONValue.int(usage["output_tokens"])
        let details = usage["output_tokens_details"] as? [String: Any]
        let reasoning = min(output, JSONValue.int(details?["thinking_tokens"] ?? details?["reasoning_tokens"]))
        event.inputTokens = JSONValue.int(usage["input_tokens"])
        event.outputTokens = output - reasoning
        event.reasoningTokens = reasoning
        event.cacheReadTokens = JSONValue.int(usage["cache_read_input_tokens"])
        let aggregateWrite = JSONValue.int(usage["cache_creation_input_tokens"])
        if let split = usage["cache_creation"] as? [String: Any] {
            let fiveMinute = JSONValue.int(split["ephemeral_5m_input_tokens"])
            let oneHour = JSONValue.int(split["ephemeral_1h_input_tokens"])
            event.cacheWriteTokens = max(aggregateWrite, fiveMinute + oneHour)
            event.cacheWrite1hTokens = oneHour
        } else {
            event.cacheWriteTokens = aggregateWrite
        }
        return event
    }

    /// ccusage's validity rules: a `version` that is not semver-like marks a
    /// foreign log format, and ids or a model that are present but empty mark a
    /// malformed line.
    private static func isValid(_ object: [String: Any], message: [String: Any]) -> Bool {
        if let version = object["version"] as? String, !isSemverPrefix(version) { return false }
        for value in [object["sessionId"], object["requestId"], message["id"], message["model"]] {
            if let text = value as? String, text.isEmpty { return false }
        }
        return true
    }

    static func isSemverPrefix(_ value: String) -> Bool {
        let parts = value.split(separator: ".", maxSplits: 2, omittingEmptySubsequences: false)
        guard parts.count == 3 else { return false }
        return parts[0].allSatisfy(\.isNumber) && !parts[0].isEmpty
            && parts[1].allSatisfy(\.isNumber) && !parts[1].isEmpty
            && parts[2].first?.isNumber == true
    }
}
