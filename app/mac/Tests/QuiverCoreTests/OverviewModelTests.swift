import Foundation
import Synchronization
import Testing
@testable import QuiverCore

func totals(tokens input: Int, cost: Double, unpriced: Int = 0) -> UsageTotals {
    var t = UsageTotals()
    t.inputTokens = input
    t.costUSD = cost
    t.unpricedTokens = unpriced
    return t
}

/// A ledger answering from fixed tables and recording what it was asked.
actor FakeLedger: LedgerQuerying {
    var harnesses: [String: UsageTotals] = [:]
    var accounts: [String: UsageTotals] = [:]
    var accountHarnesses: [String: String] = [:]
    var days: [DailyUsage] = []
    var scanError: Error?
    var nextReport = ScanReport()
    func set(report: ScanReport) { nextReport = report }
    private(set) var scans = 0
    private var scanGate: CheckedContinuation<Void, Never>?
    private var holdScans = false
    private(set) var scanWaiting = false
    /// Bumped once a held scan is parked, so a test waits for that instead
    /// of spinning on `scanWaiting`.
    let scanParks = CountGate()

    func holdNextScan() { holdScans = true }
    func releaseScan() {
        holdScans = false
        scanGate?.resume()
        scanGate = nil
    }
    private(set) var pricingUpdates = 0

    func setPricing(_ catalog: PricingCatalog) { pricingUpdates += 1 }
    private(set) var ranges: [DateInterval] = []

    /// byHarness calls from this index on park until released: a way to catch
    /// the summary cache's warming pass and stale revalidations mid-flight,
    /// while load's own earlier calls still go through.
    private var holdHarnessFrom: Int?
    private var harnessGates: [CheckedContinuation<Void, Never>] = []
    /// Bumped once per byHarness call that parks.
    let harnessParks = CountGate()

    func holdHarnessQueries(fromCall index: Int) { holdHarnessFrom = index }
    func releaseHarnessQueries() {
        holdHarnessFrom = nil
        let gates = harnessGates
        harnessGates = []
        for gate in gates { gate.resume() }
    }

    func set(harnesses: [String: UsageTotals], accounts: [String: UsageTotals] = [:],
             accountHarnesses: [String: String] = [:], days: [DailyUsage] = []) {
        self.harnesses = harnesses
        self.accounts = accounts
        self.accountHarnesses = accountHarnesses
        self.days = days
    }

    func failScans(with error: Error) { scanError = error }

    var progress: ScanProgress?
    func set(progress: ScanProgress?) { self.progress = progress }
    func scanProgress() async -> ScanProgress? { progress }

    func scan(roots: LedgerRoots?, recentLimit: Int?) async throws -> ScanReport {
        scans += 1
        // Holds only the next scan: a second one (a capped reconcile tick)
        // runs straight through while the first is parked.
        if holdScans {
            holdScans = false
            scanWaiting = true
            let parks = scanParks
            await withCheckedContinuation {
                scanGate = $0
                // Counted once the continuation is stored, so a release
                // that follows the wait always finds it.
                Task { await parks.bump() }
            }
            scanWaiting = false
        }
        if let scanError { throw scanError }
        return nextReport
    }

    func byHarness(in range: DateInterval) async -> [String: UsageTotals] {
        ranges.append(range)
        if let from = holdHarnessFrom, ranges.count >= from {
            let parks = harnessParks
            await withCheckedContinuation {
                harnessGates.append($0)
                Task { await parks.bump() }
            }
        }
        return harnesses
    }

    func byAccount(in range: DateInterval) async -> [String: UsageTotals] { accounts }

    func harnessByAccount(in range: DateInterval) async -> [String: String] { accountHarnesses }

    func daily(in range: DateInterval, calendar: Calendar) async -> [DailyUsage] {
        days.filter { range.contains($0.day) }
    }
}

struct ScanFailed: Error, LocalizedError {
    var errorDescription: String? { "disk unreadable" }
}

