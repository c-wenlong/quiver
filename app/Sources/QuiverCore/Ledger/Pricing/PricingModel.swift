import Foundation
import Observation

/// The Pricing page: every model the ledger saw in the period, grouped by
/// provider, with the rate its name resolves to and where that rate came
/// from. Saving a user price writes `config/pricing.json`, rebuilds the
/// catalog, and hands it to `apply`, which is the same path a refreshed
/// LiteLLM list takes into the ledger, so past usage reprices at once.
@MainActor
@Observable
public final class PricingModel {
    /// One model's priced usage in the period.
    public struct PricedModel: Sendable, Equatable, Identifiable {
        public var id: String { model }
        public var model: String
        public var totals: UsageTotals
        /// The harnesses that ran it, most tokens first.
        public var harnesses: [String]
        /// Every event carried a logged cost, so no price list applied.
        public var harnessBilled: Bool
        /// What the model resolves to in the catalog, nil when unpriced.
        public var entry: String?
        public var source: PricingCatalog.Source?
        public var rates: ModelRates?
        /// The charged tiers seen (`claude-opus-5-5-high-fast` prices as
        /// the base model at the "fast" multiplier, and a dim-level "fast"
        /// arrives through `ModelUsage.tier`), sorted; empty for the base
        /// tier.
        public var tiers: [String]
        /// A free model: priced at zero with no catalog entry.
        public var isFree: Bool
        /// Tokens with neither a logged cost nor a resolved rate.
        public var unpricedTokens: Int
    }

    /// A provider's card: its models, largest first.
    public struct ProviderGroup: Sendable, Equatable, Identifiable {
        public var id: String
        public var name: String
        public var icon: String?
        public var models: [PricedModel]
        public var tokens: Int
    }

    public var period: OverviewPeriod = .month
    public private(set) var groups: [ProviderGroup] = []
    /// Models with unpriced tokens, largest first; the warning button's list.
    public private(set) var unpriced: [PricedModel] = []
    public private(set) var isLoading = false

    private let ledger: Ledger
    private var overrides: PricingOverrides
    /// Rebuilds the catalog with the overrides file as saved now.
    private let makeCatalog: @Sendable () throws -> PricingCatalog
    /// Puts the rebuilt catalog into the ledger (OverviewModel's path, so
    /// its cached summaries reprice too).
    private let apply: @Sendable (PricingCatalog) async -> Void
    private let now: () -> Date
    private let calendar: Calendar

    public init(
        ledger: Ledger, overrides: PricingOverrides,
        makeCatalog: @escaping @Sendable () throws -> PricingCatalog,
        apply: @escaping @Sendable (PricingCatalog) async -> Void,
        now: @escaping () -> Date = Date.init, calendar: Calendar = .current
    ) {
        self.ledger = ledger
        self.overrides = overrides
        self.makeCatalog = makeCatalog
        self.apply = apply
        self.now = now
        self.calendar = calendar
    }

    /// Rebuild the rows for the selected period.
    public func load() async {
        isLoading = true
        defer { isLoading = false }
        let range = period.range(now: now(), calendar: calendar)
        let slices = await ledger.byModelHarness(in: range)
        let catalog = await ledger.catalog()
        var merged: [String: PricedModel] = [:]
        var order: [String] = []
        for slice in slices {
            // A logged "fast" dim tier and a `-fast` name suffix both
            // charge a multiplier the row names.
            var sliceTiers: [String] = []
            if let tier = slice.tier, !tier.isEmpty { sliceTiers.append(tier) }
            if var row = merged[slice.model] {
                row.totals.add(slice.totals)
                row.harnessBilled = row.harnessBilled && slice.harnessBilled
                row.unpricedTokens += slice.totals.unpricedTokens
                if !row.harnesses.contains(slice.harness) { row.harnesses.append(slice.harness) }
                for tier in sliceTiers where !row.tiers.contains(tier) {
                    row.tiers.append(tier)
                }
                merged[slice.model] = row
            } else {
                var entry: String?, source: PricingCatalog.Source?, rates: ModelRates?
                var isFree = false
                var tiers = sliceTiers
                switch catalog.resolve(model: slice.model, tier: slice.tier ?? "") {
                case .priced(let found, let key, let whereFrom, let resolvedTier):
                    entry = key; source = whereFrom; rates = found
                    if !resolvedTier.isEmpty, !tiers.contains(resolvedTier) {
                        tiers.append(resolvedTier)
                    }
                case .free:
                    isFree = true
                case nil:
                    break
                }
                merged[slice.model] = PricedModel(
                    model: slice.model, totals: slice.totals, harnesses: [slice.harness],
                    harnessBilled: slice.harnessBilled,
                    entry: entry, source: source, rates: rates,
                    tiers: tiers.sorted(), isFree: isFree,
                    unpricedTokens: slice.totals.unpricedTokens)
                order.append(slice.model)
            }
        }
        let rows = merged.values.sorted { ($0.totals.totalTokens, $0.model) > ($1.totals.totalTokens, $1.model) }
        unpriced = rows.filter { $0.unpricedTokens > 0 }
            .sorted { ($0.unpricedTokens, $0.model) > ($1.unpricedTokens, $1.model) }
        var grouped: [String: ProviderGroup] = [:]
        for row in rows {
            let provider = ModelProvider.of(row.model)
            grouped[provider.id, default: ProviderGroup(
                id: provider.id, name: provider.name, icon: provider.icon,
                models: [], tokens: 0)].models.append(row)
            grouped[provider.id]?.tokens += row.totals.totalTokens
        }
        groups = grouped.values
            .map { group in
                var group = group
                group.models.sort { ($0.totals.totalTokens, $0.model) > ($1.totals.totalTokens, $1.model) }
                return group
            }
            .sorted { ($0.tokens, $0.id) > ($1.tokens, $1.id) }
    }

    /// The logged model names a price saved under `key` would cover: every
    /// model in the period whose base name is `key`.
    public func coveredBy(_ key: String) -> [String] {
        groups.flatMap(\.models).map(\.model)
            .filter { PricingCatalog.baseName(for: $0) == key }
            .sorted()
    }

    /// Persist `key`'s price, rebuild the catalog with it, and reprice.
    public func save(key: String, prices: PricingOverrides.Prices) async throws {
        try overrides.set(key, prices: prices)
        try await reprice()
    }

    /// Delete `key`'s saved price and reprice back to downloaded sources.
    public func remove(key: String) async throws {
        try overrides.remove(key)
        try await reprice()
    }

    private func reprice() async throws {
        let catalog = try makeCatalog()
        await apply(catalog)
        await load()
    }
}
