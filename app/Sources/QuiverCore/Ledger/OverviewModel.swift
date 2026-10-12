import Foundation
import Observation

/// What the Overview page and the menu bar ask of the ledger.
public protocol LedgerQuerying: Sendable {
    func scan(roots: LedgerRoots) async throws -> ScanReport
    func byHarness(in range: DateInterval) async -> [String: UsageTotals]
    func byAccount(in range: DateInterval) async -> [String: UsageTotals]
    /// The harness each account home's usage came from.
    func harnessByAccount(in range: DateInterval) async -> [String: String]
    func daily(in range: DateInterval, calendar: Calendar) async -> [DailyUsage]
    func setPricing(_ catalog: PricingCatalog) async
}

extension Ledger: LedgerQuerying {}

/// The Overview page's period picker.
public enum OverviewPeriod: String, CaseIterable, Identifiable, Sendable {
    case today, week, month, thirtyDays, ninetyDays, year, allTime

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .today: "Today"
        case .week: "7 days"
        case .month: "This month"
        case .thirtyDays: "30 days"
        case .ninetyDays: "90 days"
        case .year: "365 days"
        case .allTime: "All time"
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
        case .ninetyDays:
            DateInterval(start: now.addingTimeInterval(-90 * 86_400), end: now)
        case .year:
            DateInterval(start: now.addingTimeInterval(-365 * 86_400), end: now)
        case .allTime:
            DateInterval(start: .distantPast, end: now)
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
    /// Distinct models this harness used in the period.
    public var modelCount = 0
}

/// One model's slice of the period, for the top models list.
public struct ModelShare: Sendable, Equatable, Identifiable {
    public var id: String { model }
    public var model: String
    public var totals: UsageTotals
    /// Of the period's tokens, 0...1.
    public var share: Double
    /// The harnesses that ran the model, most tokens first.
    public var harnesses: [String] = []

    public init(model: String, totals: UsageTotals, share: Double) {
        self.model = model
        self.totals = totals
        self.share = share
    }
}

public struct DayRow: Sendable, Equatable, Identifiable {
    public var id: Date { day }
    public var day: Date
    public var totals: UsageTotals

    public init(day: Date, totals: UsageTotals) {
        self.day = day
        self.totals = totals
    }
}

public struct AccountRow: Sendable, Equatable, Identifiable {
    public var id: String { home }
    public var home: String
    public var label: String
    public var totals: UsageTotals
    /// The harness the account's usage came from, for its logo; nil when
    /// the ledger can't say.
    public var harness: String? = nil
}

public struct OverviewSummary: Sendable, Equatable {
    public var period: OverviewPeriod
    public var range: DateInterval
    public var totals: UsageTotals
    public var harnesses: [HarnessShare]
    public var days: [DayRow]
    public var accounts: [AccountRow]
    /// Largest first.
    public var models: [ModelShare] = []
    /// Days in the period with any usage.
    public var activeDays: Int { days.filter { $0.totals.totalTokens > 0 }.count }
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
    /// The last 30 days' models, largest first, whatever the period picker
    /// says. For the widgets.
    public private(set) var recentModels: [ModelShare] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    /// Successful scans so far; other views reload their ledger figures when it changes.
    public private(set) var completedScans = 0
    /// When the figures were last rebuilt.
    public private(set) var updatedAt: Date?

