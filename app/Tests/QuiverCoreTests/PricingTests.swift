import Foundation
import Testing
@testable import QuiverCore

private func event(_ model: String, tokens: Int = 100, loggedCost: Double? = nil,
                   harness: String = "claude") -> UsageEvent {
    UsageEvent(harness: harness, accountHome: "", sessionId: "s", model: model,
               timestamp: Date(), inputTokens: tokens,
               costUSD: loggedCost)
}

/// `catalogJSON` plus the given supplement prices and overrides, merged the
/// way `PricingStore.load` merges them.
private func catalog(supplement prices: [String: [String: Any]] = [:],
                     overrides: [String: [String: Any]] = [:]) throws -> PricingCatalog {
    var supplement = PricingSupplement()
    supplement.prices = prices
    return try PricingCatalog(
        liteLLM: Data("""
            {"codex-mini-latest": {"input_cost_per_token": 0.0000015, \
            "output_cost_per_token": 0.000006}, \
            "gpt-4o": {"input_cost_per_token": 0.0000025, \
            "output_cost_per_token": 0.00001}}
            """.utf8),
        supplement: supplement, overrides: overrides)
}

@Suite("Pricing overrides") struct PricingOverridesTests {
    @Test func `an override under the base name prices the effort variants`() throws {
        var overrides = PricingOverrides(
            url: FileManager.default.temporaryDirectory
                .appending(path: "pricing-\(UUID().uuidString).json"))
        try overrides.set("swe-2", prices: .init(input: 2, output: 4))
        let catalog = try catalog(overrides: overrides.entries)
        for model in ["swe-2", "swe-2-medium", "swe-2-high", "swe-2-max"] {
            let rates = try #require(catalog.rates(for: model))
            #expect(rates.input == 0.000002 && rates.output == 0.000004)
        }
    }

    @Test func `an override beats LiteLLM and lookup reports the source`() throws {
        var overrides = PricingOverrides(
            url: FileManager.default.temporaryDirectory
                .appending(path: "pricing-\(UUID().uuidString).json"))
        try overrides.set("codex-mini-latest", prices: .init(input: 1, output: 1))
        let catalog = try catalog(
            supplement: ["provider/my-model": ["input_cost_per_token": 9e-6,
                                              "output_cost_per_token": 9e-6]],
            overrides: overrides.entries)

        #expect(catalog.lookup("codex-mini-latest")?.source == .yours)
        #expect(catalog.lookup("codex-mini-latest")?.rates.input == 0.000001)
        #expect(catalog.lookup("provider/my-model")?.source == .quiver)
        let other = try #require(catalog.lookup("gpt-4o"))
        #expect(other.source == .liteLLM && other.entry == "gpt-4o")
        #expect(catalog.lookup("nonexistent-model") == nil)
    }

    @Test func `an override wins under every equivalent name`() throws {
        var overrides = PricingOverrides(
            url: FileManager.default.temporaryDirectory
                .appending(path: "pricing-\(UUID().uuidString).json"))
        try overrides.set("gpt-4-1", prices: .init(input: 1, output: 2))
        try overrides.set("claude-opus-5-5", prices: .init(input: 9, output: 9))
        let catalog = try PricingCatalog(
            liteLLM: Data("""
                {"gpt-4.1": {"input_cost_per_token": 0.000002, \
                "output_cost_per_token": 0.000008}, \
                "anthropic/claude-opus-5-5-20260101": {\
                "input_cost_per_token": 0.000015, "output_cost_per_token": 0.000075}}
                """.utf8),
            overrides: overrides.entries)
        // The published literal key, the user key's form, and a dated
        // published entry all lose to the saved price.
        for model in ["gpt-4.1", "gpt-4-1"] {
            let hit = try #require(catalog.lookup(model))
            #expect(hit.source == .yours && hit.rates.input == 0.000001)
        }
        let dated = try #require(catalog.lookup("claude-opus-5-5-20260101"))
        #expect(dated.source == .yours && dated.rates.input == 0.000009)
        let prefixed = try #require(catalog.lookup("anthropic/claude-opus-5-5-20260101"))
        #expect(prefixed.source == .yours)
    }

