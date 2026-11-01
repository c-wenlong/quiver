import Foundation

/// USD per token for one model, each token kind at its own rate.
public struct ModelRates: Sendable, Equatable {
    public var input: Double
    public var output: Double
    public var cacheRead: Double
    /// 5-minute cache writes, and every cache write for providers with one rate.
    public var cacheWrite: Double
    /// Anthropic's 1-hour cache writes.
    public var cacheWrite1h: Double
    public var reasoning: Double
    /// Rates for a request whose prompt is longer than `threshold` tokens. The
    /// provider then bills the whole request at these rates.
    public var longContext: LongContext?
    /// Rates for a service tier or speed ("priority", "flex", "fast") that the
    /// catalog lists explicitly.
    public var tiers: [String: ModelRates]
    /// Multipliers for tiers the catalog gives no explicit rates for.
    public var tierMultipliers: [String: Double]

    public struct LongContext: Sendable, Equatable {
        public var threshold: Int
        public var input: Double
        public var output: Double
        public var cacheRead: Double
        public var cacheWrite: Double
        public var cacheWrite1h: Double
        public var reasoning: Double
    }

    public init(
        input: Double, output: Double, cacheRead: Double? = nil, cacheWrite: Double? = nil,
        cacheWrite1h: Double? = nil, reasoning: Double? = nil, longContext: LongContext? = nil,
        tiers: [String: ModelRates] = [:], tierMultipliers: [String: Double] = [:]
    ) {
        self.input = input
        self.output = output
        self.cacheRead = cacheRead ?? input
        self.cacheWrite = cacheWrite ?? input
        self.cacheWrite1h = cacheWrite1h ?? cacheWrite ?? input
        self.reasoning = reasoning ?? output
        self.longContext = longContext
        self.tiers = tiers
        self.tierMultipliers = tierMultipliers
    }

    /// USD for these token counts. `crossesLongContext` says the request's
    /// prompt was above `longContext.threshold`.
    public func cost(
        input inputTokens: Int, output outputTokens: Int, cacheRead cacheReadTokens: Int,
        cacheWrite cacheWriteTokens: Int, cacheWrite1h cacheWrite1hTokens: Int, reasoning reasoningTokens: Int,
        tier: String? = nil, crossesLongContext: Bool = false
    ) -> Double {
        var rates = self
        var multiplier = 1.0
        if let tier {
            if let explicit = tiers[tier] {
                rates = explicit
            } else if let factor = tierMultipliers[tier] {
                multiplier = factor
            }
        }
        var input = rates.input, output = rates.output, cacheRead = rates.cacheRead
        var cacheWrite = rates.cacheWrite, cacheWrite1h = rates.cacheWrite1h, reasoning = rates.reasoning
        if crossesLongContext, let long = rates.longContext {
            input = long.input
            output = long.output
            cacheRead = long.cacheRead
            cacheWrite = long.cacheWrite
            cacheWrite1h = long.cacheWrite1h
            reasoning = long.reasoning
        }
        let fiveMinuteWrites = max(0, cacheWriteTokens - cacheWrite1hTokens)
        let dollars = Double(inputTokens) * input
            + Double(outputTokens) * output
            + Double(cacheReadTokens) * cacheRead
            + Double(fiveMinuteWrites) * cacheWrite
            + Double(cacheWrite1hTokens) * cacheWrite1h
            + Double(reasoningTokens) * reasoning
        return dollars * multiplier
    }
}
