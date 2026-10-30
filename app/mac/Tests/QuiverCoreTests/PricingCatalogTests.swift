import Foundation
import Testing
@testable import QuiverCore

/// Serves canned bytes instead of the network.
struct StubTransport: PricingTransport {
    var data: Data?
    var error: (any Error)?
    func fetch(_ url: URL) async throws -> Data {
        if let error { throw error }
        return data ?? Data()
    }
}

struct Offline: Error {}

@Suite struct PricingCatalogTests {
    let liteLLM = Data("""
        {
          "claude-opus-5-5": {"input_cost_per_token": 4e-06, "output_cost_per_token": 2e-05,
            "cache_read_input_token_cost": 2e-07, "cache_creation_input_token_cost": 5e-06,
            "cache_creation_input_token_cost_above_1hr": 8e-06, "litellm_provider": "anthropic"},
          "claude-sonnet-4-5": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05,
            "input_cost_per_token_above_200k_tokens": 6e-06, "output_cost_per_token_above_200k_tokens": 2.25e-05,
            "cache_read_input_token_cost": 3e-07, "litellm_provider": "anthropic"},
          "claude-sonnet-4-20250514": {"input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05, "litellm_provider": "anthropic"},
          "gpt-6.1-sol": {"input_cost_per_token": 2e-06, "output_cost_per_token": 1e-05,
            "cache_read_input_token_cost": 1e-07,
            "input_cost_per_token_above_272k_tokens": 4e-06, "output_cost_per_token_above_272k_tokens": 1.5e-05,
            "cache_read_input_token_cost_above_272k_tokens": 2e-07,
            "input_cost_per_token_priority": 4e-06, "output_cost_per_token_priority": 2e-05,
            "cache_read_input_token_cost_priority": 2e-07, "litellm_provider": "openai"},
          "gpt-5.6-luna": {"input_cost_per_token": 2e-07, "output_cost_per_token": 1.2e-06, "litellm_provider": "openai"},
          "o3": {"input_cost_per_token": 2e-06, "output_cost_per_token": 8e-06, "output_cost_per_reasoning_token": 9e-06, "litellm_provider": "openai"},
          "gemini/gemini-3-pro": {"input_cost_per_token": 2e-06, "output_cost_per_token": 1.2e-05, "litellm_provider": "gemini"},
          "sample_spec": {"input_cost_per_token": 0, "output_cost_per_token": 0},
          "dall-e-3": {"mode": "image_generation", "output_cost_per_image": 0.04}
        }
        """.utf8)

    func catalog() throws -> PricingCatalog {
        try PricingCatalog(liteLLM: liteLLM, supplement: PricingSupplement(
            aliases: ["gpt-reserve": "gpt-5.6-luna"],
            tierMultipliers: ["claude-opus-5-5": ["fast": 2]],
            defaultTierMultipliers: ["priority": 2]))
    }

    @Test func aDatedEntryTakesItsModelsTierMultiplier() throws {
        let data = Data("""
            {"claude-opus-4-6-20260205": {"input_cost_per_token": 5e-06, "output_cost_per_token": 2.5e-05, "litellm_provider": "anthropic"},
             "anthropic/claude-opus-4.6": {"input_cost_per_token": 5e-06, "output_cost_per_token": 2.5e-05, "litellm_provider": "anthropic"}}
            """.utf8)
        let catalog = try PricingCatalog(liteLLM: data, supplement: PricingSupplement(
            tierMultipliers: ["claude-opus-4-6": ["fast": 6]]))
        // The exact dated entry keeps its own base prices and gains the multiplier.
        let dated = try #require(catalog.rates(for: "claude-opus-4-6-20260205"))
        #expect(dated.input == 5e-06)
        #expect(dated.tierMultipliers["fast"] == 6)
        #expect(catalog.rates(for: "anthropic/claude-opus-4.6")?.tierMultipliers["fast"] == 6)
    }

    @Test func liteLLMEntriesBecomeRatesPerTokenKind() throws {
        let rates = try #require(try catalog().rates(for: "claude-opus-5-5"))
        #expect(rates.input == 4e-06)
        #expect(rates.output == 2e-05)
        #expect(rates.cacheRead == 2e-07)
        #expect(rates.cacheWrite == 5e-06)
        #expect(rates.cacheWrite1h == 8e-06)
        // No separate reasoning price: reasoning bills as output.
        #expect(rates.reasoning == 2e-05)
        #expect(try catalog().rates(for: "o3")?.reasoning == 9e-06)
    }