@Suite struct OverviewPeriodTests {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        return c
    }
    // 2026-10-11 15:30 in Singapore.
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    @Test func todayStartsAtLocalMidnight() {
        let range = OverviewPeriod.today.range(now: now, calendar: calendar)
        #expect(range.start == Date(timeIntervalSince1970: 1_791_648_000))
        #expect(range.end == now)
    }

    @Test func thisMonthStartsOnTheFirst() {
        let range = OverviewPeriod.month.range(now: now, calendar: calendar)
        #expect(range.start == Date(timeIntervalSince1970: 1_790_784_000))   // 2026-10-01 00:00 SGT
    }

    @Test func thirtyDaysIsARollingWindow() {
        let range = OverviewPeriod.thirtyDays.range(now: now, calendar: calendar)
        #expect(range.duration == 30 * 86_400)
    }

    @Test func ninetyDaysIsARollingWindow() {
        let range = OverviewPeriod.ninetyDays.range(now: now, calendar: calendar)
        #expect(range.duration == 90 * 86_400)
        #expect(range.end == now)
    }

    @Test func aYearIsThreeHundredSixtyFiveRollingDays() {
        let range = OverviewPeriod.year.range(now: now, calendar: calendar)
        #expect(range.duration == 365 * 86_400)
        #expect(range.end == now)
    }

    @Test func allTimeStartsAtTheDistantPast() {
        let range = OverviewPeriod.allTime.range(now: now, calendar: calendar)
        #expect(range.start == .distantPast)
        #expect(range.end == now)
    }
}