    @Test func `a failed write leaves the in-memory prices alone`() throws {
        let temp = try TempDir()
        // A regular file where the config directory would go blocks the
        // atomic write outright.
        try Data().write(to: temp.path("blocked"))
        var blocked = PricingOverrides(url: temp.path("blocked/pricing.json"))
        #expect(throws: (any Error).self) {
            try blocked.set("swe-2", prices: .init(input: 1, output: 1))
        }
        #expect(blocked.models.isEmpty)

        // The same for remove: a read-only directory fails the write, the
        // price stays saved, and a retry once writable lands.
        let url = temp.path("config/pricing.json")
        var overrides = PricingOverrides(url: url)
        try overrides.set("swe-2", prices: .init(input: 1, output: 1))
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o555], ofItemAtPath: temp.path("config").path)
        defer {
            try? FileManager.default.setAttributes(
                [.posixPermissions: 0o755], ofItemAtPath: temp.path("config").path)
        }
        #expect(throws: (any Error).self) { try overrides.remove("swe-2") }
        #expect(overrides.models["swe-2"] != nil)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755], ofItemAtPath: temp.path("config").path)
        try overrides.remove("swe-2")
        #expect(overrides.models["swe-2"] == nil)
    }

    @Test func `save then load round-trips, missing file is empty`() throws {
        let url = FileManager.default.temporaryDirectory
            .appending(path: "pricing-\(UUID().uuidString).json")
        let empty = PricingOverrides(url: url)
        #expect(empty.models["swe-2"] == nil)
        var overrides = PricingOverrides(url: url)
        try overrides.set("swe-2", prices: .init(input: 1.25, output: 5, cacheRead: 0.1, cacheWrite: 0.5))
        let again = PricingOverrides(url: url)
        let prices = try #require(again.models["swe-2"])
        #expect(prices.input == 1.25 && prices.output == 5 && prices.cacheRead == 0.1
                && prices.cacheWrite == 0.5)
        try? FileManager.default.removeItem(at: url)
    }
}

@Suite("Catalog lookup helpers") struct PricingLookupTests {
    @Test func `baseName strips provider prefixes, dates and effort suffixes`() {
        #expect(PricingCatalog.baseName(for: "swe-2-medium") == "swe-2")
        #expect(PricingCatalog.baseName(for: "anthropic/claude-opus-5-5-20260101") == "claude-opus-5-5")
        #expect(PricingCatalog.baseName(for: "gpt-5") == "gpt-5")
    }

    @Test func `resolve names the entry, tier and free models`() throws {
        var supplement = PricingSupplement()
        supplement.defaultTierMultipliers = ["fast": 2]
        let catalog = try PricingCatalog(
            liteLLM: Data("""
                {"claude-opus-5-5": {"input_cost_per_token": 0.000015, \
                "output_cost_per_token": 0.000075}}
                """.utf8),
            supplement: supplement)
        // A `-fast` suffix resolves to the base entry with tier "fast".
        guard case .priced(let rates, let entry, let source, let tier)? =
                catalog.resolve(model: "claude-opus-5-5-high-fast") else {
            Issue.record("expected .priced"); return
        }
        #expect(entry == "claude-opus-5-5" && source == .liteLLM && tier == "fast")
        #expect(rates.tierMultipliers["fast"] == 2)
        // A logged tier arrives as-is; a free model and a miss round out.
        guard case .priced(_, _, _, "priority")? =
                catalog.resolve(model: "claude-opus-5-5", tier: "priority") else {
            Issue.record("expected .priced"); return
        }
        #expect(catalog.resolve(model: "openrouter/free") == .free)
        #expect(catalog.resolve(model: "never-heard-of-it") == nil)
    }

