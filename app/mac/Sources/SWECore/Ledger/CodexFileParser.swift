import Foundation

/// Turns one Codex rollout (`<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-*.jsonl`
/// or `archived_sessions/`) into ledger records.
///
/// Two shapes of usage exist, and a file uses one of them per turn:
///
/// - `token_usage_record` lines (current Codex): one per model response, with
///   that response's usage and its `response_id`. The key is the response id,
///   so a response repeated in a fork, an archived copy or a compaction replay
///   counts once. Once a file has one, its `token_count` lines are ignored.
/// - `event_msg` / `token_count` lines (older Codex): cumulative snapshots per
///   session. The turn's usage is `last_token_usage`, or the delta from the
///   previous total. A snapshot whose cumulative total was already seen is a
///   re-emit and is skipped, even when it interleaves with another stream.
///
/// Codex's `input_tokens` includes the cached tokens and `output_tokens`
/// includes reasoning, so both are split out before pricing; counting cached
/// tokens at the input rate inflates cost several times over.
///
/// A forked or spawned child rollout starts by replaying its parent's history
/// with fresh timestamps. Following OpenUsage, `token_count` lines are skipped
/// until the first live `task_started`, one whose `started_at` is at or after
/// the child's own creation; replayed ones keep the parent's older time. The
/// replay still seeds the cumulative baseline.
struct CodexFileParser: FileLogParser {
    static let harness = "codex"
    static let searchHarness: SearchDecoder.Harness = .codex
    /// Sessions that never logged a model, priced as ccusage does.
    static let fallbackModel = "gpt-5"

    struct State: Codable {
        var sessionId: String
        var cwd: String?
        var model: String?
        var tier: String?
        var sawSessionMeta = false
        /// While set, a child is replaying parent history: the epoch second its
        /// own first turn starts at or after, or -1 when only the line's own
        /// time can tell.
        var replayGate: Double?
        /// Every distinct cumulative total seen. Kept whole — a resume or
        /// compaction replay re-sends snapshots from arbitrarily far back,
        /// so a bounded ring would let an old one through again and emit it
        /// as fresh usage. The set is what dedups; size stays proportional
        /// to the session's turn count.
        var seenTotals: Set<Usage> = []
        /// The newest cumulative total, for the delta a snapshot without
        /// `last_token_usage` falls back to.
        var lastTotal: Usage?
        var usesRecords = false
    }

    struct Usage: Codable, Equatable, Hashable {
        var input = 0, cached = 0, cacheWrite = 0, output = 0, reasoning = 0, total = 0

        init(_ json: [String: Any]) {
            input = JSONValue.int(json["input_tokens"] ?? json["prompt_tokens"])
            cached = JSONValue.int(json["cached_input_tokens"] ?? json["cache_read_input_tokens"])
            cacheWrite = JSONValue.int(json["cache_write_input_tokens"] ?? json["cache_creation_input_tokens"])
            output = JSONValue.int(json["output_tokens"] ?? json["completion_tokens"])
            reasoning = JSONValue.int(json["reasoning_output_tokens"] ?? json["reasoning_tokens"])
            total = JSONValue.int(json["total_tokens"])
        }

        init() {}

        func minus(_ other: Usage) -> Usage {
            var delta = Usage()
            delta.input = max(0, input - other.input)
            delta.cached = max(0, cached - other.cached)
            delta.cacheWrite = max(0, cacheWrite - other.cacheWrite)
            delta.output = max(0, output - other.output)
            delta.reasoning = max(0, reasoning - other.reasoning)
            delta.total = max(0, total - other.total)
            return delta
        }

        var isEmpty: Bool { input == 0 && cached == 0 && output == 0 && reasoning == 0 && cacheWrite == 0 }
    }

    private let accountHome: String
    private var state: State