@MainActor
@Suite struct OverviewModelTests {
    let now = Date(timeIntervalSince1970: 1_791_703_800)
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        return c
    }

    func model(_ ledger: FakeLedger, labels: [String: String] = [:]) -> OverviewModel {
        OverviewModel(ledger: ledger, roots: { LedgerRoots() }, accountLabel: { labels[$0] ?? $0 },
                      now: { [now] in now }, calendar: calendar)
    }

    @Test func harnessesAreSharedOutLargestFirst() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: [
            "codex": totals(tokens: 116, cost: 10),
            "claude": totals(tokens: 810, cost: 70),
            "devin": totals(tokens: 74, cost: 0.5, unpriced: 70),
        ])
        let model = model(ledger)
        await model.load(scan: false)
        let summary = try #require(model.summary)
        #expect(summary.harnesses.map(\.harness) == ["claude", "codex", "devin"])
        #expect(summary.harnesses.map(\.share) == [0.81, 0.116, 0.074])
        #expect(summary.totals.totalTokens == 1000)
        #expect(summary.totals.costUSD == 80.5)
        #expect(summary.totals.unpricedTokens == 70)
        #expect(model.updatedAt == now)
    }

    @Test func dailyRowsCombineHarnessesAndModelsNewestFirst() async throws {
        let ledger = FakeLedger()
        let oct10 = Date(timeIntervalSince1970: 1_791_561_600)   // 2026-10-10 00:00 SGT
        let oct11 = Date(timeIntervalSince1970: 1_791_648_000)
        await ledger.set(harnesses: ["claude": totals(tokens: 6, cost: 6)], days: [
            DailyUsage(day: oct10, harness: "claude", model: "a", totals: totals(tokens: 1, cost: 1)),
            DailyUsage(day: oct11, harness: "claude", model: "a", totals: totals(tokens: 2, cost: 2)),
            DailyUsage(day: oct11, harness: "codex", model: "b", totals: totals(tokens: 3, cost: 3)),
        ])
        let model = model(ledger)
        model.period = .month
        await model.load(scan: false)
        let days = try #require(model.summary?.days)
        #expect(days.map(\.day) == [oct11, oct10])
        #expect(days.map(\.totals.totalTokens) == [5, 1])
    }

    @Test func topModelsAndModelsPerHarnessComeFromThePeriodsDays() async throws {
        let ledger = FakeLedger()
        let oct10 = Date(timeIntervalSince1970: 1_791_561_600)
        let oct11 = Date(timeIntervalSince1970: 1_791_648_000)
        await ledger.set(harnesses: ["claude": totals(tokens: 9, cost: 9), "codex": totals(tokens: 1, cost: 1)], days: [
            DailyUsage(day: oct10, harness: "claude", model: "opus", totals: totals(tokens: 5, cost: 5)),
            DailyUsage(day: oct11, harness: "claude", model: "opus", totals: totals(tokens: 2, cost: 2)),
            DailyUsage(day: oct11, harness: "claude", model: "sonnet", totals: totals(tokens: 2, cost: 2)),
            DailyUsage(day: oct11, harness: "codex", model: "gpt", totals: totals(tokens: 1, cost: 1)),
        ])
        let model = model(ledger)
        await model.load(scan: false)
        let summary = try #require(model.summary)
        #expect(summary.models.map(\.model) == ["opus", "sonnet", "gpt"])
        #expect(summary.models.map(\.share) == [0.7, 0.2, 0.1])
        #expect(summary.harnesses.map(\.modelCount) == [2, 1])
        #expect(summary.activeDays == 2)
    }

    @Test func topModelsCarryTheHarnessesThatUsedThem() async throws {
        let ledger = FakeLedger()
        let oct11 = Date(timeIntervalSince1970: 1_791_648_000)
        await ledger.set(harnesses: ["claude": totals(tokens: 5, cost: 0), "devin": totals(tokens: 2, cost: 0)], days: [
            DailyUsage(day: oct11, harness: "claude", model: "opus", totals: totals(tokens: 5, cost: 0)),
            DailyUsage(day: oct11, harness: "devin", model: "opus", totals: totals(tokens: 2, cost: 0)),
        ])
        let model = model(ledger)
        await model.load(scan: false)
        let models = try #require(model.summary?.models)
        #expect(models.count == 1)
        #expect(models[0].harnesses == ["claude", "devin"])
    }

    @Test func allTimeReachesBackToTheFirstDay() async throws {
        let ledger = FakeLedger()
        let old = Date(timeIntervalSince1970: 1_600_000_000)   // 2020-09-13
        let oct11 = Date(timeIntervalSince1970: 1_791_648_000)
        await ledger.set(harnesses: ["claude": totals(tokens: 3, cost: 0)], days: [
            DailyUsage(day: old, harness: "claude", model: "a", totals: totals(tokens: 1, cost: 0)),
            DailyUsage(day: oct11, harness: "claude", model: "a", totals: totals(tokens: 2, cost: 0)),
        ])
        let model = model(ledger)
        model.period = .allTime
        await model.load(scan: false)
        #expect(model.summary?.days.map(\.day) == [oct11, old])
    }

    @Test func recentModelsCoverTheLastThirtyDaysWhateverThePeriod() async throws {
        // The widget's top models must not change with the Overview's picker.
        let ledger = FakeLedger()
        let oct11 = Date(timeIntervalSince1970: 1_791_648_000)
        let sep1 = Date(timeIntervalSince1970: 1_788_192_000)    // 2026-09-01: 40 days back
        await ledger.set(harnesses: [:], days: [
            DailyUsage(day: sep1, harness: "claude", model: "old", totals: totals(tokens: 900, cost: 0)),
            DailyUsage(day: oct11, harness: "claude", model: "a", totals: totals(tokens: 30, cost: 0)),
            DailyUsage(day: oct11, harness: "codex", model: "b", totals: totals(tokens: 10, cost: 0)),
        ])
        let model = model(ledger)
        model.period = .today
        await model.load(scan: false)
        #expect(model.recentModels.map(\.model) == ["a", "b"])
        #expect(model.recentModels.map(\.share) == [0.75, 0.25])
    }

    @Test func accountsAreNamedAndOrderedBySpend() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: [:], accounts: [
            "/Users/k/.claude": totals(tokens: 10, cost: 9),
            "/Users/k/.quiver/accounts/codex/codex-work/home": totals(tokens: 20, cost: 3),
        ])
        let model = model(ledger, labels: [
            "/Users/k/.claude": "Claude: Default",
            "/Users/k/.quiver/accounts/codex/codex-work/home": "Codex: Work",
        ])
        await model.load(scan: false)
        #expect(model.summary?.accounts.map(\.label) == ["Claude: Default", "Codex: Work"])
    }

    @Test func accountsCarryTheHarnessTheirUsageCameFrom() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: [:], accounts: [
            "/Users/k/.claude": totals(tokens: 10, cost: 9),
            "/Users/k/.quiver/accounts/codex/codex-work/home": totals(tokens: 20, cost: 3),
            "/Users/k/elsewhere": totals(tokens: 5, cost: 1),
        ], accountHarnesses: [
            "/Users/k/.claude": "claude",
            "/Users/k/.quiver/accounts/codex/codex-work/home": "codex",
        ])
        let model = model(ledger)
        await model.load(scan: false)
        let accounts = try #require(model.summary?.accounts)
        #expect(accounts.map(\.harness) == ["claude", "codex", nil])
    }

    @Test func aScanRunsBeforeQueryingAndItsFailureIsShown() async {
        let ledger = FakeLedger()
        await ledger.failScans(with: ScanFailed())
        let model = model(ledger)
        await model.load(scan: true)
        #expect(await ledger.scans == 1)
        #expect(model.error == "Couldn't read your usage logs: disk unreadable")
        #expect(model.completedScans == 0)
    }

    /// A cancelled scan is not a completed one: no count, no cleared
    /// error, just a rebuilt view over whatever committed before it.
    @Test func aCancelledScanDoesNotCountAsComplete() async {
        let ledger = FakeLedger()
        await ledger.failScans(with: CancellationError())
        let model = model(ledger)
        await model.load(scan: true)
        #expect(await ledger.scans == 1)
        #expect(model.completedScans == 0)
        #expect(model.error == nil)
    }

    @Test func switchingPeriodOnlyRequeriesThatPeriod() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 2)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        let before = await ledger.ranges.count
        let spend = model.spend
        model.period = .allTime
        await model.reloadPeriod()
        // The warmed cache answers; the ledger is not asked again.
        #expect(await ledger.ranges.count == before)
        #expect(model.spend == spend)
        #expect(model.summary?.period == .allTime)
        #expect(await ledger.scans == 0)
    }

    @Test func aPeriodSwitchDuringAScanKeepsTheScansOtherFigures() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 2)])
        await ledger.holdNextScan()
        let model = model(ledger)
        let scan = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        model.period = .today
        await model.reloadPeriod()
        await ledger.releaseScan()
        await scan.value
        #expect(model.spend != nil)
        #expect(model.updatedAt != nil)
        // The scan's summary was for the old period; the newer one stays.
        #expect(model.summary?.period == .today)
    }

    @Test func aNewerPriceListIsAppliedBeforeScanning() async {
        let ledger = FakeLedger()
        let model = OverviewModel(ledger: ledger, roots: { LedgerRoots() }, accountLabel: { $0 },
                                  refreshPricing: { .empty }, now: { [now] in now }, calendar: calendar)
        await model.load(scan: false)
        #expect(await ledger.pricingUpdates == 0)
        await model.load(scan: true)
        #expect(await ledger.pricingUpdates == 1)
    }

    @Test func eachSuccessfulScanIsCounted() async {
        let ledger = FakeLedger()
        let model = model(ledger)
        await model.load(scan: false)
        #expect(model.completedScans == 0)
        await model.load(scan: true)
        await model.load(scan: true)
        #expect(model.completedScans == 2)
    }

    @Test func spendForTheMenuBarCoversTodayYesterdayAndThirtyDays() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 2)])
        let model = model(ledger)
        await model.load(scan: false)
        let spend = try #require(model.spend)
        #expect(spend.map(\.period) == [.today, .yesterday, .thirtyDays])
        // Yesterday is the whole previous local day.
        let ranges = await ledger.ranges
        #expect(ranges.contains(DateInterval(start: Date(timeIntervalSince1970: 1_791_561_600),
                                             end: Date(timeIntervalSince1970: 1_791_648_000))))
    }
}