    @Test func costIsTheSumOfEachKindAtItsOwnRate() throws {
        let rates = try #require(try catalog().rates(for: "claude-opus-5-5"))
        // The fixture's msg_A2: 3 input, 21,000 cache read, 200 5m + 300 1h writes, 60 output.
        let cost = rates.cost(input: 3, output: 60, cacheRead: 21_000, cacheWrite: 500, cacheWrite1h: 300, reasoning: 0)
        let input: Double = 3 * 4e-06
        let output: Double = 60 * 2e-05
        let reads: Double = 21_000 * 2e-07
        let writes: Double = 200 * 5e-06 + 300 * 8e-06
        let expected = input + output + reads + writes
        #expect(abs(cost - expected) < 1e-12)
    }

    @Test func anthropicOneHourWritesDefaultToTwiceTheInputRate() throws {
        let rates = try #require(try catalog().rates(for: "claude-sonnet-4-5"))
        #expect(rates.cacheWrite1h == 6e-06)
        #expect(rates.cacheWrite == 3e-06)
    }

    @Test func namesAreMatchedThroughPrefixesSuffixesAndSeparators() throws {
        let catalog = try catalog()
        #expect(catalog.rates(for: "anthropic/claude-opus-5-5")?.input == 4e-06)
        #expect(catalog.rates(for: "us.anthropic.claude-opus-5-5-v1:0")?.input == 4e-06)
        #expect(catalog.rates(for: "claude-opus-5-5[1m]")?.input == 4e-06)
        #expect(catalog.rates(for: "claude-opus-5.5")?.input == 4e-06)
        #expect(catalog.rates(for: "claude-sonnet-4-5-20250929")?.input == 3e-06)
        #expect(catalog.rates(for: "gpt-6.1-sol-2026-09-30")?.input == 2e-06)
        #expect(catalog.rates(for: "gemini-3-pro")?.input == 2e-06)
        #expect(catalog.rates(for: "GPT-6.1-Sol")?.input == 2e-06)
        #expect(catalog.rates(for: "gpt-reserve")?.input == 2e-07)
        // Devin logs the reasoning effort as a suffix and dashes for dots.
        #expect(catalog.rates(for: "gpt-6-1-sol-high")?.input == 2e-06)
        #expect(catalog.rates(for: "claude-opus-5-5-xhigh")?.input == 4e-06)
        // A shorter version never borrows a longer one's price, or the reverse.
        #expect(catalog.rates(for: "claude-sonnet-4")?.input == 3e-06)
        #expect(catalog.rates(for: "claude-sonnet-4")?.longContext == nil)
        #expect(catalog.rates(for: "claude-sonnet-4-6") == nil)
        #expect(catalog.rates(for: "swe-2-max") == nil)
        #expect(catalog.rates(for: "sample_spec") == nil)
    }

    /// OpenCode rows are logged `provider/model`, with any slash of the
    /// model's own after the first one. Everything keyed by a model name
    /// (price lookup, the free check, a saved price's base name, the lab
    /// on the Pricing and Models pages, the Artificial Analysis match)
    /// reads only what follows the last slash.
    @Test func providerQualifiedNamesResolveLikeTheBareOnes() throws {
        let catalog = try catalog()
        for (qualified, bare) in [("openai/gpt-6.1-sol", "gpt-6.1-sol"),
                                  ("github-copilot/gpt-6.1-sol", "gpt-6.1-sol"),
                                  ("anthropic/claude-opus-5-5", "claude-opus-5-5")] {
            #expect(catalog.rates(for: qualified) == catalog.rates(for: bare), "\(qualified)")
            #expect(PricingCatalog.baseName(for: qualified) == PricingCatalog.baseName(for: bare))
            #expect(ModelBrands.forModel(qualified) == ModelBrands.forModel(bare))
            #expect(ModelMatcher.parse(qualified).base == ModelMatcher.parse(bare).base)
        }
        #expect(catalog.resolve(model: "xyz-co/unlisted-model") == nil)
        #expect(PricingCatalog.isFree("openrouter/nvidia/nemotron-3-ultra:free"))
        #expect(PricingCatalog.isFree("opencode/nemotron-3-ultra-free"))
        #expect(PricingCatalog.isFree("openrouter/openrouter/free"))
        #expect(!PricingCatalog.isFree("openai/gpt-6.1-sol"))
        #expect(ModelBrands.forModel("zai-coding-plan/glm-5.1").id == "zai")
        #expect(ModelBrands.forModel("featherless/zai-org/GLM-5.2").id == "zai")
        #expect(ModelBrands.forModel("xiaomi-token-plan-sgp/mimo-v2.5-pro").id == "xiaomi")
        #expect(PricingCatalog.baseName(for: "groq/openai/gpt-oss-120b") == "gpt-oss-120b")
    }