    @Test func `providers come from name prefixes`() {
        let cases: [(String, String)] = [
            ("claude-opus-5-5", "anthropic"), ("gpt-5", "openai"), ("o3", "openai"),
            ("codex-mini", "openai"), ("gemini-2.5-pro", "google"), ("gemma-3", "google"),
            ("grok-4", "xai"), ("swe-2-max", "cognition"), ("compactor", "cognition"),
            ("glm-5.2", "zai"), ("deepseek-v3", "deepseek"), ("kimi-k2", "moonshot"),
            ("moonshot-v1", "moonshot"), ("minimax-m2", "minimax"), ("qwen3-coder", "qwen"),
            ("mistral-large", "mistral"), ("devstral-small", "mistral"),
            ("composer-1", "cursor"), ("cursor-fast", "cursor"), ("something-else", "other"),
        ]
        for (model, provider) in cases {
            #expect(ModelProvider.of(model).id == provider)
        }
        // grok-bot matches before grok.
        let bot = ModelProvider.of("grok-bot-default")
        #expect(bot.id == "grok-bot" && bot.icon == "grok")
        #expect(ModelProvider.of("anthropic/claude-sonnet-4-6").id == "anthropic")
        #expect(ModelProvider.of("something-else").icon == nil)
    }
}

@MainActor
@Suite("PricingModel") struct PricingModelTests {
    private func makeModel(_ records: [UsageEvent],
                           overrides: PricingOverrides? = nil,
                           catalog: PricingCatalog) throws -> (Ledger, PricingModel, TempDir) {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        for (i, event) in records.enumerated() {
            try store.upsert(LedgerRecord(key: "t:\(i)", event: event))
        }
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let model = PricingModel(
            ledger: ledger,
            overrides: overrides ?? PricingOverrides(
                url: temp.url.appending(path: "config/pricing.json")),
            makeCatalog: { catalog }, apply: { _ in })
        return (ledger, model, temp)
    }