/// A movable `now`, for the freshness rules that depend on the clock.
final class OverviewClock {
    var now = Date(timeIntervalSince1970: 1_791_703_800)   // 2026-10-11 15:30 SGT
}

@MainActor
@Suite struct OverviewCacheTests {
    let clock = OverviewClock()
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        return c
    }

    func model(_ ledger: FakeLedger) -> OverviewModel {
        OverviewModel(ledger: ledger, roots: { LedgerRoots() }, accountLabel: { $0 },
                      now: { [clock] in clock.now }, calendar: calendar)
    }

    @Test func warmingFillsEveryPeriodAndSwitchesThenCostNothing() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        #expect(model.cachedPeriods == Set(OverviewPeriod.allCases))
        let before = await ledger.ranges.count
        for period in OverviewPeriod.allCases {
            model.period = period
            await model.reloadPeriod()
            #expect(model.summary?.period == period)
        }
        #expect(await ledger.ranges.count == before)
    }

    @Test func aScanThatReadFilesInvalidatesEveryEntry() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        var report = ScanReport()
        report.filesRead = 2
        await ledger.set(report: report)
        let before = await ledger.ranges.count
        await model.load(scan: true)
        await model.warming?.value
        // Selected summary, three spend slices, six warmed periods: all new.
        #expect(await ledger.ranges.count == before + 10)
    }

    @Test func aScanThatReadNothingKeepsEveryEntry() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        let before = await ledger.ranges.count
        await model.load(scan: true)
        await model.warming?.value
        // Only the menu bar's three spend slices asked again.
        #expect(await ledger.ranges.count == before + 3)
    }

    @Test func aNewDayInvalidatesTheCache() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        let before = await ledger.ranges.count
        clock.now = clock.now.addingTimeInterval(86_400)
        model.period = .allTime
        await model.reloadPeriod()
        #expect(await ledger.ranges.count == before + 1)
        #expect(model.summary?.period == .allTime)
    }

    @Test func aStaleRollingEntryShowsAtOnceThenRefreshes() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        // Eleven minutes on, the rolling entry is stale and the data moved.
        clock.now = clock.now.addingTimeInterval(660)
        await ledger.set(harnesses: ["claude": totals(tokens: 9, cost: 9)])
        await ledger.holdHarnessQueries(fromCall: 1)
        model.period = .week
        let reload = Task { await model.reloadPeriod() }
        try #require(await ledger.harnessParks.reach(1))
        // The cached figure is already up while the recompute is parked.
        #expect(model.summary?.totals.totalTokens == 1)
        await ledger.releaseHarnessQueries()
        await reload.value
        #expect(model.summary?.totals.totalTokens == 9)
    }

    @Test func aNewLoadStopsTheWarmingPass() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        // Calls 1-4 are load's own (summary + three spend slices); call 5 on
        // is the warming pass, which parks inside its first period.
        await ledger.holdHarnessQueries(fromCall: 5)
        await model.load(scan: false)
        try #require(await ledger.harnessParks.reach(1))
        let second = Task { await model.load(scan: false) }
        await ledger.releaseHarnessQueries()
        await second.value
        await model.warming?.value
        #expect(model.cachedPeriods == Set(OverviewPeriod.allCases))
        // 4 (load 1) + 1 (warm's parked call) + 3 (load 2's spend, its summary
        // was a fresh cache hit) + 6 (warm 2): the cancelled pass stored
        // nothing, so its period is computed again.
        #expect(await ledger.ranges.count == 14)
    }

    @Test func aFailedScanInvalidatesEveryEntry() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        #expect(model.cachedPeriods == Set(OverviewPeriod.allCases))
        await ledger.failScans(with: ScanFailed())
        await model.load(scan: true)
        // Files may have committed before the throw, so no cached figure
        // can be trusted: a period switch asks the ledger again.
        model.period = .allTime
        let before = await ledger.ranges.count
        await model.reloadPeriod()
        #expect(await ledger.ranges.count == before + 1)
        #expect(model.error == "Couldn't read your usage logs: disk unreadable")
    }

    @Test func aCacheWriteStampedBeforeAScanIsRejected() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        let model = model(ledger)
        await model.load(scan: false)
        await model.warming?.value
        let stamped = model.revision
        var report = ScanReport()
        report.filesRead = 1
        await ledger.set(report: report)
        await model.load(scan: true)
        await model.warming?.value
        var stale = try #require(model.summary)
        stale.period = .today
        stale.totals = totals(tokens: 999, cost: 0)
        // Queried under the old revision, its figures must not overwrite the
        // post-scan entry.
        model.storeCached(stale, at: clock.now, stamped: stamped)
        model.period = .today
        await model.reloadPeriod()
        #expect(model.summary?.totals.totalTokens == 1)
        // The same write stamped with the current revision does land.
        stale.period = .week
        model.storeCached(stale, at: clock.now, stamped: model.revision)
        model.period = .week
        await model.reloadPeriod()
        #expect(model.summary?.totals.totalTokens == 999)
    }

    // A warming pass left parked would hang the wait on it; fail instead.
    @Test(.timeLimit(.minutes(1)))
    func aRevalidationSupersededByAnotherReloadStoresNothing() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        // Calls 1-4 are load's own; every later one parks.
        await ledger.holdHarnessQueries(fromCall: 5)
        let model = model(ledger)
        await model.load(scan: false)
        // The warming pass runs on the main actor, so it has not started
        // yet: cancelled now, it ends before asking anything, leaving only
        // the selected period cached.
        model.warming?.cancel()
        await model.warming?.value
        model.period = .allTime
        let first = Task { await model.reloadPeriod() }
        try #require(await ledger.harnessParks.reach(1))
        model.period = .today
        let second = Task { await model.reloadPeriod() }
        try #require(await ledger.harnessParks.reach(2))
        await ledger.releaseHarnessQueries()
        await first.value
        await second.value
        // The first reload's generation was superseded while it was parked:
        // its figures were neither published nor cached.
        #expect(model.summary?.period == .today)
        #expect(model.cachedPeriods == [.month, .today])
    }
}

