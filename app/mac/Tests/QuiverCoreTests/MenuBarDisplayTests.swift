import Foundation
import Testing
@testable import QuiverCore

private let now = Date(timeIntervalSince1970: 1_800_000_000)

private func reading(_ source: String, _ window: String, _ used: Double) -> LimitReading {
    LimitReading(source: source, harness: "claude", title: "Claude: \(source)", window: window, usedPercent: used,
                 resetsAt: now.addingTimeInterval(3600), fetchedAt: now, isStale: false)
}

private var today: UsageTotals {
    var totals = UsageTotals()
    totals.inputTokens = 1_200_000
    totals.outputTokens = 300_000
    totals.costUSD = 947.2
    return totals
}

@Suite struct MenuBarDisplayTests {
    private func defaults() -> UserDefaults {
        let suite = TestDefaults("MenuBarDisplayTests")
        return suite
    }

    @Test func byDefaultItIsOnAndShowsJustTheIcon() {
        let display = MenuBarDisplay(defaults: defaults())
        #expect(display.isVisible)
        #expect(display.primary == .none)
        #expect(display.secondary == .none)
        #expect(display.watchedLimit == nil)
        #expect(display.text(limit: reading("work", "5h", 81), today: today) == "")
    }

    @Test func choicesSurviveARelaunch() {
        let store = defaults()
        var display = MenuBarDisplay(defaults: store)
        display.isVisible = false
        display.primary = .limit
        display.secondary = .todaySpend
        display.watchedLimit = "work|5h"
        display.save(to: store)
        #expect(MenuBarDisplay(defaults: store) == display)
    }

    @Test func anUnknownSavedFigureFallsBackToNothing() {
        let store = defaults()
        store.set("weather", forKey: MenuBarDisplay.Key.primary)
        #expect(MenuBarDisplay(defaults: store).primary == .none)
    }

    @Test func textJoinsTheChosenFigures() {
        var display = MenuBarDisplay(defaults: defaults())
        display.primary = .limit
        #expect(display.text(limit: reading("work", "5h", 81), today: today) == "81%")
        display.secondary = .todaySpend
        #expect(display.text(limit: reading("work", "5h", 81), today: today) == "81% · $947")
        display.primary = .todayTokens
        #expect(display.text(limit: nil, today: today) == "1.5M · $947")
    }

    @Test func aFigureWithNoDataIsLeftOut() {
        var display = MenuBarDisplay(defaults: defaults())
        display.primary = .limit
        display.secondary = .todaySpend
        #expect(display.text(limit: nil, today: today) == "$947")
        #expect(display.text(limit: reading("work", "5h", 81), today: nil) == "81%")
        #expect(display.text(limit: nil, today: nil) == "")
    }

    @Test func theSameFigureTwiceShowsOnce() {
        var display = MenuBarDisplay(defaults: defaults())
        display.primary = .todaySpend
        display.secondary = .todaySpend
        #expect(display.text(limit: nil, today: today) == "$947")
    }

    @Test func theWatchedLimitWinsAndAMissingOneFallsBackToTheTightest() {
        let readings = [reading("work", "5h", 81), reading("personal", "7d", 40)]
        var display = MenuBarDisplay(defaults: defaults())
        #expect(display.limit(in: readings, now: now)?.id == "work|5h")
        display.watchedLimit = "personal|7d"
        #expect(display.limit(in: readings, now: now)?.id == "personal|7d")
        display.watchedLimit = "gone|5h"
        #expect(display.limit(in: readings, now: now)?.id == "work|5h")
    }

    @Test func todayAddsUpEveryHarness() {
        var codex = UsageTotals()
        codex.outputTokens = 500_000
        codex.costUSD = 2.8
        let spend = [SpendSlice(period: .yesterday, byHarness: ["claude": today]),
                     SpendSlice(period: .today, byHarness: ["claude": today, "codex": codex])]
        let total = MenuBarDisplay.today(in: spend)
        #expect(total?.totalTokens == 2_000_000)
        #expect(total?.costUSD == 950)
        #expect(MenuBarDisplay.today(in: nil) == nil)
        #expect(MenuBarDisplay.today(in: [SpendSlice(period: .yesterday, byHarness: [:])]) == nil)
    }

    @Test func aStaleOrResetWatchedLimitFallsBackToACurrentOne() {
        var stale = reading("personal", "7d", 40)
        stale.isStale = true
        var reset = reading("side", "5h", 95)
        reset.resetsAt = now.addingTimeInterval(-60)
        let readings = [reading("work", "5h", 81), stale, reset]
        var display = MenuBarDisplay(defaults: defaults())
        display.watchedLimit = "personal|7d"
        #expect(display.limit(in: readings, now: now)?.id == "work|5h")
        display.watchedLimit = "side|5h"
        #expect(display.limit(in: readings, now: now)?.id == "work|5h")
        #expect(display.limit(in: [stale], now: now) == nil)
    }

    @Test func everyFigureHasASentenceCaseTitle() {
        #expect(MenuBarFigure.allCases.map(\.title) == ["Nothing", "Limit", "Today's spend", "Today's tokens"])
    }
}

@Suite struct WidgetCatalogTests {
    @Test func listsTheWidgetsByTheirKinds() {
        #expect(WidgetCatalog.all.map(\.kind) == ["limits", "summary", "heatmap", "models", "compute"])
        #expect(WidgetCatalog.all.map(\.title) == ["Usage limits", "Usage summary", "Activity heatmap",
                                                  "Top models", "Compute"])
    }
}