    @Test func thresholdsCoverTodaysEntriesAndEarlierPrices() throws {
        let catalog = try PricingCatalog(liteLLM: liteLLM)
        #expect(catalog.thresholds == [200_000, 272_000])
        // An earlier price of a key with no threshold today still counts.
        let earlier = CatalogDatabase.PriceSpan(from: 0, to: 1_000, entry: [
            "input_cost_per_token": 1e-06, "output_cost_per_token": 1e-05,
            "input_cost_per_token_above_128k_tokens": 2e-06, "litellm_provider": "openai"])
        let withHistory = catalog.withHistory(["gpt-5.6-luna": [earlier]])
        #expect(withHistory.thresholds == [128_000, 200_000, 272_000])
        #expect(withHistory.withHistory([:]).thresholds == [200_000, 272_000])
    }

    @Test func longContextAndPriorityRatesApplyPerRequest() throws {
        let rates = try #require(try catalog().rates(for: "gpt-6.1-sol"))
        #expect(rates.longContext?.threshold == 272_000)
        let long = rates.cost(input: 10_000, output: 200, cacheRead: 290_000, cacheWrite: 0, cacheWrite1h: 0,
                              reasoning: 0, crossesLongContext: true)
        #expect(abs(long - (10_000 * 4e-06 + 200 * 1.5e-05 + 290_000 * 2e-07)) < 1e-12)
        let priority = rates.cost(input: 1_000, output: 100, cacheRead: 0, cacheWrite: 0, cacheWrite1h: 0,
                                  reasoning: 0, tier: "priority")
        #expect(abs(priority - (1_000 * 4e-06 + 100 * 2e-05)) < 1e-12)
    }

    @Test func tiersWithoutExplicitRatesUseTheSupplementMultiplier() throws {
        let catalog = try catalog()
        let opus = try #require(catalog.rates(for: "claude-opus-5-5"))
        let fast = opus.cost(input: 1_000, output: 0, cacheRead: 0, cacheWrite: 0, cacheWrite1h: 0, reasoning: 0, tier: "fast")
        #expect(abs(fast - 1_000 * 4e-06 * 2) < 1e-12)
        let luna = try #require(catalog.rates(for: "gpt-5.6-luna"))
        let priority = luna.cost(input: 1_000, output: 0, cacheRead: 0, cacheWrite: 0, cacheWrite1h: 0, reasoning: 0, tier: "priority")
        #expect(abs(priority - 1_000 * 2e-07 * 2) < 1e-12)
    }

    @Test func theBundledSnapshotPricesTheModelsInUse() throws {
        let catalog = try PricingCatalog.bundled()
        for model in ["claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5",
                      "claude-haiku-4-5-20251001", "gpt-6-astra", "gpt-6.1-sol", "gpt-5.6-sol", "gpt-5.4-mini"] {
            #expect(catalog.rates(for: model) != nil, "\(model) is unpriced")
        }
        #expect(catalog.rates(for: "claude-opus-5")?.tierMultipliers["fast"] == 2)
    }

