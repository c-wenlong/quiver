import Foundation

/// Pi session logs (`<home>/agent/sessions/<project>/<ts>_<uuid>.jsonl`).
///
/// Assistant `message` lines carry a `usage` block with per-request counts
/// and a USD `cost` breakdown Pi computed itself. `usage.output` already
/// includes `reasoning` (`totalTokens` = input + output + cacheRead +
/// cacheWrite in the logs), so reasoning is split out of output the way the
/// Codex reader does it. The session's cwd arrives once on the `session`
/// line, so it is kept as line-parser state.
struct PiFileParser: FileLogParser {
    static let harness = "pi"

    private let file: URL
    private let accountHome: String
    private var state = State()

    struct State: Codable {
        var cwd: String?
    }

    init(file: URL, accountHome: String, savedState: Data?) {
        self.file = file
        self.accountHome = accountHome
        if let savedState, let decoded = try? JSONDecoder().decode(State.self, from: savedState) {
            state = decoded
        }
    }

    mutating func records(fromLine line: Data) -> [LedgerRecord] {
        guard let object = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
        else { return [] }
        switch object["type"] as? String {
        case "session":
            if let cwd = JSONValue.string(object["cwd"]) { state.cwd = cwd }
            return []
        case "message":
            break
        default:
            return []
        }
        guard let message = object["message"] as? [String: Any],
              message["role"] as? String == "assistant",
              let usage = message["usage"] as? [String: Any],
              let milliseconds = message["timestamp"] as? NSNumber
        else { return [] }

        let input = JSONValue.int(usage["input"])
        let output = JSONValue.int(usage["output"])
        let reasoning = min(JSONValue.int(usage["reasoning"]), output)
        let cost = (usage["cost"] as? [String: Any]).flatMap { $0["total"] as? NSNumber }
            .map { $0.doubleValue }
        let model = JSONValue.string(message["model"]) ?? "unknown"
        let event = UsageEvent(
            harness: Self.harness, accountHome: accountHome,
            sessionId: file.path, cwd: state.cwd,
            model: model,
            timestamp: Date(timeIntervalSince1970: milliseconds.doubleValue / 1000),
            inputTokens: input, outputTokens: output - reasoning,
            cacheReadTokens: JSONValue.int(usage["cacheRead"]),
            cacheWriteTokens: JSONValue.int(usage["cacheWrite"]),
            reasoningTokens: reasoning, costUSD: cost)
        guard event.totalTokens > 0 || (cost ?? 0) > 0 else { return [] }

        // `id` is the line's message id; fall back to the timestamp so a
        // rescan still collides with itself.
        let identity = JSONValue.string(object["id"])
            ?? String(format: "%.3f", milliseconds.doubleValue)
        return [LedgerRecord(key: "pi:\(file.lastPathComponent):\(identity)", event: event)]
    }

    func savedState() -> Data? { try? JSONEncoder().encode(state) }
}

/// Session-total sources: files that hold one session's totals and are
/// rewritten in place, so they are re-read whole whenever their stamp
/// changes and upserted under one stable, authoritative key.
enum SessionTotalsFile {
    /// Droid: `<home>/sessions/<project>/<uuid>.settings.json`.
    /// `inclusiveTokenUsage` (falling back to `tokenUsage`) is the whole
    /// session's totals; `factoryCredits` is not USD, so no cost is logged
    /// and the catalog prices it. The timestamp is the last `timestamp` in
    /// the sibling `<uuid>.jsonl` (the settings file's mtime otherwise).
    /// No Droid file observed carries per-message usage; `thinkingTokens`
    /// is treated as separate from `outputTokens` (see the commit message).
    static func droid(settings url: URL, accountHome: String) throws -> [LedgerRecord] {
        guard let object = try readJSON(url),
              let usage = (object["inclusiveTokenUsage"] ?? object["tokenUsage"]) as? [String: Any]
        else { return [] }
        let input = JSONValue.int(usage["inputTokens"])
        let output = JSONValue.int(usage["outputTokens"])
        let cacheRead = JSONValue.int(usage["cacheReadTokens"])
        let cacheWrite = JSONValue.int(usage["cacheCreationTokens"])
        let reasoning = JSONValue.int(usage["thinkingTokens"])
        guard input + output + cacheRead + cacheWrite + reasoning > 0 else { return [] }

        let sessionId = url.deletingPathExtension()
            .deletingPathExtension().lastPathComponent
        var cwd: String?
        var last = FileInfo(path: url.path).map {
            Date(timeIntervalSince1970: $0.modified)
        }
        // `<uuid>.settings.json` -> `<uuid>.jsonl` (two extensions to drop).
        let transcript = url.deletingPathExtension()
            .deletingPathExtension().appendingPathExtension("jsonl")
        if let handle = FileHandle(forReadingAtPath: transcript.path) {
            defer { try? handle.close() }
            while autoreleasepool(invoking: {
                guard let line = (try? handle.readLine()) else { return false }
                guard let entry = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any]
                else { return true }
                if entry["type"] as? String == "session_start" {
                    cwd = cwd ?? JSONValue.string(entry["cwd"])
                }
                if let stamp = JSONValue.string(entry["timestamp"]).flatMap(Timestamp.parse) {
                    last = stamp
                }
                return true
            }) {}
        }
        guard let timestamp = last else { return [] }

