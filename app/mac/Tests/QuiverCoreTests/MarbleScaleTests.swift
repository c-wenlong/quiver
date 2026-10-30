import Foundation
import Testing
@testable import QuiverCore

struct MarbleScaleTests {
    func share(_ harness: String, cost: Double, tokens: Int = 1_000) -> HarnessShare {
        var totals = UsageTotals()
        totals.inputTokens = tokens
        totals.costUSD = cost
        return HarnessShare(harness: harness, totals: totals, share: 0)
    }

    @Test func aHeavyMonthGetsALargeUnit() {
        // A made-up heavy month: about $16,400 across six harnesses.
        let scale = MarbleScale(shares: [
            share("claude", cost: 8150), share("codex", cost: 1950), share("devin", cost: 3750),
            share("cursor", cost: 1250), share("opencode", cost: 0.05), share("grok-bot", cost: 1400),
        ], capacity: 150)
        #expect(scale.unit == .dollars(200))
        #expect(scale.counts.map(\.count) == [41, 10, 19, 6, 1, 7])
        #expect(scale.total <= 150)
    }

    @Test func aLightMonthGetsASmallUnitAndStillFills() {
        let scale = MarbleScale(shares: [share("claude", cost: 140), share("codex", cost: 60)], capacity: 150)
        #expect(scale.unit == .dollars(2))
        #expect(scale.counts.map(\.count) == [70, 30])
    }

    @Test func pennySpendsUseCents() {
        let scale = MarbleScale(shares: [share("opencode", cost: 0.37)], capacity: 150)
        #expect(scale.unit == .dollars(0.01))
        #expect(scale.counts.map(\.count) == [37])
    }

    @Test func stepsRunOneTwoFive() {
        var steps = [0.01]
        for _ in 0..<7 { steps.append(MarbleScale.nextStep(after: steps.last!)) }
        #expect(steps == [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2])
    }

    @Test func everyHarnessWithUsageKeepsOneMarble() {
        let scale = MarbleScale(shares: [share("claude", cost: 5000), share("opencode", cost: 0)], capacity: 50)
        #expect(scale.counts.map(\.harness) == ["claude", "opencode"])
        #expect(scale.counts.last?.count == 1)
    }

    @Test func harnessesWithNoTokensGetNoMarbles() {
        let scale = MarbleScale(shares: [share("claude", cost: 10), share("codex", cost: 0, tokens: 0)], capacity: 50)
        #expect(scale.counts.map(\.harness) == ["claude"])
    }

    @Test func noPricedSpendCountsTokens() {
        let scale = MarbleScale(shares: [share("custom", cost: 0, tokens: 3_400_000)], capacity: 100)
        #expect(scale.unit == .tokens(50_000))
        #expect(scale.counts.map(\.count) == [68])
    }

    @Test func moreHarnessesThanCapacityStillEnds() {
        let shares = (0..<5).map { share("h\($0)", cost: 1) }
        let scale = MarbleScale(shares: shares, capacity: 2)
        #expect(scale.total == 5)
    }

    @Test func nothingInThePeriodIsAnEmptyPile() {
        let scale = MarbleScale(shares: [], capacity: 100)
        #expect(scale.total == 0)
    }
}
