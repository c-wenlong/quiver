import Foundation

/// How many marbles each harness gets in the Overview's marble pile.
///
/// One marble stands for a round amount of spend ($0.01, $0.02, $0.05, $0.10
/// and so on up), the smallest that keeps the pile within `capacity`
/// marbles. A $200 month and a $16,000 month then fill about the same share
/// of the card, where a fixed $50 would leave one nearly empty and overflow
/// the other. A period with no priced spend at all counts tokens instead.
public struct MarbleScale: Equatable, Sendable {
    public enum Unit: Equatable, Sendable {
        case dollars(Double)
        case tokens(Int)
    }

    public var unit: Unit
    /// Marbles per harness, in the order the shares came in. Every harness
    /// with usage gets at least one, even when its spend rounds to nothing.
    public var counts: [(harness: String, count: Int)]

    public var total: Int { counts.reduce(0) { $0 + $1.count } }

    public init(shares: [HarnessShare], capacity: Int) {
        let shown = shares.filter { $0.totals.totalTokens > 0 }
        let bySpend = shown.contains { $0.totals.costUSD > 0 }
        let values = shown.map { bySpend ? $0.totals.costUSD : Double($0.totals.totalTokens) }
        let largest = values.max() ?? 0
        // Cents for spend, single tokens for tokens; then 1, 2, 5 times
        // each power of ten until the pile fits. Once the unit passes the
        // largest value every harness is down to its one marble, so stop
        // there even if that is still over capacity.
        var step = bySpend ? 0.01 : 1
        var counts = Self.counts(values, unit: step)
        while counts.reduce(0, +) > max(capacity, 1), step <= largest {
            step = Self.nextStep(after: step)
            counts = Self.counts(values, unit: step)
        }
        self.unit = bySpend ? .dollars(step) : .tokens(Int(step.rounded()))
        self.counts = zip(shown, counts).map { ($0.harness, $1) }
    }

    static func counts(_ values: [Double], unit: Double) -> [Int] {
        values.map { max(1, Int(($0 / unit).rounded())) }
    }

    /// 1, 2, 5, 10, 20, 50, ... at any scale.
    static func nextStep(after step: Double) -> Double {
        let magnitude = pow(10, floor(log10(step) + 1e-9))
        let lead = (step / magnitude).rounded()
        let next = lead < 2 ? 2 * magnitude : lead < 5 ? 5 * magnitude : 10 * magnitude
        // Steps never go under a cent, so this only strips float noise
        // (0.1 + error back to exactly 0.1).
        return (next * 100).rounded() / 100
    }

    public static func == (lhs: MarbleScale, rhs: MarbleScale) -> Bool {
        lhs.unit == rhs.unit && lhs.counts.map(\.harness) == rhs.counts.map(\.harness)
            && lhs.counts.map(\.count) == rhs.counts.map(\.count)
    }
}
