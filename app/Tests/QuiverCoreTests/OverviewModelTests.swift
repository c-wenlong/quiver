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
    var days: [DailyUsage] = []
    var scanError: Error?
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

    func set(harnesses: [String: UsageTotals], accounts: [String: UsageTotals] = [:], days: [DailyUsage] = []) {
        self.harnesses = harnesses
        self.accounts = accounts
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
        return ScanReport()
    }

    func byHarness(in range: DateInterval) async -> [String: UsageTotals] {
        ranges.append(range)
        return harnesses
    }

    func byAccount(in range: DateInterval) async -> [String: UsageTotals] { accounts }

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

    @Test func aScanRunsBeforeQueryingAndItsFailureIsShown() async {
        let ledger = FakeLedger()
        await ledger.failScans(with: ScanFailed())
        let model = model(ledger)
        await model.load(scan: true)
        #expect(await ledger.scans == 1)
        #expect(model.error == "Couldn't read your usage logs: disk unreadable")
    }

    @Test func switchingPeriodOnlyRequeriesThatPeriod() async throws {
        let ledger = FakeLedger()
        await ledger.set(harnesses: ["claude": totals(tokens: 1, cost: 2)])
        let model = model(ledger)
        await model.load(scan: false)
        let before = await ledger.ranges.count
        let spend = model.spend
        model.period = .today
        await model.reloadPeriod()
        #expect(await ledger.ranges.count == before + 1)
        #expect(model.spend == spend)
        #expect(model.summary?.period == .today)
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