/// Stands in for the scan watcher's sleep between progress reads. Sleep
/// `n` parks on its own gate until the test ends it, so the test decides
/// when each read happens instead of racing a wall-clock interval. A
/// parked sleep throws when the watcher is cancelled, as `Task.sleep` does.
final class WatchTicks: Sendable {
    private struct State {
        var gates: [HoldGate] = []
        var calls = 0
        /// Sleeps the test has ended so far.
        var ended = 0
    }

    private let state = Mutex(State())

    private func gate(_ n: Int) -> HoldGate {
        state.withLock { s in
            while s.gates.count < n { s.gates.append(HoldGate()) }
            return s.gates[n - 1]
        }
    }

    /// The sleep to hand the model.
    var sleep: @Sendable (Duration) async throws -> Void {
        { [self] _ in
            let n = state.withLock { s -> Int in
                s.calls += 1
                return s.calls
            }
            try await gate(n).pass()
        }
    }

    /// Ends the next `reads` sleeps in turn, each once the watcher reaches
    /// it, and returns once the watcher is parked in the one after: by
    /// then every read after those sleeps, and any rebuild it started, has
    /// finished. False after `gateTimeout`.
    func run(_ reads: Int) async -> Bool {
        let first = state.withLock { s -> Int in
            defer { s.ended += reads }
            return s.ended + 1
        }
        for n in first ..< first + reads {
            guard await gate(n).arrived() else { return false }
            gate(n).open()
        }
        return await gate(first + reads).arrived()
    }
}

