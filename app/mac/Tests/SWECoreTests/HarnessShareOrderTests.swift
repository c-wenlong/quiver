import Foundation
import Testing
@testable import SWECore

struct HarnessShareOrderTests {
    func share(_ harness: String, cost: Double, tokens: Int = 100) -> HarnessShare {
        var totals = UsageTotals()
        totals.inputTokens = tokens
        totals.costUSD = cost
        return HarnessShare(harness: harness, totals: totals, share: 0)
    }

    @Test func biggestSpenderComesFirst() {
        // Made-up spend for a heavy month, in Settings order.
        let shares = [share("claude", cost: 8150), share("codex", cost: 1950), share("devin", cost: 3750),
                      share("cursor", cost: 1250), share("opencode", cost: 0.05), share("grok-bot", cost: 1400)]
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

    @Test func equalPositiveSpendKeepsSettingsOrderWhateverTheTokens() {
        let shares = [share("x", cost: 5, tokens: 10), share("y", cost: 5, tokens: 9_000)]
        #expect(shares.sortedBySpend().map(\.harness) == ["x", "y"])
    }
}
