import Foundation
import Testing
@testable import QuiverCore

struct HarnessShareOrderTests {
    func share(_ harness: String, cost: Double, tokens: Int = 100) -> HarnessShare {
        var totals = UsageTotals()
        totals.inputTokens = tokens
        totals.costUSD = cost
        return HarnessShare(harness: harness, totals: totals, share: 0)
    }

    @Test func biggestSpenderComesFirst() {
        // October's Settings order and spend.
        let shares = [share("claude", cost: 8102), share("codex", cost: 1973), share("devin", cost: 3725),
                      share("cursor", cost: 1225), share("opencode", cost: 0.05), share("grok-bot", cost: 1417)]
        #expect(shares.sortedBySpend().map(\.harness) == ["claude", "devin", "codex", "grok-bot", "cursor", "opencode"])
    }

    @Test func unpricedHarnessesFollowByTokens() {
        let shares = [share("a", cost: 0, tokens: 10), share("b", cost: 0, tokens: 500), share("c", cost: 1)]
        #expect(shares.sortedBySpend().map(\.harness) == ["c", "b", "a"])
    }

    @Test func equalsKeepTheirOrder() {
        let shares = [share("x", cost: 5), share("y", cost: 5), share("z", cost: 5)]
        #expect(shares.sortedBySpend().map(\.harness) == ["x", "y", "z"])
    }
}
