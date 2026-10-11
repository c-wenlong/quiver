import Foundation

/// One model response, as a harness logged it.
///
/// The five token kinds are disjoint, so they add up to `totalTokens` and each
/// one is priced at its own rate:
///
/// - `inputTokens`: prompt tokens read neither from nor into the cache.
/// - `cacheReadTokens`: prompt tokens served from the cache.
/// - `cacheWriteTokens`: prompt tokens written to the cache. `cacheWrite1hTokens`
///   is the part of it that went to Anthropic's 1-hour cache, which bills at a
///   higher rate than the 5-minute one.
/// - `outputTokens`: generated tokens, not counting reasoning.
/// - `reasoningTokens`: thinking or reasoning tokens. Claude and Codex both log
///   these inside their output count; the parsers move them here.
public struct UsageEvent: Sendable, Equatable, Hashable, Codable {
    /// "claude", "codex", "opencode" or "devin".
    public var harness: String
    /// The config root the event was read from, for example `~/.claude` or a
    /// Quiver profile home. It is how usage is split by account.
    public var accountHome: String
    public var sessionId: String
    public var cwd: String?
    public var model: String
    public var timestamp: Date
    public var inputTokens: Int
    public var outputTokens: Int
    public var cacheReadTokens: Int
    public var cacheWriteTokens: Int
    public var cacheWrite1hTokens: Int
    public var reasoningTokens: Int
    /// The cost the log itself recorded, when it records one. It wins over the
    /// pricing catalog.
    public var costUSD: Double?
    /// The speed or service tier the request ran on when it is not the standard
    /// one: "fast" (Claude fast mode), "priority" or "flex" (OpenAI tiers).
    public var serviceTier: String?

    public init(
        harness: String, accountHome: String, sessionId: String, cwd: String? = nil,
        model: String, timestamp: Date,
        inputTokens: Int = 0, outputTokens: Int = 0,
        cacheReadTokens: Int = 0, cacheWriteTokens: Int = 0, cacheWrite1hTokens: Int = 0,
        reasoningTokens: Int = 0, costUSD: Double? = nil, serviceTier: String? = nil
    ) {
        self.harness = harness
        self.accountHome = accountHome
        self.sessionId = sessionId
        self.cwd = cwd
        self.model = model
        self.timestamp = timestamp
        self.inputTokens = inputTokens
        self.outputTokens = outputTokens
        self.cacheReadTokens = cacheReadTokens
        self.cacheWriteTokens = cacheWriteTokens
        self.cacheWrite1hTokens = cacheWrite1hTokens
        self.reasoningTokens = reasoningTokens
        self.costUSD = costUSD
        self.serviceTier = serviceTier
    }

    public var totalTokens: Int {
        inputTokens + outputTokens + cacheReadTokens + cacheWriteTokens + reasoningTokens
    }

    /// Tokens of the prompt, which decide whether a request crosses a model's
    /// long-context price threshold.
    public var promptTokens: Int { inputTokens + cacheReadTokens + cacheWriteTokens }
}

/// A parsed event plus what the ledger needs to deduplicate it.
struct LedgerRecord: Sendable, Equatable {
    /// Two records with the same key are the same response logged twice. The
    /// ledger keeps one: the non-sidechain copy, then the larger token total.
    var key: String
    /// Claude only: a sidechain line can replay a parent message under a new
    /// request id, so sidechain records also collide on the message id alone.
    var messageId: String?
    var isSidechain: Bool = false
    var event: UsageEvent
}

enum JSONValue {
    /// A non-negative integer from a JSON number, or 0.
    static func int(_ value: Any?) -> Int {
        guard let number = value as? NSNumber, !(number === kCFBooleanTrue || number === kCFBooleanFalse) else {
            return 0
        }
        let double = number.doubleValue
        guard double.isFinite, double > 0 else { return 0 }
        return double >= Double(Int.max) ? Int.max : Int(double)
    }

    static func string(_ value: Any?) -> String? {
        guard let text = value as? String else { return nil }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
