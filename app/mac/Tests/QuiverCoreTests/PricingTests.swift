import Foundation
import os
import Testing
@testable import QuiverCore

private func event(_ model: String, tokens: Int = 100, loggedCost: Double? = nil,
                   harness: String = "claude", at: Date = Date()) -> UsageEvent {
    UsageEvent(harness: harness, accountHome: "", sessionId: "s", model: model,
               timestamp: at, inputTokens: tokens,
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
        let temp = try TempDir()
        var overrides = PricingOverrides(url: temp.path("pricing.json"))
        try overrides.set("swe-2", prices: .init(input: 2, output: 4))
        let catalog = try catalog(overrides: overrides.entries)
        for model in ["swe-2", "swe-2-medium", "swe-2-high", "swe-2-max"] {
            let rates = try #require(catalog.rates(for: model))
            #expect(rates.input == 0.000002 && rates.output == 0.000004)
        }
    }

    @Test func `an override beats LiteLLM and lookup reports the source`() throws {
        let temp = try TempDir()
        var overrides = PricingOverrides(url: temp.path("pricing.json"))
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
        let temp = try TempDir()
        var overrides = PricingOverrides(url: temp.path("pricing.json"))
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

    @Test func `listLookup answers the price the model would have without overrides`() throws {
        let temp = try TempDir()
        var overrides = PricingOverrides(url: temp.path("pricing.json"))
        try overrides.set("codex-mini-latest", prices: .init(input: 1, output: 1))
        let catalog = try catalog(overrides: overrides.entries)
        // The normal lookup sees the saved price…
        #expect(catalog.lookup("codex-mini-latest")?.rates.input == 0.000001)
        // …but the list lookup still finds the LiteLLM price it shadows.
        let list = try #require(catalog.listLookup("codex-mini-latest"))
        #expect(list.source == .liteLLM && list.entry == "codex-mini-latest")
        #expect(list.rates.input == 0.0000015)
        #expect(catalog.listLookup("gpt-4o")?.source == .liteLLM)
        #expect(catalog.listLookup("nonexistent-model") == nil)
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

    @Test func `one bad entry skips only itself and survives a save`() throws {
        let temp = try TempDir()
        let url = temp.path("config/pricing.json")
        try FileManager.default.createDirectory(
            at: temp.path("config"), withIntermediateDirectories: true)
        try Data(#"""
            {"schema": 1, "note": "mine", "models": {
              "swe-1": {"input": 2, "output": 8},
              "draft": "fill in later",
              "half": {"input": 1}}}
            """#.utf8).write(to: url)
        var overrides = PricingOverrides(url: url)
        #expect(overrides.models == ["swe-1": .init(input: 2, output: 8)])

        try overrides.set("swe-2", prices: .init(input: 1, output: 4))
        let object = try #require(
            JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
        let models = try #require(object["models"] as? [String: Any])
        #expect(Set(models.keys) == ["swe-1", "swe-2", "draft", "half"])
        #expect(models["draft"] as? String == "fill in later")
        #expect(object["note"] as? String == "mine")
        #expect(PricingOverrides(url: url).models.count == 2)
    }

    @Test func `a file that isn't JSON is never overwritten`() throws {
        let temp = try TempDir()
        let url = temp.path("config/pricing.json")
        try FileManager.default.createDirectory(
            at: temp.path("config"), withIntermediateDirectories: true)
        // Cut off mid-entry. (A trailing comma is no good here: Foundation's
        // JSONSerialization accepts one on recent macOS.)
        let text = Data(#"{"models": {"swe-1": {"input": 2, "output""#.utf8)
        try text.write(to: url)
        var overrides = PricingOverrides(url: url)
        #expect(overrides.models.isEmpty)
        #expect(throws: (any Error).self) {
            try overrides.set("swe-2", prices: .init(input: 1, output: 4))
        }
        #expect(overrides.models.isEmpty)
        #expect(try Data(contentsOf: url) == text)
    }

    @Test func `save then load round-trips, missing file is empty`() throws {
        let temp = try TempDir()
        let url = temp.path("pricing.json")
        let empty = PricingOverrides(url: url)
        #expect(empty.models["swe-2"] == nil)
        var overrides = PricingOverrides(url: url)
        try overrides.set("swe-2", prices: .init(input: 1.25, output: 5, cacheRead: 0.1, cacheWrite: 0.5))
        let again = PricingOverrides(url: url)
        let prices = try #require(again.models["swe-2"])
        #expect(prices.input == 1.25 && prices.output == 5 && prices.cacheRead == 0.1
                && prices.cacheWrite == 0.5)
    }
}

@Suite("Catalog lookup helpers") struct PricingLookupTests {
    @Test func `baseName strips provider prefixes, dates and effort suffixes`() {
        #expect(PricingCatalog.baseName(for: "swe-2-medium") == "swe-2")
        #expect(PricingCatalog.baseName(for: "anthropic/claude-opus-5-5-20260101") == "claude-opus-5-5")
        #expect(PricingCatalog.baseName(for: "gpt-5") == "gpt-5")
    }

    @Test func `an alias cycle resolves to nothing instead of recursing forever`() throws {
        var supplement = PricingSupplement()
        supplement.aliases = ["loop-a": "loop-b", "loop-b": "LOOP-A", "self-x": "self-x-high"]
        let catalog = try PricingCatalog(
            liteLLM: Data(#"{"gpt-5": {"input_cost_per_token": 0.000001, "output_cost_per_token": 0.000002}}"#.utf8),
            supplement: supplement)
        #expect(catalog.resolve(model: "loop-a") == nil)
        #expect(catalog.resolve(model: "self-x") == nil)
        #expect(catalog.resolve(model: "gpt-5") != nil)
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

    @Test func `columns drop as the card narrows`() {
        let full = PricingColumns.visible(forWidth: 700)
        #expect(full.cacheReadWrite && full.source)
        let tight = PricingColumns.visible(forWidth: 600)
        #expect(!tight.cacheReadWrite && tight.source)
        let tightest = PricingColumns.visible(forWidth: 480)
        #expect(!tightest.cacheReadWrite && !tightest.source)
    }

    @Test func `providers come from name prefixes`() {
        let cases: [(String, String)] = [
            ("claude-opus-5-5", "anthropic"), ("gpt-5", "openai"), ("o3", "openai"),
            ("codex-mini", "openai"), ("gemini-2.5-pro", "google"), ("gemma-3", "google"),
            ("grok-4", "xai"), ("swe-2-max", "cognition"), ("compactor", "cognition"),
            ("glm-5.2", "zai"), ("deepseek-v3", "deepseek"), ("kimi-k2", "moonshot"),
            ("moonshot-v1", "moonshot"), ("minimax-m2", "minimax"), ("qwen3-coder", "alibaba"),
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
        let ledger = try Ledger(directory: temp.path("cache"))
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

    @Test func `empty events without a charge leave a model harness-billed`() async throws {
        // Cursor's feed logs some Grok Bot events with no tokens and no
        // charge; they cost nothing either way.
        let (_, model, temp) = try makeModel([
            event("billed-model", tokens: 90, loggedCost: 1.2),
            event("billed-model", tokens: 0),
        ], catalog: priced)
        await model.load()
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.harnessBilled && row.totals.costUSD == 1.2)
        _ = temp
    }

    @Test func `a harness-billed row is billed by its biller, Grok Bot's by Cursor`() async throws {
        let (_, model, temp) = try makeModel([
            event("grok-bot-default", tokens: 90, loggedCost: 1.2, harness: "grok-bot"),
            event("composer-2", tokens: 90, loggedCost: 0.5, harness: "cursor"),
            event("swe-2", tokens: 90, loggedCost: 0.3, harness: "devin"),
            event("swe-2", tokens: 90, loggedCost: 0.3, harness: "grok-bot"),
        ], catalog: priced)
        await model.load()
        let rows = Dictionary(uniqueKeysWithValues: model.groups.flatMap(\.models).map { ($0.model, $0) })
        #expect(rows["grok-bot-default"]?.spendSource == .billed(by: ["cursor"]))
        #expect(rows["composer-2"]?.spendSource == .billed(by: ["cursor"]))
        #expect(rows["swe-2"]?.spendSource == .billed(by: ["devin", "cursor"])
                || rows["swe-2"]?.spendSource == .billed(by: ["cursor", "devin"]))
        _ = temp
    }

    @Test func `a mixed row names who bills its logged part`() async throws {
        // Cursor logs a charge; Claude Code's tokens of the same model don't.
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 90, loggedCost: 1.2, harness: "cursor"),
            event("claude-sonnet-4-6", tokens: 90, harness: "claude"),
            event("gemini-2.5-pro", tokens: 90, harness: "claude"),
        ], catalog: priced)
        await model.load()
        let rows = Dictionary(uniqueKeysWithValues: model.groups.flatMap(\.models).map { ($0.model, $0) })
        let mixed = try #require(rows["claude-sonnet-4-6"])
        #expect(!mixed.harnessBilled && mixed.billedBy == ["cursor"])
        // The mark follows the price for the uncharged part, not the biller.
        if case .billed = mixed.spendSource { Issue.record("a mixed row is not billed") }
        #expect(rows["gemini-2.5-pro"]?.billedBy == [])
        _ = temp
    }

    @Test func `a row's spend source follows its price when nothing bills it`() {
        func row(source: PricingCatalog.Source?, free: Bool = false) -> PricingModel.PricedModel {
            PricingModel.PricedModel(
                model: "m", totals: UsageTotals(), harnesses: ["claude"], harnessBilled: false,
                entry: nil, source: source, rates: nil, tiers: [], isFree: free,
                unpricedTokens: 0, listPrice: nil)
        }
        #expect(row(source: .liteLLM).spendSource == .liteLLM)
        #expect(row(source: .quiver).spendSource == .quiver)
        #expect(row(source: .yours).spendSource == .yours)
        #expect(row(source: nil).spendSource == .unpriced)
        #expect(row(source: nil, free: true).spendSource == .free)
    }

    @Test func `a model with uncharged tokens is not harness-billed`() async throws {
        let (_, model, temp) = try makeModel([
            event("billed-model", tokens: 90, loggedCost: 1.2),
            event("billed-model", tokens: 10),
        ], catalog: priced)
        await model.load()
        #expect(model.groups.flatMap(\.models).first?.harnessBilled == false)
        _ = temp
    }

    @Test func `a model with only empty uncharged events is not harness-billed`() async throws {
        let (_, model, temp) = try makeModel([event("billed-model", tokens: 0)], catalog: priced)
        await model.load()
        #expect(model.groups.flatMap(\.models).first?.harnessBilled == false)
        _ = temp
    }

    @Test func `a price saved for a harness-billed model shows, but its logged cost stays`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("grok-4.7-high (Auto)",
                                                             tokens: 1_000_000, loggedCost: 1.25)))
        let ledger = try Ledger(directory: temp.path("cache"))
        let file = temp.url.appending(path: "config/pricing.json")
        let model = PricingModel(
            ledger: ledger, overrides: PricingOverrides(url: file),
            makeCatalog: { try PricingCatalog(
                liteLLM: Data("{}".utf8),
                overrides: PricingOverrides(url: file).entries) },
            apply: { catalog in await ledger.setPricing(catalog) })
        try await model.save(key: "grok-4.7-high (Auto)", prices: .init(input: 3, output: 15))
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.harnessBilled && row.source == .yours && row.rates?.input == 3 / 1_000_000)
        // The harness's own charge, not 1M input tokens at $3/1M.
        #expect(row.totals.costUSD == 1.25)
        #expect(await ledger.totals(in: allTime).costUSD == 1.25)
    }

    @Test func `saving an override reprices the ledger`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("swe-2-medium", tokens: 1_000_000)))
        let ledger = try Ledger(directory: temp.path("cache"))
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

    @Test func `a slow catalog build never lands over a newer save`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("swe-2", tokens: 1_000_000)))
        let ledger = try Ledger(directory: temp.path("cache"))
        let file = temp.url.appending(path: "config/pricing.json")
        let firstRead = CountGate()
        let builds = OSAllocatedUnfairLock(initialState: 0)
        let release = DispatchSemaphore(value: 0)
        let model = PricingModel(
            ledger: ledger, overrides: PricingOverrides(url: file),
            makeCatalog: {
                // The first build reads the old prices, then holds until the
                // second save has applied: the older build finishes last,
                // the order a Cancel-and-save-again can give.
                let entries = PricingOverrides(url: file).entries
                // The build is synchronous, so it holds on a semaphore
                // rather than a gate; the bound only keeps a regression
                // from hanging the suite.
                if builds.withLock({ $0 += 1; return $0 }) == 1 {
                    Task { await firstRead.bump() }
                    _ = release.wait(timeout: .now() + .seconds(Int(gateTimeout.components.seconds)))
                }
                return try PricingCatalog(liteLLM: Data("{}".utf8), overrides: entries)
            },
            apply: { catalog in
                await ledger.setPricing(catalog)
                release.signal()
            })
        async let first: Void = model.save(key: "swe-2", prices: .init(input: 1, output: 1))
        #expect(await firstRead.reach(1))
        try await model.save(key: "swe-2", prices: .init(input: 5, output: 5))
        try await first
        #expect(await ledger.totals(in: allTime).costUSD == 5)
    }

    @Test func `saving under a changed key replaces the old one in one write`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("swe-2-max", tokens: 1_000)))
        let ledger = try Ledger(directory: temp.path("cache"))
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
        // Remove takes every saved key the model resolves to, whatever
        // the field said, but never a sibling's own price: swe-2-medium
        // doesn't price swe-2-max.
        try await model.remove(matching: "swe-2-max")
        #expect(PricingOverrides(url: file).models["swe-2-medium"]?.input == 2)
        try await model.remove(matching: "swe-2-medium")
        #expect(PricingOverrides(url: file).models.isEmpty)
        _ = temp
    }

    /// A model whose save/remove round-trips through a `pricing.json`,
    /// like the page does against the real file.
    private func editableModel(_ records: [UsageEvent]) throws -> (Ledger, PricingModel, TempDir) {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        for (i, event) in records.enumerated() {
            try store.upsert(LedgerRecord(key: "t:\(i)", event: event))
        }
        let ledger = try Ledger(directory: temp.path("cache"))
        let file = temp.url.appending(path: "config/pricing.json")
        let model = PricingModel(
            ledger: ledger, overrides: PricingOverrides(url: file),
            makeCatalog: { try PricingCatalog(
                liteLLM: Data("""
                    {"gpt-4o": {"input_cost_per_token": 0.0000025,
                    "output_cost_per_token": 0.00001}}
                    """.utf8),
                overrides: PricingOverrides(url: file).entries) },
            apply: { catalog in await ledger.setPricing(catalog) })
        return (ledger, model, temp)
    }

    @Test func `a saved price on a listed model offers reset, and reset restores LiteLLM`() async throws {
        let (ledger, model, temp) = try editableModel([event("gpt-4o", tokens: 1_000_000)])
        await model.load()
        var row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.source == .liteLLM)
        #expect(row.listPrice?.rates.input == 0.0000025)

        try await model.save(key: "gpt-4o", prices: .init(input: 1, output: 2))
        row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.source == .yours && row.rates?.input == 0.000001)
        // The shadowed LiteLLM price is still visible for the reset.
        if case .resetToList(let list) = row.resetKind {
            #expect(list.source == .liteLLM && list.rates.input == 0.0000025)
        } else {
            Issue.record("expected resetToList, got \(row.resetKind)")
        }

        try await model.remove(matching: "gpt-4o")
        row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.source == .liteLLM && row.rates?.input == 0.0000025)
        // The ledger repriced back to the list price.
        let totals = await ledger.totals(in: allTime)
        #expect(totals.costUSD == 2.5)  // 1M input @ $2.50/1M
        _ = temp
    }

    @Test func `a saved price with no list price under it offers remove`() async throws {
        let (_, model, temp) = try editableModel([event("swe-2-medium", tokens: 1_000)])
        try await model.save(key: "swe-2", prices: .init(input: 1, output: 1))
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.source == .yours && row.listPrice == nil && row.resetKind == .remove)
        _ = temp
    }

    @Test func `saved keys keep their effort suffix when matching`() throws {
        let price: [String: Any] = ["input_cost_per_token": 0.000001, "output_cost_per_token": 0.000001]
        let catalog = try PricingCatalog(
            liteLLM: Data("{}".utf8),
            overrides: ["swe-2-medium": price, "swe-2-high": price, "swe-2": price])
        #expect(catalog.userKeys(matching: "swe-2-medium") == ["swe-2", "swe-2-medium"])
        #expect(catalog.userKeys(matching: "swe-2-high") == ["swe-2", "swe-2-high"])
    }

    @Test func `reset removes every saved key that matches the model`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        try store.upsert(LedgerRecord(key: "t:1", event: event("gpt-4.1", tokens: 1_000_000)))
        let ledger = try Ledger(directory: temp.path("cache"))
        let file = temp.url.appending(path: "config/pricing.json")
        let liteLLM = Data("""
            {"gpt-4.1": {"input_cost_per_token": 0.000002,
            "output_cost_per_token": 0.000008}}
            """.utf8)
        let model = PricingModel(
            ledger: ledger, overrides: PricingOverrides(url: file),
            makeCatalog: { try PricingCatalog(
                liteLLM: liteLLM,
                overrides: PricingOverrides(url: file).entries) },
            apply: { catalog in await ledger.setPricing(catalog) })
        // Two different saved keys both price gpt-4.1; removing just the
        // row's own key would leave the other one charging.
        try await model.save(key: "gpt-4.1", prices: .init(input: 1, output: 1))
        try await model.save(key: "gpt-4-1", prices: .init(input: 2, output: 2))
        await model.load()
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.source == .yours)

        try await model.remove(matching: "gpt-4.1")
        #expect(PricingOverrides(url: file).models.isEmpty)
        let reset = try #require(model.groups.flatMap(\.models).first)
        #expect(reset.source == .liteLLM && reset.rates?.input == 0.000002)
        let totals = await ledger.totals(in: allTime)
        #expect(totals.costUSD == 2)  // 1M input @ $2.00/1M
        _ = temp
    }

    @Test func `remove with no list price leaves the model unpriced`() async throws {
        let (_, model, temp) = try editableModel([event("swe-2-medium", tokens: 1_000)])
        try await model.save(key: "swe-2", prices: .init(input: 1, output: 1))
        try await model.remove(matching: "swe-2-medium")
        let row = try #require(model.groups.flatMap(\.models).first)
        #expect(row.source == nil && !row.isFree && row.unpricedTokens == 1_000)
        #expect(model.unpriced.map(\.model) == ["swe-2-medium"])
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

extension PricingModelTests {
    @Test func `a harness filter keeps only its models and tokens`() async throws {
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 50, harness: "claude"),
            event("gpt-5", tokens: 300, harness: "codex"),
            event("claude-opus-5-5", tokens: 100, harness: "codex"),
        ], catalog: priced)
        await model.load()
        model.harnessFilter = ["codex"]
        await model.load()
        let rows = model.groups.flatMap(\.models)
        #expect(rows.map(\.model).sorted() == ["claude-opus-5-5", "gpt-5"])
        #expect(rows.allSatisfy { $0.harnesses == ["codex"] })
        #expect(model.groups.reduce(0) { $0 + $1.tokens } == 400)
        _ = temp
    }

    @Test func `the unpriced list follows the filter`() async throws {
        let (ledger, model, temp) = try makeModel([
            event("swe-2-max", tokens: 200, harness: "claude"),
            event("kimi-k2", tokens: 300, harness: "codex"),
            event("gpt-5", tokens: 10, harness: "codex"),
        ], catalog: priced)
        await ledger.setPricing(priced)
        await model.load()
        #expect(model.unpriced.map(\.model).sorted() == ["gpt-5", "kimi-k2", "swe-2-max"])
        model.harnessFilter = ["codex"]
        await model.load()
        #expect(model.unpriced.map(\.model) == ["kimi-k2", "gpt-5"])
        _ = temp
    }

    @Test func `two picked harnesses keep both their models and no others`() async throws {
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 50, harness: "claude"),
            event("gpt-5", tokens: 300, harness: "codex"),
            event("claude-opus-5-5", tokens: 100, harness: "codex"),
            event("claude-opus-5-5", tokens: 20, harness: "gemini"),
            event("gemini-2.5-pro", tokens: 70, harness: "gemini"),
        ], catalog: priced)
        await model.load()
        model.harnessFilter = ["claude", "codex"]
        await model.load()
        let rows = model.groups.flatMap(\.models)
        #expect(rows.map(\.model).sorted() == ["claude-opus-5-5", "claude-sonnet-4-6", "gpt-5"])
        // gemini's share of a shared model stays out too.
        #expect(rows.first { $0.model == "claude-opus-5-5" }?.harnesses == ["codex"])
        #expect(model.groups.reduce(0) { $0 + $1.tokens } == 450)
        _ = temp
    }

    @Test func `a picked harness that vanished drops out and the rest stay`() async throws {
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 50, harness: "claude"),
            event("gpt-5", tokens: 300, harness: "codex",
                  at: Date().addingTimeInterval(-60 * 86_400)),
        ], catalog: priced)
        model.period = .allTime
        await model.load()
        model.harnessFilter = ["claude", "codex"]
        await model.load()
        model.period = .today
        await model.load()
        #expect(model.harnessFilter == ["claude"])
        #expect(model.groups.flatMap(\.models).map(\.model) == ["claude-sonnet-4-6"])
        _ = temp
    }

    @Test func `a filter whose harness vanished falls back to all`() async throws {
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 50, harness: "claude"),
            event("gpt-5", tokens: 300, harness: "codex",
                  at: Date().addingTimeInterval(-60 * 86_400)),
        ], catalog: priced)
        model.period = .allTime
        await model.load()
        model.harnessFilter = ["codex"]
        await model.load()
        #expect(model.groups.flatMap(\.models).map(\.model) == ["gpt-5"])
        model.period = .today
        await model.load()
        #expect(model.harnessFilter.isEmpty)
        #expect(model.harnessOptions == ["claude"])
        _ = temp
    }

    /// Parks the first `slices` call after `arm()` at a gate the test
    /// opens by hand, so a superseded load provably sits inside its first
    /// suspension while its replacement runs to completion.
    private final class Gate: Sendable {
        private let armed = OSAllocatedUnfairLock(initialState: false)
        let hold = HoldGate()

        func arm() { armed.withLock { $0 = true } }
        /// Disarms and says whether it was armed, in one step.
        func takeArmed() -> Bool {
            armed.withLock { isArmed in
                defer { isArmed = false }
                return isArmed
            }
        }
    }

    @Test func `a superseded load does not clear the filter or republish`() async throws {
        let temp = try TempDir()
        let store = try LedgerStore(url: temp.path("cache/ledger.sqlite"))
        for (i, e) in [
            event("claude-sonnet-4-6", tokens: 50, harness: "claude"),
            event("gpt-5", tokens: 300, harness: "codex",
                  at: Date().addingTimeInterval(-60 * 86_400)),
        ].enumerated() {
            try store.upsert(LedgerRecord(key: "t:\(i)", event: e))
        }
        let ledger = try Ledger(directory: temp.path("cache"))
        let gate = Gate()
        let catalog = priced
        let model = PricingModel(
            ledger: ledger,
            overrides: PricingOverrides(url: temp.url.appending(path: "config/pricing.json")),
            makeCatalog: { catalog }, apply: { _ in },
            slices: { range in
                if gate.takeArmed() {
                    try? await gate.hold.pass()
                }
                return await ledger.byModelHarness(in: range)
            })
        model.period = .allTime
        await model.load()
        model.harnessFilter = ["codex"]
        await model.load()

        // The stale "today" load (no codex in it) parks on the gate; its
        // replacement runs, then the stale one resumes and must leave
        // the filter, the options and the rows alone.
        model.period = .today
        gate.arm()
        let stale = Task { await model.load() }
        try #require(await gate.hold.arrived(1), "the first load never parked")
        model.period = .allTime
        await model.load()
        gate.hold.open()
        await stale.value
        #expect(model.harnessFilter == ["codex"])
        #expect(model.groups.flatMap(\.models).map(\.model) == ["gpt-5"])
    }

    @Test func `the covered-by list stays unfiltered`() async throws {
        let (_, model, temp) = try makeModel([
            event("swe-2-max", tokens: 200, harness: "claude"),
            event("swe-2-medium", tokens: 100, harness: "codex"),
        ], catalog: priced)
        await model.load()
        model.harnessFilter = ["codex"]
        await model.load()
        // The sheet warns that a saved price applies to every harness, so
        // the names it lists must too.
        #expect(model.coveredBy("swe-2") == ["swe-2-max", "swe-2-medium"])
        _ = temp
    }

    @Test func `the harness options sort by tokens`() async throws {
        let (_, model, temp) = try makeModel([
            event("claude-sonnet-4-6", tokens: 100, harness: "claude"),
            event("gpt-5", tokens: 300, harness: "codex"),
            event("swe-2-max", tokens: 200, harness: "devin"),
        ], catalog: priced)
        await model.load()
        #expect(model.harnessOptions == ["codex", "devin", "claude"])
        _ = temp
    }
}

