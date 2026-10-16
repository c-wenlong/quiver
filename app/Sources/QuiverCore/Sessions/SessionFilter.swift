import Foundation

/// The "Hide accidental sessions" rule. A session is *trivial* when it
/// produced nothing (zero tokens or zero responses), or when it looks
/// like a one-shot probe: at most a couple of responses inside a
/// minute, with a nearly empty reply. Both the calendar (ledger SQL,
/// whole stored history) and the Sessions page (`SessionUsage`, already
/// whole-history) decide through this one place.
public enum SessionFilter {
    /// Harnesses whose event logs the ledger reads. A listed session of
    /// one of these with no usage events at all ran zero responses.
    public static let usageLogHarnesses: Set<String> = [
        "claude", "codex", "opencode", "devin", "pi", "droid", "cline",
        "cursor", "grok-bot",
    ]
    /// Harnesses whose events carry no per-reply token detail, so the
    /// probe rule cannot tell a probe from a real quick answer; only
    /// emptiness marks them trivial.
    public static let noReplyDetailHarnesses: Set<String> = ["cursor", "droid", "cline"]
    /// A session is probe-like at or under these bounds together.
    public static let maxProbeResponses = 2
    public static let maxProbeSpan: TimeInterval = 60
    public static let maxProbeOutputTokens = 100

    /// The rule over a session's whole-history usage.
    public static func isTrivial(usage: SessionUsage) -> Bool {
        if usage.totals.totalTokens == 0 || usage.totals.eventCount == 0 { return true }
        guard !noReplyDetailHarnesses.contains(usage.harness) else { return false }
        return usage.totals.eventCount <= maxProbeResponses
            && usage.lastTimestamp.timeIntervalSince(usage.firstTimestamp) < maxProbeSpan
            && usage.totals.outputTokens + usage.totals.reasoningTokens < maxProbeOutputTokens
    }
}