    init(file: URL, accountHome: String, savedState: Data?) {
        self.accountHome = accountHome
        if let savedState, let decoded = try? JSONDecoder().decode(State.self, from: savedState) {
            state = decoded
        } else {
            state = State(sessionId: file.deletingPathExtension().lastPathComponent)
        }
    }

    func savedState() -> Data? { try? JSONEncoder().encode(state) }

    /// Lines whose bytes name a record this parser reads — `session_meta`,
    /// `turn_context`, the two usage carriers and the two event types the
    /// replay gate listens for. Every object `records(fromObject:)` acts
    /// on carries its marker verbatim, so the byte check loses nothing.
    private static let markers = [
        "\"session_meta\"", "\"turn_context\"", "\"token_count\"", "\"token_usage_record\"",
        "\"task_started\"", "\"thread_settings_applied\"",
    ].map { Data($0.utf8) }

    func wants(line: Data) -> Bool {
        Self.markers.contains(where: { line.range(of: $0) != nil })
    }

    mutating func records(fromObject object: [String: Any]) -> [LedgerRecord] {
        let type = object["type"] as? String
        let payload = object["payload"] as? [String: Any] ?? [:]
        let lineTime = (object["timestamp"] as? String).flatMap(Timestamp.parse)

        switch type {
        case "session_meta":
            // A child also replays its parent's metadata; only the first is its own.
            guard !state.sawSessionMeta else { return [] }
            state.sawSessionMeta = true
            state.cwd = JSONValue.string(payload["cwd"]) ?? state.cwd
            if Self.isChild(payload) {
                let created = (payload["timestamp"] as? String).flatMap(Timestamp.parse) ?? lineTime
                state.replayGate = created.map { $0.timeIntervalSince1970.rounded(.down) } ?? -1
            }
            return []
        case "turn_context":
            if let model = JSONValue.string(payload["model"]) { state.model = model }
            if let cwd = JSONValue.string(payload["cwd"]) { state.cwd = cwd }
            return []
        case "token_usage_record":
            // Flip the flag only once a record is actually counted: a line
            // missing `usage` or a timestamp must not silence the older
            // `token_count` lines after it — the flag persists in the cursor,
            // so a malformed record would undercount the file for good.
            guard let usage = payload["usage"] as? [String: Any], let lineTime else { return [] }
            state.usesRecords = true
            let responseId = JSONValue.string(payload["response_id"])
            let key = responseId.map { "codex:resp:\($0)" }
                ?? "codex:\(state.sessionId):\(object["timestamp"] as? String ?? ""):record"
            return record(Usage(usage), key: key, at: lineTime)
        case "event_msg":
            break
        default:
            return []
        }

        switch payload["type"] as? String {
        case "thread_settings_applied":
            let settings = payload["thread_settings"] as? [String: Any]
            if let tier = JSONValue.string(settings?["service_tier"] ?? payload["service_tier"]) {
                state.tier = Self.normalizedTier(tier)
            }
            return []
        case "task_started":
            if let gate = state.replayGate, let startedAt = (payload["started_at"] as? NSNumber)?.doubleValue {
                let threshold = gate >= 0 ? gate : (lineTime?.timeIntervalSince1970.rounded(.down) ?? .infinity)
                if startedAt >= threshold { state.replayGate = nil }
            }
            return []
        case "token_count":
            return tokenCount(payload, timestampText: object["timestamp"] as? String, at: lineTime)
        default:
            return []
        }
    }

    private mutating func tokenCount(_ payload: [String: Any], timestampText: String?, at time: Date?) -> [LedgerRecord] {
        guard let info = payload["info"] as? [String: Any], let time, let timestampText else { return [] }
        let total = (info["total_token_usage"] as? [String: Any]).map(Usage.init)
        let last = (info["last_token_usage"] as? [String: Any]).map(Usage.init)

        if state.replayGate != nil || state.usesRecords {
            if let total { remember(total) }
            return []
        }
        let usage: Usage
        if let total {
            if state.seenTotals.contains(total) { return [] }
            usage = last ?? total.minus(state.lastTotal ?? Usage())
            remember(total)
        } else if let last {
            usage = last
        } else {
            return []
        }
        return record(usage, key: "codex:\(state.sessionId):\(timestampText)", at: time)
    }