    private var priced: PricingCatalog {
        try! catalog(supplement: [
            "claude-sonnet-4-6": ["input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6],
            "gemini-2.5-pro": ["input_cost_per_token": 1.25e-6, "output_cost_per_token": 10e-6],
        ])
    }


    @Test func `groups by provider sorted by tokens, models sorted inside`() async throws {
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 50),
            event("gemini-2.5-pro", tokens: 300),
            event("gpt-5", tokens: 200),
            event("claude-opus-5-5", tokens: 100),
        ], catalog: priced)
        await model.load()
        #expect(model.groups.map { $0.id } == ["google", "openai", "anthropic"])
        #expect(model.groups[2].models.map(\.model) == ["claude-opus-5-5", "claude-sonnet-4-6"])
        #expect(model.groups[1].tokens == 200)
        _ = temp
    }

    @Test func `the unpriced list and the covered-by list`() async throws {
        let (ledger, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 50),
            event("swe-2-medium", tokens: 40),
            event("swe-2-max", tokens: 200),
        ], catalog: priced)
        await ledger.setPricing(priced)
        await model.load()
        #expect(model.unpriced.map(\.model) == ["swe-2-max", "swe-2-medium"])
        #expect(model.groups.first { $0.id == "cognition" }?.models.first?.source == nil)
        let sonnet = model.groups.flatMap(\.models).first { $0.model == "claude-sonnet-4-6" }
        #expect(sonnet?.source == .quiver)
        #expect(model.coveredBy("swe-2") == ["swe-2-max", "swe-2-medium"])
        _ = temp
    }

    @Test func `a harness-billed model shows no rates`() async throws {
        let (_, model, temp) = try makeModel([event("billed-model", tokens: 90, loggedCost: 1.2)],
                                          catalog: priced)
        await model.load()
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.harnessBilled && row.rates == nil && row.source == nil && row.unpricedTokens == 0)
        _ = temp
    }

    @Test func `saving an override reprices the ledger`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("swe-2-medium", tokens: 1_000_000)))
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let model = PricingModel(
            ledger: ledger,
            overrides: PricingOverrides(url: temp.url.appending(path: "config/pricing.json")),
            makeCatalog: {
                var supplement = PricingSupplement()
                supplement.prices = [:]
                // Re-read the file the save just wrote, like PricingStore.load.
                let overrides = PricingOverrides(
                    url: temp.url.appending(path: "config/pricing.json"))
                return try PricingCatalog(
                    liteLLM: Data("{}".utf8), supplement: supplement,
                    overrides: overrides.entries)
            },
            apply: { catalog in await ledger.setPricing(catalog) })
        var totals = await ledger.totals(in: allTime)
        #expect(totals.unpricedTokens == 1_000_000)
        #expect(totals.costUSD == 0)
        try await model.save(key: "swe-2", prices: .init(input: 3, output: 6))
        totals = await ledger.totals(in: allTime)
        #expect(totals.unpricedTokens == 0)
        #expect(totals.costUSD == 3)  // 1M input tokens at $3/1M.
        await model.load()
        #expect(model.unpriced.isEmpty)
        #expect(model.groups.flatMap(\.models).first?.source == .yours)
    }

    @Test func `saving under a changed key replaces the old one in one write`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("swe-2-max", tokens: 1_000)))
        let ledger = try Ledger(cacheDirectory: temp.path("cache"))
        let file = temp.url.appending(path: "config/pricing.json")
        let model = PricingModel(
            ledger: ledger, overrides: PricingOverrides(url: file),
            makeCatalog: { try PricingCatalog(
                liteLLM: Data("{}".utf8),
                overrides: PricingOverrides(url: file).entries) },
            apply: { catalog in await ledger.setPricing(catalog) })
        // Edit swe-2's saved price to live under swe-2-medium instead:
        // the old key leaves in the same write.
        try await model.save(key: "swe-2", prices: .init(input: 1, output: 1))
        try await model.save(key: "swe-2-medium", prices: .init(input: 2, output: 2),
                             replacing: "swe-2")
        let saved = PricingOverrides(url: file)
        #expect(saved.models["swe-2"] == nil)
        #expect(saved.models["swe-2-medium"]?.input == 2)
        // Remove always takes the original key, whatever the field said.
        try await model.remove(key: "swe-2-medium")
        #expect(PricingOverrides(url: file).models.isEmpty)
        _ = temp
    }

    @Test func `a fast-suffixed model and a free model are both priced`() async throws {
        var supplement = PricingSupplement()
        supplement.defaultTierMultipliers = ["fast": 2]
        let catalog = try PricingCatalog(
            liteLLM: Data("""
                {"claude-opus-5-5": {"input_cost_per_token": 0.000015, \
                "output_cost_per_token": 0.000075}}
                """.utf8),
            supplement: supplement)
        let (ledger, model, temp) = try makeModel([
            event("claude-opus-5-5-high-fast", tokens: 100),
            event("openrouter/free", tokens: 50),
        ], catalog: catalog)
        await ledger.setPricing(catalog)
        await model.load()
        #expect(model.unpriced.isEmpty)
        let fast = try #require(model.groups.flatMap(\.models)
            .first { $0.model == "claude-opus-5-5-high-fast" })
        #expect(fast.source == .liteLLM && fast.tiers == ["fast"]
                && fast.entry == "claude-opus-5-5" && !fast.isFree)
        let free = try #require(model.groups.flatMap(\.models)
            .first { $0.model == "openrouter/free" })
        #expect(free.isFree && free.source == nil && free.rates == nil)
        // The ledger agrees: the fast event costs the 2x fast rate and the
        // free one costs nothing, so neither reports unpriced tokens.
        let totals = await ledger.totals(in: allTime)
        #expect(totals.unpricedTokens == 0)
        #expect(totals.costUSD == 0.003)  // 100 input @ $15/1M x2
        _ = temp
    }

    @Test func `a logged dim tier shows the multiplier and prices it`() async throws {
        var supplement = PricingSupplement()
        supplement.defaultTierMultipliers = ["fast": 2]
        let catalog = try PricingCatalog(
            liteLLM: Data("""
                {"claude-opus-5-5": {"input_cost_per_token": 0.000015, \
                "output_cost_per_token": 0.000075}}
                """.utf8),
            supplement: supplement)
        // Claude fast mode: the model name stays the same and the tier
        // rides on the dim's serviceTier.
        let (ledger, model, temp) = try makeModel([
            UsageEvent(harness: "claude", accountHome: "", sessionId: "s",
                       model: "claude-opus-5-5", timestamp: Date(),
                       inputTokens: 100, serviceTier: "fast"),
        ], catalog: catalog)
        await ledger.setPricing(catalog)
        await model.load()
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.tiers == ["fast"])
        #expect(row.rates?.tierMultipliers["fast"] == 2)
        // The ledger charges the multiplier, so the page and totals agree.
        let totals = await ledger.totals(in: allTime)
        #expect(totals.costUSD == 0.003)  // 100 input @ $15/1M x2
        _ = temp
    }
}
