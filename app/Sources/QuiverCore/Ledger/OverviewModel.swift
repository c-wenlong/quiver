import Foundation
import Observation

/// What the Overview page and the menu bar ask of the ledger.
public protocol LedgerQuerying: Sendable {
    func scan(roots: LedgerRoots) async throws -> ScanReport
    func byHarness(in range: DateInterval) async -> [String: UsageTotals]
    func byAccount(in range: DateInterval) async -> [String: UsageTotals]
    func daily(in range: DateInterval, calendar: Calendar) async -> [DailyUsage]
    func setPricing(_ catalog: PricingCatalog) async
}

extension Ledger: LedgerQuerying {}

/// The Overview page's period picker.
public enum OverviewPeriod: String, CaseIterable, Identifiable, Sendable {
    case today, week, month, thirtyDays

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .today: "Today"
        case .week: "7 days"
        case .month: "This month"
        case .thirtyDays: "30 days"
        }
    }

    public func range(now: Date, calendar: Calendar) -> DateInterval {
        switch self {
        case .today:
            DateInterval(start: calendar.startOfDay(for: now), end: now)
        case .week:
            DateInterval(start: now.addingTimeInterval(-7 * 86_400), end: now)
        case .month:
            DateInterval(start: calendar.dateInterval(of: .month, for: now)?.start ?? now, end: now)
        case .thirtyDays:
            DateInterval(start: now.addingTimeInterval(-30 * 86_400), end: now)
        }
    }
}

/// One harness's slice of the period.
public struct HarnessShare: Sendable, Equatable, Identifiable {
    public var id: String { harness }
    public var harness: String
    public var totals: UsageTotals
    /// Of the period's tokens, 0...1.
    public var share: Double
}

public struct DayRow: Sendable, Equatable, Identifiable {
    public var id: Date { day }
    public var day: Date
    public var totals: UsageTotals
}

public struct AccountRow: Sendable, Equatable, Identifiable {
    public var id: String { home }
    public var home: String
    public var label: String
    public var totals: UsageTotals
}

public struct OverviewSummary: Sendable, Equatable {
    public var period: OverviewPeriod
    public var range: DateInterval
    public var totals: UsageTotals
    public var harnesses: [HarnessShare]
    public var days: [DayRow]
    public var accounts: [AccountRow]
}

/// Spend for the menu bar's donut.
public struct SpendSlice: Sendable, Equatable, Identifiable {
    public enum Period: String, Sendable { case today, yesterday, thirtyDays }
    public var id: Period { period }
    public var period: Period
    public var byHarness: [String: UsageTotals]

    public var costUSD: Double { byHarness.values.reduce(0) { $0 + $1.costUSD } }
}

@MainActor
@Observable
public final class OverviewModel {
    public var period: OverviewPeriod = .month
    public private(set) var summary: OverviewSummary?
    public private(set) var spend: [SpendSlice]?
    /// Days of the last half year with their token totals, for the heatmap.
    public private(set) var activity: [DayRow] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    /// When the figures were last rebuilt.
    public private(set) var updatedAt: Date?

    private let ledger: LedgerQuerying
    private let roots: @Sendable () async -> LedgerRoots
    private let accountLabel: (String) -> String
    private let now: () -> Date
    private let calendar: Calendar
    private let refreshPricing: @Sendable () async -> PricingCatalog?
    private var generation = 0

    /// - Parameter refreshPricing: a newer price list when one is due, else nil;
    ///   asked before every scan.
    public init(ledger: LedgerQuerying, roots: @escaping @Sendable () async -> LedgerRoots,
                accountLabel: @escaping (String) -> String,
                refreshPricing: @escaping @Sendable () async -> PricingCatalog? = { nil },
                now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.ledger = ledger
        self.refreshPricing = refreshPricing
        self.roots = roots
        self.accountLabel = accountLabel
        self.now = now
        self.calendar = calendar
    }

    /// Optionally scan the logs for new usage, then rebuild every figure.
    public func load(scan: Bool) async {
        generation += 1
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        if scan {
            if let catalog = await refreshPricing() { await ledger.setPricing(catalog) }
            do {
                _ = try await ledger.scan(roots: roots())
                error = nil
            } catch is CancellationError {
                return
            } catch {
                self.error = "Couldn't read your usage logs: \(error.localizedDescription)"
                return
            }
        }

        let now = now()
        let summary = await summarize(period, now: now)
        let todayStart = calendar.startOfDay(for: now)
        let yesterdayStart = calendar.date(byAdding: .day, value: -1, to: todayStart) ?? todayStart
        let spend = [
            SpendSlice(period: .today, byHarness: await ledger.byHarness(in: DateInterval(start: todayStart, end: now))),
            SpendSlice(period: .yesterday, byHarness: await ledger.byHarness(in: DateInterval(start: yesterdayStart, end: todayStart))),
            SpendSlice(period: .thirtyDays, byHarness: await ledger.byHarness(
                in: OverviewPeriod.thirtyDays.range(now: now, calendar: calendar))),
        ]
        let halfYear = DateInterval(start: calendar.date(byAdding: .day, value: -182, to: todayStart) ?? todayStart, end: now)
        let activity = Self.combineDays(await ledger.daily(in: halfYear, calendar: calendar))

        guard mine == generation else { return }
        self.summary = summary
        self.spend = spend
        self.activity = activity
        updatedAt = now
    }

    /// Rebuild only the period's figures, after the picker changes. The menu
    /// bar's spend and the heatmap don't depend on the period.
    public func reloadPeriod() async {
        generation += 1
        let mine = generation
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        let summary = await summarize(period, now: now())
        guard mine == generation else { return }
        self.summary = summary
    }

    private func summarize(_ period: OverviewPeriod, now: Date) async -> OverviewSummary {
        let range = period.range(now: now, calendar: calendar)
        let harnesses = await ledger.byHarness(in: range)
        let accounts = await ledger.byAccount(in: range)
        let daily = await ledger.daily(in: range, calendar: calendar)
        var total = UsageTotals()
        for value in harnesses.values { total.add(value) }
        let grand = Double(max(total.totalTokens, 1))
        return OverviewSummary(
            period: period,
            range: range,
            totals: total,
            harnesses: harnesses
                .map { HarnessShare(harness: $0.key, totals: $0.value, share: Double($0.value.totalTokens) / grand) }
                .sorted { ($0.totals.totalTokens, $1.harness) > ($1.totals.totalTokens, $0.harness) },
            days: Self.combineDays(daily).sorted { $0.day > $1.day },
            accounts: accounts
                .map { AccountRow(home: $0.key, label: accountLabel($0.key), totals: $0.value) }
                .sorted { $0.totals.costUSD > $1.totals.costUSD })
    }

    /// Reload on a timer until cancelled, scanning for new usage each time.
    public func keepFresh(every interval: Duration = .seconds(300)) async {
        while !Task.isCancelled {
            await load(scan: true)
            do { try await Task.sleep(for: interval) } catch { return }
        }
    }

    static func combineDays(_ rows: [DailyUsage]) -> [DayRow] {
        var byDay: [Date: UsageTotals] = [:]
        for row in rows { byDay[row.day, default: UsageTotals()].add(row.totals) }
        return byDay.map { DayRow(day: $0.key, totals: $0.value) }.sorted { $0.day < $1.day }
    }
}