        let event = UsageEvent(
            harness: "droid", accountHome: accountHome, sessionId: sessionId, cwd: cwd,
            model: JSONValue.string(object["model"]) ?? "unknown",
            timestamp: timestamp,
            inputTokens: input, outputTokens: output,
            cacheReadTokens: cacheRead, cacheWriteTokens: cacheWrite,
            reasoningTokens: reasoning)
        return [LedgerRecord(key: "droid:\(sessionId)", authoritative: true, event: event)]
    }

    /// Cline: `<home>/data/sessions/<id>/<id>.json` (the `.messages.json`
    /// transcript is a sibling, not read here). `metadata.tokensIn` is the
    /// input count — `metadata.usage.inputTokens` folds cache reads in —
    /// and `totalCost` is logged only when positive, so a free or unknown
    /// price falls back to the catalog.
    static func cline(session url: URL, accountHome: String) throws -> [LedgerRecord] {
        guard let object = try readJSON(url),
              let sessionId = JSONValue.string(object["session_id"])
        else { return [] }
        let metadata = object["metadata"] as? [String: Any] ?? [:]
        let input = JSONValue.int(metadata["tokensIn"])
        let output = JSONValue.int(metadata["tokensOut"])
        let cacheRead = JSONValue.int(metadata["cacheReads"])
        let cacheWrite = JSONValue.int(metadata["cacheWrites"])
        let logged = (metadata["totalCost"] as? NSNumber)?.doubleValue
        let cost = (logged ?? 0) > 0 ? logged : nil
        guard input + output + cacheRead + cacheWrite > 0 || cost != nil else { return [] }
        guard let timestamp = JSONValue.string(object["ended_at"] ?? object["started_at"])
            .flatMap(Timestamp.parse)
        else { return [] }

        let event = UsageEvent(
            harness: "cline", accountHome: accountHome, sessionId: sessionId,
            cwd: JSONValue.string(object["cwd"] ?? object["workspace_root"]),
            model: JSONValue.string(object["model"]) ?? "unknown",
            timestamp: timestamp,
            inputTokens: input, outputTokens: output,
            cacheReadTokens: cacheRead, cacheWriteTokens: cacheWrite,
            costUSD: cost)
        return [LedgerRecord(key: "cline:\(sessionId)", authoritative: true, event: event)]
    }

    private static func readJSON(_ url: URL) throws -> [String: Any]? {
        guard let data = FileManager.default.contents(atPath: url.path) else { return nil }
        return try JSONSerialization.jsonObject(with: data) as? [String: Any]
    }
}

private extension FileHandle {
    /// One line's bytes, newline included, or nil at EOF. Read in chunks so
    /// a long line is still one return value.
    func readLine() throws -> Data? {
        var data = Data()
        while true {
            guard let chunk = try read(upToCount: 8192), !chunk.isEmpty else {
                return data.isEmpty ? nil : data
            }
            if let newline = chunk.firstIndex(of: UInt8(ascii: "\n")) {
                data.append(chunk[..<newline])
                try seek(toOffset: offset() - UInt64(chunk.count - newline - 1))
                return data
            }
            data.append(chunk)
        }
    }
}
