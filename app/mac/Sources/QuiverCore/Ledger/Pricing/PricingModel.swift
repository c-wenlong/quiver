import Foundation
import Observation

/// The Pricing page: every model the ledger saw in the period, grouped by
/// provider, with the rate its name resolves to and where that rate came
/// from. Saving a user price writes `config/pricing.json`, rebuilds the
/// catalog, and hands it to `apply`, which is the same path a refreshed
/// LiteLLM list takes into the ledger, so past usage reprices at once.
/// Which columns a provider card's table shows at a given content width.
/// Narrow cards drop the cache rate columns first, then the source
/// column (the row's hover help and a caption carry them instead).
public struct PricingColumns: Sendable, Equatable {
    public var cacheReadWrite: Bool
    public var source: Bool

    public static func visible(forWidth width: CGFloat) -> PricingColumns {
        PricingColumns(cacheReadWrite: width >= 640, source: width >= 520)
    }
}

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
        /// Every event with tokens carried a logged cost, so no price list
        /// applied. Empty events without one (Cursor's feed logs some for
        /// Grok Bot) cost nothing either way and don't count against it.
        public var harnessBilled: Bool
        /// The billers (as `PricingModel.biller(of:)` names them) whose
        /// logged charges price part of this row's usage: all of it when
        /// `harnessBilled`, otherwise the rest goes by `source`.
        public var billedBy: [String] = []
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
        /// The downloaded price under any user override, nil when the
        /// model has no LiteLLM or Quiver price at all.
        public var listPrice: ListPrice?
        /// Where the row's spend comes from, as the Source column shows it:
        /// a harness-billed row is billed by its harness (Cursor for Grok
        /// Bot) whatever price is saved, since no rate applies to it.
        public var spendSource: SpendSource {
            if harnessBilled { return .billed(by: billedBy) }
            if isFree { return .free }
            return switch source {
            case .yours: .yours
            case .swe: .swe
            case .liteLLM: .liteLLM
            case nil: .unpriced
            }
        }

        /// What deleting this row's saved price does: with a list price
        /// under it the override resets to the list price, without it the
        /// price goes away entirely.
        public var resetKind: ResetKind {
            guard let listPrice else { return .remove }
            return .resetToList(listPrice)
        }
    }

    /// Where a row's spend comes from.
    public enum SpendSource: Sendable, Equatable {
        /// Each request's logged charge, from these harness ids (billers,
        /// not runners: Grok Bot's charges come from Cursor).
        case billed(by: [String])
        case free
        case yours
        /// Quiver's bundled list, filling in or correcting LiteLLM's.
        case quiver
        /// LiteLLM's API price list.
        case liteLLM
        case unpriced
    }

    /// The harness whose charge a harness's logged costs are: Grok Bot
    /// signs in with the Cursor account and its charges arrive through
    /// Cursor's usage feed; every other harness bills its own usage.
    public nonisolated static func biller(of harness: String) -> String {
        harness == "grok-bot" ? "cursor" : harness
    }

    /// The price a model would have without the user's overrides.
    public struct ListPrice: Sendable, Equatable {
        public var rates: ModelRates
        public var source: PricingCatalog.Source
    }

    public enum ResetKind: Sendable, Equatable {
        case resetToList(ListPrice)
        case remove
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
    /// Harnesses with usage in the selected period, most tokens first:
    /// the filter menu's options after its "All harnesses" row.
    public private(set) var harnessOptions: [String] = []
    /// The picked harnesses: every list on the page shows only their
    /// usage, and an empty set shows all. A pick whose usage vanished (a
    /// period change) drops out on the next load. Not persisted.
    public var harnessFilter: Set<String> = []
    public private(set) var groups: [ProviderGroup] = []
    /// Models with unpriced tokens, largest first; the warning button's list.
    public private(set) var unpriced: [PricedModel] = []
    public private(set) var isLoading = false

    /// Model names seen in the period across every harness: the sheet's
    /// "Covers …" list stays unfiltered because a saved price applies to
    /// every harness that ran the model.
    private var periodModels: [String] = []
    /// Bumped on every `load`: a superseded load must not publish or
    /// touch the filter once a newer one has started.
    private var loadGeneration = 0
    /// Bumped on every `reprice`: only the newest catalog build applies.
    private var repriceGeneration = 0

    private let ledger: Ledger
    private var overrides: PricingOverrides
    /// Rebuilds the catalog with the overrides file as saved now.
    private let makeCatalog: @Sendable () throws -> PricingCatalog
    /// Puts the rebuilt catalog into the ledger (OverviewModel's path, so
    /// its cached summaries reprice too).
    private let apply: @Sendable (PricingCatalog) async -> Void
    private let now: () -> Date
    private let calendar: Calendar
    /// The period's per-model-per-harness usage; overridable in tests to
    /// hold a load mid-flight.
    private let slices: @Sendable (DateInterval) async -> [ModelUsage]

    public init(
        ledger: Ledger, overrides: PricingOverrides,
        makeCatalog: @escaping @Sendable () throws -> PricingCatalog,
        apply: @escaping @Sendable (PricingCatalog) async -> Void,
        now: @escaping () -> Date = Date.init, calendar: Calendar = .current,
        slices: (@Sendable (DateInterval) async -> [ModelUsage])? = nil
    ) {
        self.ledger = ledger
        self.overrides = overrides
        self.makeCatalog = makeCatalog
        self.apply = apply
        self.now = now
        self.calendar = calendar
        self.slices = slices ?? { range in
            await ledger.byModelHarness(in: range)
        }
    }

    /// Rebuild the rows for the selected period.
    public func load() async {
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        defer { if generation == loadGeneration { isLoading = false } }
        let range = period.range(now: now(), calendar: calendar)
        let all = await slices(range)
        guard generation == loadGeneration else { return }
        periodModels = Array(Set(all.map(\.model))).sorted()
        var byHarness: [String: Int] = [:]
        for slice in all { byHarness[slice.harness, default: 0] += slice.totals.totalTokens }
        harnessOptions = byHarness
            .sorted { ($0.value, $0.key) > ($1.value, $1.key) }
            .map(\.key)
        let live = harnessFilter.filter { byHarness[$0] != nil }
        if live != harnessFilter { harnessFilter = live }
        let slices = live.isEmpty ? all : all.filter { live.contains($0.harness) }
        let catalog = await ledger.catalog()
        guard generation == loadGeneration else { return }
        var merged: [String: PricedModel] = [:]
        var order: [String] = []
        // Per model: whether any slice carried a logged cost. A model whose
        // slices are all empty and unbilled is not harness-billed.
        var anyLogged: [String: Bool] = [:]
        for slice in slices {
            let billed = slice.harnessBilled || slice.totals.totalTokens == 0
            anyLogged[slice.model, default: false] = anyLogged[slice.model, default: false]
                || slice.harnessBilled
            let biller = slice.harnessBilled ? Self.biller(of: slice.harness) : nil
            // A logged "fast" dim tier and a `-fast` name suffix both
            // charge a multiplier the row names.
            var sliceTiers: [String] = []
            if let tier = slice.tier, !tier.isEmpty { sliceTiers.append(tier) }
            if var row = merged[slice.model] {
                row.totals.add(slice.totals)
                row.harnessBilled = row.harnessBilled && billed
                if let biller, !row.billedBy.contains(biller) { row.billedBy.append(biller) }
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
                let listPrice = catalog.listLookup(slice.model)
                    .map { ListPrice(rates: $0.rates, source: $0.source) }
                merged[slice.model] = PricedModel(
                    model: slice.model, totals: slice.totals, harnesses: [slice.harness],
                    harnessBilled: billed,
                    entry: entry, source: source, rates: rates,
                    tiers: tiers.sorted(), isFree: isFree,
                    unpricedTokens: slice.totals.unpricedTokens,
                    listPrice: listPrice)
                if let biller { merged[slice.model]?.billedBy = [biller] }
                order.append(slice.model)
            }
        }
        for (model, logged) in anyLogged where !logged { merged[model]?.harnessBilled = false }
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
    /// model in the period whose base name is `key`, unfiltered — a saved
    /// price applies to every harness.
    public func coveredBy(_ key: String) -> [String] {
        periodModels
            .filter { PricingCatalog.baseName(for: $0) == key }
            .sorted()
    }

    /// Persist `key`'s price, rebuild the catalog with it, and reprice.
    /// `replacing` is the entry being edited: a save under a changed key
    /// drops the old one in the same write.
    public func save(key: String, prices: PricingOverrides.Prices,
                     replacing: String? = nil) async throws {
        try overrides.set(key, prices: prices, replacing: replacing)
        try await reprice()
    }

    /// Delete every saved key that prices `model` and reprice back to
    /// downloaded sources — several keys can match one model (`gpt-4.1`
    /// and `gpt-4-1`), and removing just one would leave it user-priced.
    public func remove(matching model: String) async throws {
        let keys = await ledger.catalog().userKeys(matching: model)
        try overrides.remove(keys)
        try await reprice()
    }

    /// Rebuilds the catalog off the main actor (reading the price list
    /// and its history takes most of a second), hands it to `apply`, then
    /// reloads this page's rows. `apply` is expected to return once the
    /// ledger prices with the new catalog; slower redraws elsewhere (the
    /// Overview's figures) belong in the background, not on this path.
    /// Saves can overlap (Cancel closes the sheet while its write runs),
    /// so a build that a later save has superseded is dropped rather than
    /// applied over the newer catalog.
    private func reprice() async throws {
        repriceGeneration += 1
        let generation = repriceGeneration
        let makeCatalog = self.makeCatalog
        let catalog = try await Task.detached(priority: .userInitiated) { try makeCatalog() }.value
        guard generation == repriceGeneration else { return }
        await apply(catalog)
        await load()
    }
}