    @Test func splitTierSuffixPullsFastOutOfTheModelName() throws {
        let split = PricingCatalog.splitTierSuffix(model: "claude-opus-5-5-high-fast", tier: "")
        #expect(split.model == "claude-opus-5-5-high")
        #expect(split.tier == "fast")
        // A logged tier wins over the name.
        let kept = PricingCatalog.splitTierSuffix(model: "claude-opus-5-5-high-fast", tier: "priority")
        #expect(kept.model == "claude-opus-5-5-high-fast")
        #expect(kept.tier == "priority")
        let plain = PricingCatalog.splitTierSuffix(model: "gpt-6-sol", tier: "")
        #expect(plain.model == "gpt-6-sol")
        #expect(plain.tier == "")
    }

    @Test func freeModelNamesAreRecognised() throws {
        for model in ["openrouter/free", "nvidia/nemotron-3-ultra-550b-a55b:free",
                      "deepseek-v4-flash-free", "mimo-v2.5-free",
                      "qwen3.6-plus-free", "nemotron-3-ultra-free"] {
            #expect(PricingCatalog.isFree(model), "\(model) should be free")
        }
        for model in ["gpt-6-sol", "freebuff-model", "claude-opus-5-5"] {
            #expect(!PricingCatalog.isFree(model), "\(model) is not free")
        }
    }

    @Test func refreshFetchesThroughTheTransportAndStoresTheResult() async throws {
        let temp = try TempDir()
        let database = CatalogDatabase(url: temp.path("catalog.sqlite"))
        let now = utc("2026-10-11T00:00:00Z")
        let store = PricingStore(database: database, transport: StubTransport(data: liteLLM), minimumModels: 1)
        let refreshed = try #require(await store.refreshIfStale(now: now))
        #expect(refreshed.rates(for: "gpt-6.1-sol")?.input == 2e-06)
        let fetch = try #require(database.lastSuccess(source: CatalogDatabase.liteLLMSource))
        #expect(fetch.fetchedAt == now)
        #expect(fetch.rowCount == 7)
        #expect(fetch.contentSHA?.count == 64)

        // Within a day the stored copy is used and the network is not asked.
        let offline = PricingStore(database: database, transport: StubTransport(error: Offline()), minimumModels: 1)
        #expect(await offline.refreshIfStale(now: now.addingTimeInterval(3_600)) == nil)
        #expect(try offline.load().rates(for: "o3")?.reasoning == 9e-06)
        #expect(database.fetches().count == 1)
    }

    @Test func aFailedOrGarbledRefreshKeepsWhatWasThere() async throws {
        let temp = try TempDir()
        let database = CatalogDatabase(url: temp.path("catalog.sqlite"))
        let offline = PricingStore(database: database, transport: StubTransport(error: Offline()), minimumModels: 1)
        #expect(await offline.refreshIfStale(now: Date()) == nil)
        let garbled = PricingStore(database: database, transport: StubTransport(data: Data("<html>".utf8)),
                                   minimumModels: 1)
        #expect(await garbled.refreshIfStale(now: Date()) == nil)
        #expect(database.currentPriceEntries() == nil)
        // Only the latest failure since the last success is kept.
        let fetches = database.fetches(source: CatalogDatabase.liteLLMSource)
        #expect(fetches.count == 1)
        #expect(fetches.first?.error == "Unusable response")
        // With no downloaded list, load falls back to the bundled snapshot.
        #expect(try offline.load().rates(for: "claude-opus-5-5") != nil)
    }

    @Test func anUnchangedListRecordsTheFetchButReportsNothingNew() async throws {
        let temp = try TempDir()
        let database = CatalogDatabase(url: temp.path("catalog.sqlite"))
        let store = PricingStore(database: database, transport: StubTransport(data: liteLLM), minimumModels: 1)
        let day = utc("2026-10-11T00:00:00Z")
        #expect(await store.refreshIfStale(now: day) != nil)
        #expect(await store.refreshIfStale(now: day.addingTimeInterval(2 * 86_400)) == nil)
        #expect(database.fetches(source: CatalogDatabase.liteLLMSource).count == 2)
        #expect(database.prices(key: "o3").count == 1)
    }
}