    private let ledger: LedgerQuerying
    private let roots: @Sendable () async -> LedgerRoots
    private let accountLabel: (String) -> String
    private let now: () -> Date
    private let calendar: Calendar
    private let refreshPricing: @Sendable () async -> PricingCatalog?
    /// Bumped by every full load: only the newest writes spend and activity.
    private var loadGeneration = 0
    /// Bumped by every load and period reload: only the newest writes the summary.
    private var summaryGeneration = 0
    private var inFlight = 0 { didSet { isLoading = inFlight > 0 } }

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
        loadGeneration += 1
        summaryGeneration += 1
        let mine = loadGeneration
        let mySummary = summaryGeneration
        inFlight += 1
        defer { inFlight -= 1 }
        if scan {
            if let catalog = await refreshPricing() { await ledger.setPricing(catalog) }
            do {
                _ = try await ledger.scan(roots: roots())
                error = nil
                completedScans += 1
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
        let halfYearRows = await ledger.daily(in: halfYear, calendar: calendar)
        let activity = Self.combineDays(halfYearRows)
        let thirtyDays = OverviewPeriod.thirtyDays.range(now: now, calendar: calendar)
        // The same rolling range as the Overview's "30 days", to the minute.
        let recentModels = Self.models(await ledger.daily(in: thirtyDays, calendar: calendar))

        if mySummary == summaryGeneration { self.summary = summary }
        guard mine == loadGeneration else { return }
        self.spend = spend
        self.activity = activity
        self.recentModels = recentModels
        updatedAt = now
    }

    /// Rebuild only the period's figures, after the picker changes. The menu
    /// bar's spend and the heatmap don't depend on the period.
    public func reloadPeriod() async {
        summaryGeneration += 1
        let mine = summaryGeneration
        inFlight += 1
        defer { inFlight -= 1 }
        let summary = await summarize(period, now: now())
        guard mine == summaryGeneration else { return }
        self.summary = summary
    }

    private func summarize(_ period: OverviewPeriod, now: Date) async -> OverviewSummary {
        let range = period.range(now: now, calendar: calendar)
        let harnesses = await ledger.byHarness(in: range)
        let accounts = await ledger.byAccount(in: range)
        let accountHarnesses = await ledger.harnessByAccount(in: range)
        let daily = await ledger.daily(in: range, calendar: calendar)
        var total = UsageTotals()
        for value in harnesses.values { total.add(value) }
        let grand = Double(max(total.totalTokens, 1))
        var modelsByHarness: [String: Set<String>] = [:]
        for row in daily { modelsByHarness[row.harness, default: []].insert(row.model) }
        return OverviewSummary(
            period: period,
            range: range,
            totals: total,
            harnesses: harnesses
                .map { HarnessShare(harness: $0.key, totals: $0.value, share: Double($0.value.totalTokens) / grand,
                                    modelCount: modelsByHarness[$0.key]?.count ?? 0) }
                .sorted { ($0.totals.totalTokens, $1.harness) > ($1.totals.totalTokens, $0.harness) },
            days: Self.combineDays(daily).sorted { $0.day > $1.day },
            accounts: accounts
                .map { AccountRow(home: $0.key, label: accountLabel($0.key), totals: $0.value,
                                  harness: accountHarnesses[$0.key]) }
                .sorted { $0.totals.costUSD > $1.totals.costUSD },
            models: Self.models(daily))
    }

    /// Each model's share of the rows' tokens, largest first, with the
    /// harnesses that ran it, most tokens first.
    static func models(_ rows: [DailyUsage]) -> [ModelShare] {
        var byModel: [String: UsageTotals] = [:]
        var harnessTokens: [String: [String: Int]] = [:]
        for row in rows {
            byModel[row.model, default: UsageTotals()].add(row.totals)
            harnessTokens[row.model, default: [:]][row.harness, default: 0] += row.totals.totalTokens
        }
        let grand = Double(max(byModel.values.reduce(0) { $0 + $1.totalTokens }, 1))
        return byModel
            .map { model, totals in
                var share = ModelShare(model: model, totals: totals,
                                       share: Double(totals.totalTokens) / grand)
                share.harnesses = (harnessTokens[model] ?? [:])
                    .sorted { ($0.value, $1.key) > ($1.value, $0.key) }
                    .map(\.key)
                return share
            }
            .sorted { ($0.totals.totalTokens, $1.model) > ($1.totals.totalTokens, $0.model) }
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
