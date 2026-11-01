import Foundation
import Observation

/// The Usage page's data: limits from `/v1/usage` and one overview per
/// period from `/v1/app/overview`, each loaded on first need.
@MainActor @Observable
final class UsageModel {
    private(set) var usage: UsageResponse?
    private(set) var usageError: String?
    private(set) var overviews: [UsagePeriod: OverviewResponse] = [:]
    private(set) var overviewErrors: [UsagePeriod: String] = [:]
    private(set) var loadedAt: Date?
    var period: UsagePeriod = .week

    /// Reopening the tab within this long shows what's there.
    static let freshFor: TimeInterval = 60

    init(usage: UsageResponse? = nil, overviews: [UsagePeriod: OverviewResponse] = [:], period: UsagePeriod = .week) {
        self.usage = usage
        self.overviews = overviews
        self.period = period
    }

    func loadIfStale(app: AppModel) async {
        if let loadedAt, Date().timeIntervalSince(loadedAt) < Self.freshFor, overviews[period] != nil { return }
        await load(app: app, refresh: false)
    }

    /// Pull to refresh: refetches limits (`?refresh=1`) and the period on
    /// screen; the other periods reload when picked.
    func refresh(app: AppModel) async {
        let keep = overviews[period]
        overviews = keep.map { [period: $0] } ?? [:]
        await load(app: app, refresh: true)
    }

    private func load(app: AppModel, refresh: Bool) async {
        async let limits: Void = loadUsage(app: app, refresh: refresh)
        async let spend: Void = loadPeriod(app: app, period, force: true)
        _ = await (limits, spend)
        // A cancelled load fetched nothing, so the next open still loads.
        guard !Task.isCancelled else { return }
        loadedAt = Date()
    }

    func loadUsage(app: AppModel, refresh: Bool) async {
        guard let client = app.client else { return }
        do {
            usage = try await client.usage(refresh: refresh)
            usageError = nil
        } catch is CancellationError {
            // The tab went away mid-fetch: nothing failed.
        } catch {
            usageError = message(error, app: app, client: client)
        }
    }

    func loadPeriod(app: AppModel, _ period: UsagePeriod, force: Bool = false) async {
        guard force || overviews[period] == nil, let client = app.client else { return }
        do {
            let overview = try await client.overview(period: period)
            overviews[period] = overview
            overviewErrors[period] = nil
            if period == .today { app.publishSpend(overview.totals?.costUsd) }
        } catch is CancellationError {
        } catch {
            overviewErrors[period] = message(error, app: app, client: client)
        }
    }

    private func message(_ error: Error, app: AppModel, client: HubClient) -> String {
        guard let hubError = error as? HubError else { return error.localizedDescription }
        if hubError.isUnauthorized { app.unpair(from: client) }
        return hubError.message
    }

    // MARK: Derived

    /// A harness's name as the registry gives it, for overview rows that
    /// carry only the id.
    func harnessName(_ id: String) -> String {
        let rows = (usage?.harnesses ?? []) + (usage?.allMachines ?? []).flatMap(\.rows)
        if let row = rows.first(where: { $0.name == id }) { return row.title }
        return UsageFormat.harnessName(id: id, displayName: nil)
    }
}