    private mutating func remember(_ total: Usage) {
        state.seenTotals.insert(total)
        state.lastTotal = total
    }

    private func record(_ usage: Usage, key: String, at time: Date) -> [LedgerRecord] {
        guard !usage.isEmpty else { return [] }
        let cached = min(usage.cached, usage.input)
        let cacheWrite = min(usage.cacheWrite, usage.input - cached)
        let reasoning = min(usage.reasoning, usage.output)
        let event = UsageEvent(
            harness: Self.harness, accountHome: accountHome, sessionId: state.sessionId, cwd: state.cwd,
            model: state.model ?? Self.fallbackModel, timestamp: time,
            inputTokens: usage.input - cached - cacheWrite, outputTokens: usage.output - reasoning,
            cacheReadTokens: cached, cacheWriteTokens: cacheWrite, reasoningTokens: reasoning,
            serviceTier: state.tier)
        return [LedgerRecord(key: key, event: event)]
    }

    /// The child markers OpenUsage checks: a fork, a spawned sub-agent, or a
    /// thread with a parent. JSON null and blank strings do not count.
    private static func isChild(_ payload: [String: Any]) -> Bool {
        if JSONValue.string(payload["forked_from_id"]) != nil { return true }
        if JSONValue.string(payload["parent_thread_id"]) != nil { return true }
        if payload["thread_source"] as? String == "subagent" { return true }
        if let source = payload["source"] as? [String: Any], let sub = source["subagent"], !(sub is NSNull) {
            return true
        }
        return false
    }

    static func normalizedTier(_ tier: String) -> String? {
        switch tier.lowercased() {
        case "default", "auto", "standard": return nil
        case "fast", "priority": return "priority"
        default: return tier.lowercased()
        }
    }
}

extension CodexFileParser.State {
    /// `recentTotals` was the 32-entry ring `seenTotals` replaced. States
    /// saved with it seed the set and the last-seen total from it.
    private enum Keys: String, CodingKey {
        case sessionId, cwd, model, tier, sawSessionMeta, replayGate
        case seenTotals, lastTotal, usesRecords, recentTotals
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: Keys.self)
        sessionId = try container.decode(String.self, forKey: .sessionId)
        cwd = try container.decodeIfPresent(String.self, forKey: .cwd)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        tier = try container.decodeIfPresent(String.self, forKey: .tier)
        sawSessionMeta = try container.decodeIfPresent(Bool.self, forKey: .sawSessionMeta) ?? false
        replayGate = try container.decodeIfPresent(Double.self, forKey: .replayGate)
        usesRecords = try container.decodeIfPresent(Bool.self, forKey: .usesRecords) ?? false
        let recent = try container.decodeIfPresent([CodexFileParser.Usage].self, forKey: .recentTotals) ?? []
        seenTotals = try container.decodeIfPresent(Set<CodexFileParser.Usage>.self, forKey: .seenTotals)
            ?? Set(recent)
        lastTotal = try container.decodeIfPresent(CodexFileParser.Usage.self, forKey: .lastTotal)
            ?? recent.last
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: Keys.self)
        try container.encode(sessionId, forKey: .sessionId)
        try container.encodeIfPresent(cwd, forKey: .cwd)
        try container.encodeIfPresent(model, forKey: .model)
        try container.encodeIfPresent(tier, forKey: .tier)
        try container.encode(sawSessionMeta, forKey: .sawSessionMeta)
        try container.encodeIfPresent(replayGate, forKey: .replayGate)
        try container.encode(seenTotals, forKey: .seenTotals)
        try container.encodeIfPresent(lastTotal, forKey: .lastTotal)
        try container.encode(usesRecords, forKey: .usesRecords)
    }
}