@Suite struct LedgerPricingTests {
    @Test func totalsPriceEachEventAndReportUnknownModelsAsUnpriced() async throws {
        let temp = try TempDir()
        let home = try temp.copyFixture("claude-home")
        let pricing = try PricingCatalog(liteLLM: Data("""
            {"claude-opus-5-5": {"input_cost_per_token": 4e-06, "output_cost_per_token": 2e-05,
              "cache_read_input_token_cost": 2e-07, "cache_creation_input_token_cost": 5e-06,
              "cache_creation_input_token_cost_above_1hr": 8e-06, "litellm_provider": "anthropic"}}
            """.utf8))
        let ledger = try Ledger(directory: temp.path("cache"), pricing: pricing)
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))

        let sessionB = DateInterval(start: utc("2026-10-04T00:00:00Z"), end: utc("2026-10-05T00:00:00Z"))
        let totals = await ledger.totals(in: sessionB)
        // B1 on opus 5.5 is priced; its advisor call on opus 5 is not in this catalog.
        let b1 = 4 * 4e-06 + 22_000 * 2e-07 + 90 * 2e-05
        #expect(abs(totals.costUSD - b1) < 1e-9)
        #expect(totals.unpricedTokens == 340)
        #expect(totals.unpricedModels == ["claude-opus-5"])

        // The legacy line's logged cost wins over any catalog.
        let september = DateInterval(start: utc("2026-09-01T00:00:00Z"), end: utc("2026-10-01T00:00:00Z"))
        #expect(await ledger.totals(in: september).costUSD == 0.25)
        #expect(await ledger.totals(in: september).unpricedTokens == 0)
    }

    /// A ledger over one hand-written Claude home.
    private func ledger(with lines: String) async throws -> (Ledger, TempDir) {
        let temp = try TempDir()
        let home = temp.path("claude")
        let file = home.appendingPathComponent("projects/-p/s1.jsonl")
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(lines.utf8).write(to: file)
        let pricing = try PricingCatalog(liteLLM: Data("""
            {"claude-opus-5-5": {"input_cost_per_token": 4e-06, "output_cost_per_token": 2e-05,
              "litellm_provider": "anthropic"},
             "grok-code-fast": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06,
              "litellm_provider": "xai"}}
            """.utf8), supplement: PricingSupplement(
            tierMultipliers: ["claude-opus-5-5": ["fast": 2]]))
        let ledger = try Ledger(directory: temp.path("cache"), pricing: pricing)
        try await ledger.scan(roots: LedgerRoots(claudeHomes: [home]))
        return (ledger, temp)
    }

    @Test func aFastModelSuffixPricesAtTheFastMultiplier() async throws {
        // Same tokens on the base model and on Devin's `-fast` name.
        let (ledger, keepAlive) = try await ledger(with:
            claudeLine(message: "m1", request: "r1", output: 100, input: 1_000, model: "claude-opus-5-5")
            + claudeLine(message: "m2", request: "r2", output: 100, input: 1_000, model: "claude-opus-5-5-high-fast"))
        defer { withExtendedLifetime(keepAlive) {} }
        let expected = 2 * (1_000 * 4e-06 + 100 * 2e-05) + (1_000 * 4e-06 + 100 * 2e-05)
        let totals = await ledger.totals(in: allTime)
        #expect(abs(totals.costUSD - expected) < 1e-9)
        #expect(totals.unpricedTokens == 0)
    }

    @Test func aCatalogNameEndingInFastPricesAtItsOwnRates() async throws {
        // `grok-code-fast` is a real catalog entry: the literal name must be
        // tried before the `-fast` suffix is split, or it goes unpriced.
        let (ledger, keepAlive) = try await ledger(with:
            claudeLine(message: "m1", output: 100, input: 1_000, model: "grok-code-fast"))
        defer { withExtendedLifetime(keepAlive) {} }
        let expected = 1_000 * 1e-06 + 100 * 2e-06
        let totals = await ledger.totals(in: allTime)
        #expect(abs(totals.costUSD - expected) < 1e-9)
        #expect(totals.unpricedTokens == 0)
    }

    @Test func aFreeModelCostsZeroAndIsNotUnpriced() async throws {
        let (ledger, keepAlive) = try await ledger(with:
            claudeLine(message: "m1", output: 100, input: 1_000, model: "deepseek-v4-flash-free"))
        defer { withExtendedLifetime(keepAlive) {} }
        let totals = await ledger.totals(in: allTime)
        #expect(totals.costUSD == 0)
        #expect(totals.unpricedTokens == 0)
    }
}