/// The first scan's "files done of total" and the partial figures shown
/// while it runs.
@MainActor
@Suite struct OverviewScanProgressTests {
    var calendar: Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "Asia/Singapore")!
        return c
    }
    let now = Date(timeIntervalSince1970: 1_791_703_800)

    /// A model whose progress reads happen when `ticks` lets them.
    func model(_ ledger: FakeLedger, ticks: WatchTicks = WatchTicks()) -> OverviewModel {
        let model = OverviewModel(ledger: ledger, roots: { LedgerRoots() }, accountLabel: { $0 },
                                  scanWatchInterval: .seconds(1),
                                  now: { [now] in now }, calendar: calendar)
        model.scanWatchSleep = ticks.sleep
        return model
    }

    @Test func theFirstScanShowsProgressAndPartialFigures() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 100, cost: 1)])
        await ledger.set(progress: ScanProgress(filesDone: 12, filesTotal: 40))
        await ledger.holdNextScan()
        let ticks = WatchTicks()
        let model = model(ledger, ticks: ticks)
        let scan = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        // The third read rebuilds the figures.
        try #require(await ticks.run(3))
        #expect(model.scanProgress == ScanProgress(filesDone: 12, filesTotal: 40))
        // Figures from what had committed, before the scan finished.
        #expect(model.summary?.totals.totalTokens == 100)
        #expect(model.completedScans == 0)
        #expect(await ledger.scanWaiting)

        await ledger.set(progress: nil)
        await ledger.releaseScan()
        await scan.value
        #expect(model.scanProgress == nil)
        #expect(model.completedScans == 1)
    }

    @Test func aCappedScanFinishingMidwayKeepsThePartialFiguresComing() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 100, cost: 1)])
        await ledger.set(progress: ScanProgress(filesDone: 5, filesTotal: 40))
        await ledger.holdNextScan()
        let ticks = WatchTicks()
        let model = model(ledger, ticks: ticks)
        let full = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        // The search index's reconcile tick: a capped scan that finishes
        // while the full one is still held.
        await model.load(scan: true, recentLimit: 12)
        #expect(model.completedScans == 1)
        #expect(!model.fullScanFinished)

        await ledger.set(harnesses: ["claude": totals(tokens: 250, cost: 2)])
        try #require(await ticks.run(3))
        #expect(model.summary?.totals.totalTokens == 250)
        #expect(model.scanProgress != nil)

        await ledger.set(progress: nil)
        await ledger.releaseScan()
        await full.value
        #expect(model.fullScanFinished)
        #expect(model.scanProgress == nil)
    }

    @Test func partialFiguresWaitForTheThirdRead() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 100, cost: 1)])
        await ledger.set(progress: ScanProgress(filesDone: 1, filesTotal: 40))
        await ledger.holdNextScan()
        let ticks = WatchTicks()
        let model = model(ledger, ticks: ticks)
        let scan = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        // The first two reads show progress and nothing else.
        try #require(await ticks.run(1))
        #expect(model.scanProgress != nil)
        #expect(model.summary == nil)
        try #require(await ticks.run(1))
        #expect(model.summary == nil)
        // The third rebuilds the figures from what has committed.
        try #require(await ticks.run(1))
        #expect(model.summary?.totals.totalTokens == 100)
        #expect(model.completedScans == 0)

        await ledger.set(progress: nil)
        await ledger.releaseScan()
        await scan.value
    }

    // A load left waiting hangs here; fail instead of stalling the run.
    @Test(.timeLimit(.minutes(1))) func fullScansRunOneAtATime() async throws {
        let ledger = FakeLedger()
        await ledger.holdNextScan()
        let model = model(ledger)
        let first = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        let second = Task { await model.load(scan: true) }
        // The second waits instead of scanning beside the held first.
        try #require(await observe { model.queuedFullScans == 1 })
        #expect(await ledger.scans == 1)

        await ledger.releaseScan()
        await first.value
        await second.value
        #expect(await ledger.scans == 2)
        #expect(model.completedScans == 2)
    }

    // A load left waiting hangs here; fail instead of stalling the run.
    @Test(.timeLimit(.minutes(1))) func aFullScanQueuedBehindAnotherIsCoveredByIt() async throws {
        let ledger = FakeLedger()
        await ledger.holdNextScan()
        let model = model(ledger)
        let first = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        // All ask while the first runs: the second's scan starts after the
        // third and fourth asked, so they have nothing left to read.
        let second = Task { await model.load(scan: true) }
        try #require(await observe { model.queuedFullScans == 1 })
        let third = Task { await model.load(scan: true) }
        let fourth = Task { await model.load(scan: true) }
        try #require(await observe { model.queuedFullScans == 3 })

        await ledger.releaseScan()
        await first.value
        await second.value
        // Both were covered by the second; neither may be left waiting.
        await third.value
        await fourth.value
        #expect(await ledger.scans == 2)
    }

    @Test func aCappedScanShowsNoProgress() async throws {
        let ledger = FakeLedger()
        await ledger.set(progress: ScanProgress(filesDone: 1, filesTotal: 2))
        await ledger.holdNextScan()
        let model = model(ledger)
        let scan = Task { await model.load(scan: true, recentLimit: 12) }
        try #require(await ledger.scanParks.reach(1))
        // No watcher started, so nothing reads progress while it is held.
        #expect(model.scanWatchers == 0)
        #expect(model.scanProgress == nil)
        #expect(model.summary == nil)
        await ledger.releaseScan()
        await scan.value
    }

    @Test func noWatchIntervalLeavesProgressOff() async throws {
        let ledger = FakeLedger()
        await ledger.set(progress: ScanProgress(filesDone: 1, filesTotal: 2))
        await ledger.holdNextScan()
        let model = OverviewModel(ledger: ledger, roots: { LedgerRoots() }, accountLabel: { $0 },
                                  now: { [now] in now }, calendar: calendar)
        let scan = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        // No watcher started, so nothing reads progress while it is held.
        #expect(model.scanWatchers == 0)
        #expect(model.scanProgress == nil)
        await ledger.releaseScan()
        await scan.value
    }

    /// The positive control for the two checks above: a full scan with an
    /// interval does start a watcher, counted before the scan is asked.
    @Test func aFullScanWithAnIntervalStartsAWatcher() async throws {
        let ledger = FakeLedger()
        await ledger.holdNextScan()
        let model = model(ledger)
        let scan = Task { await model.load(scan: true) }
        try #require(await ledger.scanParks.reach(1))
        #expect(model.scanWatchers == 1)
        await ledger.releaseScan()
        await scan.value
        #expect(model.scanWatchers == 0)
    }
}
