import Foundation
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
    private(set) var harnessWaiting = false

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

    func scan(roots: LedgerRoots) async throws -> ScanReport {
        scans += 1
        if holdScans {
            scanWaiting = true
            await withCheckedContinuation { scanGate = $0 }
            scanWaiting = false
        }
        if let scanError { throw scanError }
        return nextReport
    }

    func byHarness(in range: DateInterval) async -> [String: UsageTotals] {
        ranges.append(range)
        if let from = holdHarnessFrom, ranges.count >= from {
            harnessWaiting = true
            await withCheckedContinuation { harnessGates.append($0) }
            harnessWaiting = false
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
        while !(await ledger.scanWaiting) { await Task.yield() }
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

    /// Waits for the fake to report a parked query, with a bound so a broken
    /// wait fails instead of spinning forever.
    func untilParked(_ ledger: FakeLedger) async {
        for _ in 0..<4_000 where !(await ledger.harnessWaiting) { await Task.yield() }
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
        await untilParked(ledger)
        #expect(await ledger.harnessWaiting)
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
        await untilParked(ledger)
        #expect(await ledger.harnessWaiting)
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

    /// Waits until the fake has recorded `count` byHarness calls, parked or
    /// answered, with the same bound as `untilParked`.
    func untilRanges(_ ledger: FakeLedger, _ count: Int) async {
        for _ in 0..<4_000 where await ledger.ranges.count < count { await Task.yield() }
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

    @Test func aRevalidationSupersededByAnotherReloadStoresNothing() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 1)])
        // Calls 1-4 are load's own; call 5 on parks, so warming stops with
        // only the selected period cached and stays parked for the test.
        await ledger.holdHarnessQueries(fromCall: 5)
        let model = model(ledger)
        await model.load(scan: false)
        model.warming?.cancel()
        model.period = .allTime
        let first = Task { await model.reloadPeriod() }
        await untilRanges(ledger, 6)
        model.period = .today
        let second = Task { await model.reloadPeriod() }
        await untilRanges(ledger, 7)
        await ledger.releaseHarnessQueries()
        await first.value
        await second.value
        // The first reload's generation was superseded while it was parked:
        // its figures were neither published nor cached.
        #expect(model.summary?.period == .today)
        #expect(model.cachedPeriods == [.month, .today])
    }
}
