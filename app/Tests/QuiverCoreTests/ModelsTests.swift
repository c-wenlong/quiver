import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// A transport stub that records every request and answers from a handler.
private final class StubHTTP: HTTPTransport, @unchecked Sendable {
    /// URL path → (body, status, headers); unmatched paths throw.
    var handler: (URLRequest) throws -> (Data, Int, [String: String]) = { _ in
        ("{}".data(using: .utf8)!, 200, [:])
    }
    private(set) var requests: [URLRequest] = []

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        let (data, status, headers) = try handler(request)
        return (data, HTTPURLResponse(url: request.url!, statusCode: status,
                                      httpVersion: nil, headerFields: headers)!)
    }
}

@Suite struct ArtificialAnalysisDecodingTests {
    /// The docs' example shape plus a benchmark AA added later.
    private let fixture = """
    {"status": 200, "prompt_options": {},
     "data": [
       {"id": "m1", "name": "Claude Opus 5.5", "slug": "claude-opus-5-5",
        "model_creator": {"id": "c1", "name": "Anthropic", "slug": "anthropic"},
        "evaluations": {"artificial_analysis_intelligence_index": 58.2,
                        "artificial_analysis_coding_index": 55.1,
                        "artificial_analysis_math_index": 40.0,
                        "mmlu_pro": 88.0, "brand_new_bench": 12.5,
                        "dropped_null": null},
        "pricing": {"price_1m_blended_3_to_1": 15.0,
                    "price_1m_input_tokens": 10.0,
                    "price_1m_output_tokens": 25.0},
        "median_output_tokens_per_second": 61.4,
        "median_time_to_first_token_seconds": 0.82,
        "median_time_to_first_answer_token": 1.9},
       {"id": "m2", "name": "GPT-6 Astra", "slug": "gpt-6-astra",
        "model_creator": {"id": "c2", "name": "OpenAI", "slug": "openai"},
        "evaluations": {"artificial_analysis_intelligence_index": 55.9},
        "pricing": {"price_1m_blended_3_to_1": 8.0},
        "median_output_tokens_per_second": 130.0}
     ]}
    """.data(using: .utf8)!

    @Test func decodesEveryFieldAndKeepsUnknownEvaluations() throws {
        let models = try AAModelsResponse.decode(fixture)
        #expect(models.count == 2)
        let first = try #require(models.first)
        #expect(first.id == "m1")
        #expect(first.name == "Claude Opus 5.5")
        #expect(first.creatorName == "Anthropic")
        #expect(first.creatorSlug == "anthropic")
        #expect(first.intelligence == 58.2)
        #expect(first.coding == 55.1)
        #expect(first.maths == 40.0)
        // AA adds benchmarks; the dict must keep them.
        #expect(first.evaluations["brand_new_bench"] == 12.5)
        // Nulls drop out instead of decoding as 0.
        #expect(first.evaluations["dropped_null"] == nil)
        #expect(first.priceBlended == 15.0)
        #expect(first.priceInput == 10.0)
        #expect(first.priceOutput == 25.0)
        #expect(first.speed == 61.4)
        #expect(first.timeToFirstToken == 0.82)
        #expect(first.timeToFirstAnswer == 1.9)
        // Missing sections leave nils, not crashes.
        let second = models[1]
        #expect(second.coding == nil)
        #expect(second.priceInput == nil)
        #expect(second.timeToFirstToken == nil)
    }

    @Test func rejectsAResponseWithNoDataArray() {
        #expect(throws: AAError.badResponse) {
            _ = try AAModelsResponse.decode(#"{"status": 200}"#.data(using: .utf8)!)
        }
    }
}

@Suite struct ArtificialAnalysisStoreTests {
    private func makeStore(_ dir: TempDir, now: Date = Date(timeIntervalSince1970: 1_800_000_000)) -> ArtificialAnalysisStore {
        ArtificialAnalysisStore(directory: dir.url, now: { now })
    }

    private let llmsBody = """
    {"data": [{"id": "m1", "name": "Claude Opus 5.5", "slug": "opus",
               "model_creator": {"id": "c", "name": "Anthropic", "slug": "anthropic"},
               "evaluations": {"artificial_analysis_intelligence_index": 58.0},
               "pricing": {"price_1m_blended_3_to_1": 15.0}}]}
    """.data(using: .utf8)!

    @Test func refreshWritesAllSixEndpointsAndMeta() async throws {
        let dir = try TempDir()
        let transport = StubHTTP()
        transport.handler = { _ in (self.llmsBody, 200, ["X-RateLimit-Remaining": "999"]) }
        let store = makeStore(dir)
        let errors = await store.refresh(key: "test-key", transport: transport)
        #expect(errors.isEmpty)
        #expect(transport.requests.count == 6)
        // Every request carried the key.
        #expect(transport.requests.allSatisfy { $0.value(forHTTPHeaderField: "x-api-key") == "test-key" })
        #expect(store.languageModels()?.first?.name == "Claude Opus 5.5")
        let meta = store.meta()[AAEndpoint.llms.rawValue]
        #expect(meta?.status == 200)
        #expect(meta?.rateLimitHeaders?["X-RateLimit-Remaining"] == "999")
        #expect(meta?.fetchedAt == 1_800_000_000)
        for endpoint in AAEndpoint.allCases {
            #expect(FileManager.default.fileExists(atPath: dir.path(endpoint.fileName).path))
        }
    }

    @Test func aFailedRequestKeepsTheStoredDataAndRecordsTheError() async throws {
        let dir = try TempDir()
        let transport = StubHTTP()
        transport.handler = { _ in (self.llmsBody, 200, [:]) }
        let store = makeStore(dir)
        await store.refresh(key: "key", transport: transport)
        // A 429 on the next refresh must not clobber the good bytes.
        transport.handler = { _ in ("rate limited".data(using: .utf8)!, 429, [:]) }
        let errors = await store.refresh(key: "key", transport: transport)
        #expect(errors[.llms] == "HTTP 429")
        #expect(store.languageModels()?.first?.name == "Claude Opus 5.5")
        #expect(store.meta()[AAEndpoint.llms.rawValue]?.error == "HTTP 429")
    }

    @Test func badLLMJSONIsAnErrorNotAWrite() async throws {
        let dir = try TempDir()
        let transport = StubHTTP()
        transport.handler = { request in
            if request.url?.path.hasSuffix("/data/llms/models") == true {
                return ("not json".data(using: .utf8)!, 200, [:])
            }
            return ("{}".data(using: .utf8)!, 200, [:])
        }
        let store = makeStore(dir)
        let errors = await store.refresh(key: "key", transport: transport)
        #expect(errors[.llms] == "Unusable response")
        #expect(store.languageModels() == nil)
        #expect(store.meta()[AAEndpoint.llms.rawValue]?.error == "Unusable response")
        // The other endpoints still saved.
        #expect(FileManager.default.fileExists(atPath: dir.path(AAEndpoint.textToImage.fileName).path))
    }

    @Test func stalenessTurnsOnMissingAndOldDataOnly() throws {
        let dir = try TempDir()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        var store = ArtificialAnalysisStore(directory: dir.url, now: { now })
        #expect(store.llmsIsStale())
        // Fresh data is not stale.
        try llmsBody.write(to: dir.path(AAEndpoint.llms.fileName))
        store = makeStore(dir, now: now)
        // Pretend a successful fetch wrote meta 1h ago.
        try #"{"llms": {"fetchedAt": 1799964000.0, "status": 200}}"#
            .data(using: .utf8)!
            .write(to: dir.path("meta.json"))
        #expect(!store.llmsIsStale())
        // Eight days later it is.
        store = ArtificialAnalysisStore(directory: dir.url,
                                        now: { now.addingTimeInterval(8 * 86_400) })
        #expect(store.llmsIsStale())
    }
}

@Suite struct ModelMatcherTests {
    private func aa(_ name: String, slug: String, creator: String = "anthropic",
                    intelligence: Double = 50) -> AALanguageModel {
        AALanguageModel(id: slug, name: name, slug: slug, creatorSlug: creator,
                        evaluations: ["artificial_analysis_intelligence_index": intelligence])
    }

    @Test func parsesProviderPrefixSnapshotDateFastAndEffort() {
        let parsed = ModelMatcher.parse("openai/gpt-6.1-sol-max@20261003-fast")
        #expect(parsed.base == "gpt-6-1-sol")
        #expect(parsed.effort == "max")
        #expect(parsed.fast)
        #expect(ModelMatcher.parse("Claude Opus 5.5 (max)").effort == "max")
        #expect(ModelMatcher.parse("claude-opus-5-5").effort == nil)
        #expect(!ModelMatcher.parse("gpt-6-astra").fast)
    }

    @Test func parsesAAEffortTailsWithANoteAfterTheEffort() {
        let parsed = ModelMatcher.parse("Claude Opus 5.5 (Max, Default Fallback)")
        #expect(parsed.base == "claude-opus-5-5")
        #expect(parsed.effort == "max")
        #expect(ModelMatcher.parse("Claude Fable 5 (Max, Opus 4.8 Fallback)").base == "claude-fable-5")
        // A parenthesis that leads with something else is its own model.
        #expect(ModelMatcher.parse("Claude Sonnet 5 (Non-reasoning, High)").effort == nil)
    }

    /// AA's real naming: the effort-free slug is the headline run.
    private var opusFamily: [AALanguageModel] {
        [aa("Claude Opus 5.5 (Max, Default Fallback)", slug: "claude-opus-5-5", intelligence: 57.6),
         aa("Claude Opus 5.5 (Xhigh, Default Fallback)", slug: "claude-opus-5-5-xhigh", intelligence: 56),
         aa("Claude Opus 5.5 (High, Default Fallback)", slug: "claude-opus-5-5-high", intelligence: 53.6)]
    }

    @Test func harnessVariantsResolveToTheFoundationModel() {
        // Devin and Cursor log the effort and mode they ran; the card is
        // the foundation model's, as AA headlines it.
        for logged in ["claude-opus-5-5-high-fast", "claude-opus-5-5", "anthropic/claude-opus-5-5-xhigh",
                       "claude-opus-5.5-max@20260922"] {
            #expect(ModelMatcher.match(logged, in: opusFamily)?.slug == "claude-opus-5-5")
        }
    }

    @Test func aLinkToAnEffortEntryLandsOnItsFamilysHeadline() {
        // The link sheet lists every effort; the leaderboard only headlines.
        #expect(ModelMatcher.match("my-proxy-model", in: opusFamily,
                                   links: ["my-proxy-model": "claude-opus-5-5-high"])?.slug
                == "claude-opus-5-5")
    }

    @Test func aFamilyWithNoEffortFreeSlugTakesItsHighestEffort() {
        let models = [aa("Grok 9 (High)", slug: "grok-9-high", creator: "x-ai", intelligence: 40),
                      aa("Grok 9 (Max)", slug: "grok-9-max", creator: "x-ai", intelligence: 39),
                      aa("Grok 9 (Low)", slug: "grok-9-low", creator: "x-ai", intelligence: 30)]
        #expect(ModelMatcher.match("grok-9-low", in: models)?.slug == "grok-9-max")
    }

    @Test func headlinesListOneEntryPerFoundationModel() {
        let models = opusFamily + [aa("GPT-6 Astra (Max)", slug: "gpt-6-astra", creator: "openai"),
                                   aa("GPT-6 Astra (High)", slug: "gpt-6-astra-high", creator: "openai")]
        #expect(ModelMatcher.headlines(in: models).map(\.slug) == ["claude-opus-5-5", "gpt-6-astra"])
    }

    @Test func displayNameDropsOnlyTheEffortTail() {
        #expect(ModelMatcher.displayName(opusFamily[0]) == "Claude Opus 5.5")
        #expect(ModelMatcher.displayName(aa("GPT-6 Sol (Non-reasoning)", slug: "x")) == "GPT-6 Sol (Non-reasoning)")
    }

    @Test func dotsAndDatesNormalizeTheLoggedName() {
        let models = [aa("GPT-6.1 Sol (max)", slug: "gpt-6-1-sol-max", creator: "openai")]
        #expect(ModelMatcher.match("gpt-6.1-sol@20261001", in: models)?.id == "gpt-6-1-sol-max")
    }

    @Test func unmatchedNamesStayUnmatched() {
        let models = [aa("Claude Opus 5.5", slug: "claude-opus-5-5")]
        #expect(ModelMatcher.match("swe-2-medium", in: models) == nil)
        #expect(ModelMatcher.match("grok-bot-abc123", in: models) == nil)
        #expect(ModelMatcher.match("compactor", in: models) == nil)
    }

    @Test func aManualLinkBeatsEveryRule() throws {
        let models = [aa("Claude Opus 5.5", slug: "claude-opus-5-5"),
                      aa("GPT-6 Astra", slug: "gpt-6-astra", creator: "openai")]
        // Without the link the name doesn't match; with it the link wins.
        #expect(ModelMatcher.match("my-proxy-model", in: models) == nil)
        #expect(ModelMatcher.match("my-proxy-model", in: models,
                                   links: ["my-proxy-model": "gpt-6-astra"])?.id == "gpt-6-astra")
        // Links survive a save/load round trip.
        let dir = try TempDir()
        let url = dir.path("config/model-links.json")
        try ModelMatcher.saveLinks(["my-proxy-model": "gpt-6-astra"], to: url)
        #expect(ModelMatcher.loadLinks(from: url) == ["my-proxy-model": "gpt-6-astra"])
    }

    @Test func effortTiesTakeTheHigherIntelligence() {
        let models = [
            aa("Grok 4.7 (high)", slug: "grok-4-7-high", creator: "x-ai", intelligence: 40),
            aa("Grok 4.7 (high)", slug: "grok-4-7-high-2", creator: "x-ai", intelligence: 46),
        ]
        #expect(ModelMatcher.match("grok-4.7-high", in: models)?.id == "grok-4-7-high-2")
    }
}

@Suite struct CostPercentileTests {
    private func priced(_ id: String, _ price: Double?) -> AALanguageModel {
        AALanguageModel(id: id, name: id, slug: id,
                        priceBlended: price)
    }

    @Test func extremesAreZeroAndOneAndTiesShare() {
        let models = [priced("cheap", 1), priced("mid-a", 5), priced("mid-b", 5), priced("dear", 10)]
        #expect(CostPercentile.rank(of: models[0], in: models) == 0)
        // The two $5 models share a spot: (1 cheaper + 0.5 ties) / 3.
        #expect(CostPercentile.rank(of: models[1], in: models) == 0.5)
        #expect(CostPercentile.rank(of: models[2], in: models) == 0.5)
        #expect(CostPercentile.rank(of: models[3], in: models) == 1)
    }

    @Test func unpricedOrLoneModelsHaveNoRank() {
        let free = priced("free", nil)
        #expect(CostPercentile.rank(of: free, in: [free, priced("x", 5)]) == nil)
        let one = priced("one", 5)
        #expect(CostPercentile.rank(of: one, in: [one, free]) == nil)
    }
}

@Suite @MainActor struct ModelsModelTests {
    private func makeModel(dir: TempDir,
                           slices: [ModelUsage] = [],
                           key: String? = "key") -> ModelsModel {
        ModelsModel(store: ArtificialAnalysisStore(directory: dir.path("aa")),
                    keyReader: { key },
                    transport: StubHTTP(),
                    linksURL: dir.path("config/model-links.json"),
                    slices: { _ in slices },
                    now: { Date(timeIntervalSince1970: 1_800_000_000) },
                    calendar: Calendar(identifier: .gregorian))
    }

    private func slice(_ model: String, _ harness: String, tokens: Int) -> ModelUsage {
        var totals = UsageTotals()
        totals.outputTokens = tokens
        return ModelUsage(model: model, harness: harness, tier: nil,
                          totals: totals, harnessBilled: false)
    }

    private func writeLLMs(_ dir: TempDir, _ models: [[String: Any]]) throws {
        let aa = dir.path("aa")
        try FileManager.default.createDirectory(at: aa, withIntermediateDirectories: true)
        let body = try JSONSerialization.data(withJSONObject: ["data": models])
        try body.write(to: aa.appending(path: AAEndpoint.llms.fileName))
        try #"{"llms": {"fetchedAt": 1800000000.0, "status": 200}}"#
            .data(using: .utf8)!.write(to: aa.appending(path: "meta.json"))
    }

    private func entry(_ id: String, _ name: String, _ slug: String,
                       creator: String, intelligence: Double? = nil,
                       coding: Double? = nil, price: Double? = nil) -> [String: Any] {
        var evals: [String: Any] = [:]
        if let intelligence { evals["artificial_analysis_intelligence_index"] = intelligence }
        if let coding { evals["artificial_analysis_coding_index"] = coding }
        return ["id": id, "name": name, "slug": slug,
                "model_creator": ["id": creator, "name": creator, "slug": creator],
                "evaluations": evals,
                "pricing": price.map { ["price_1m_blended_3_to_1": $0] } ?? [:]]
    }

    @Test func usedModelsMatchAndUnmatchedOnesCollect() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("opus", "Claude Opus 5.5 (max)", "claude-opus-5-5-max",
                  creator: "anthropic", intelligence: 58, price: 15),
        ])
        let model = makeModel(dir: dir, slices: [
            slice("claude-opus-5-5-max", "claude", tokens: 1000),
            slice("swe-2-medium", "devin", tokens: 400),
        ])
        await model.load()
        #expect(model.hasKey)
        // Unmatched names never reach the chart or table.
        #expect(model.rows.count == 1)
        #expect(model.rows.allSatisfy { $0.aa != nil })
        let used = model.rows.first { $0.logged == "claude-opus-5-5-max" }
        #expect(used?.aa?.id == "opus")
        #expect(used?.tokens == 1000)
        #expect(used?.harnesses == ["claude"])
        #expect(model.unmatched == [ModelsModel.Unmatched(model: "swe-2-medium", tokens: 400)])
    }

    @Test func loggedAliasesOfOneAAModelMergeIntoOneRow() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("grok", "Grok 4.7 (xhigh)", "grok-4-7-xhigh",
                  creator: "xai", intelligence: 46),
        ])
        let model = makeModel(dir: dir, slices: [
            slice("grok-4.7", "grok", tokens: 100),
            slice("grok-4.7-fast", "cursor", tokens: 50),
        ])
        await model.load()
        let used = model.rows.filter(\.used)
        #expect(used.count == 1)
        #expect(used[0].aa?.id == "grok")
        #expect(used[0].tokens == 150)
        #expect(used[0].harnesses == ["grok", "cursor"])
        #expect(model.unmatched.isEmpty)
    }

    @Test func scopeSwitchesBetweenYoursAndTheLeaderboard() async throws {
        let dir = try TempDir()
        var entries = (0..<12).map { i in
            entry("top-\(i)", "Top \(i)", "top-\(i)", creator: "openai",
                  intelligence: Double(90 - i))
        }
        entries.append(entry("mine", "My Model", "mine", creator: "anthropic", intelligence: 10))
        try writeLLMs(dir, entries)
        let model = makeModel(dir: dir, slices: [slice("my-model", "claude", tokens: 10)])
        await model.load()
        // "Your models + top 10": the used row plus ten leaders.
        #expect(model.scope == .yoursPlusTop)
        #expect(model.rows.count == 11)
        #expect(model.rows.contains { $0.logged == "my-model" })
        model.set(scope: .yours)
        #expect(model.rows.count == 1)
        model.set(scope: .top25)
        #expect(model.rows.count == 13)
    }

    @Test func harnessFilterNarrowsRowsAndDropsDeadFilters() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("a", "A", "a", creator: "openai", intelligence: 80),
            entry("b", "B", "b", creator: "anthropic", intelligence: 70),
        ])
        let model = makeModel(dir: dir, slices: [
            slice("a", "codex", tokens: 100),
            slice("b", "claude", tokens: 50),
        ])
        await model.load()
        #expect(model.harnessOptions == ["codex", "claude"])
        model.set(harnessFilter: "codex")
        #expect(model.rows.filter(\.used).map(\.logged) == ["a"])
        // A filter whose harness vanished from the period resets itself.
        model.set(harnessFilter: "pi")
        #expect(model.harnessFilter == nil)
    }

    @Test func metricSortRunsBothDirectionsAndNilsSitLast() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("hi", "Hi", "hi", creator: "openai", intelligence: 90, price: 20),
            entry("lo", "Lo", "lo", creator: "openai", intelligence: 10, price: 1),
            // A used model with no benchmark for the metric sorts last.
            entry("noval", "NoVal", "noval", creator: "mistral"),
        ])
        let model = makeModel(dir: dir, slices: [
            slice("noval", "pi", tokens: 5),
            slice("unmatched-x", "pi", tokens: 3),
        ])
        await model.load()
        model.set(scope: .top25)
        // Top 25 is the leaderboard only: "noval" has no intelligence,
        // so it never makes the board even though it ran.
        #expect(model.rows.map(\.logged) == ["Hi", "Lo"])
        // Cost sorts cheapest-first; "noval" is still off the board.
        model.set(metric: .cost)
        #expect(model.rows.map(\.logged) == ["Lo", "Hi"])
        #expect(model.unmatched == [ModelsModel.Unmatched(model: "unmatched-x", tokens: 3)])
    }

    @Test func linkingRematchesTheLoggedName() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [entry("opus", "Claude Opus 5.5", "opus",
                                 creator: "anthropic", intelligence: 58)])
        let model = makeModel(dir: dir, slices: [slice("my-alias", "claude", tokens: 10)])
        await model.load()
        #expect(model.unmatched.count == 1)
        try model.link(logged: "my-alias", to: "opus")
        #expect(model.unmatched.isEmpty)
        #expect(model.rows.first?.aa?.id == "opus")
        #expect(ModelMatcher.loadLinks(from: dir.path("config/model-links.json"))["my-alias"] == "opus")
    }

    @Test func noKeyKeepsTheEmptyStateAndMakesNoRequests() async throws {
        let dir = try TempDir()
        let transport = StubHTTP()
        let model = ModelsModel(store: ArtificialAnalysisStore(directory: dir.path("aa")),
                                keyReader: { nil }, transport: transport,
                                linksURL: dir.path("links.json"),
                                slices: { _ in [] })
        await model.load()
        #expect(!model.hasKey)
        #expect(model.allModels.isEmpty)
        #expect(transport.requests.isEmpty)
    }

    /// A slices closure the test pauses and releases by hand; each entry
    /// is one in-flight call.
    private final class SlicesGate: @unchecked Sendable {
        private let state = Mutex<[CheckedContinuation<[ModelUsage], Never>]>([])
        var pendingCount: Int { state.withLock { $0.count } }
        func call(_ range: DateInterval) async -> [ModelUsage] {
            _ = range
            return await withCheckedContinuation { continuation in
                state.withLock { $0.append(continuation) }
            }
        }
        func resume(_ index: Int, with value: [ModelUsage]) {
            state.withLock { $0[index] }.resume(returning: value)
        }
    }

    private func gatedModel(dir: TempDir, gate: SlicesGate) -> ModelsModel {
        ModelsModel(store: ArtificialAnalysisStore(directory: dir.path("aa")),
                    keyReader: { "key" },
                    transport: StubHTTP(),
                    linksURL: dir.path("config/model-links.json"),
                    slices: { range in await gate.call(range) },
                    now: { Date(timeIntervalSince1970: 1_800_000_000) },
                    calendar: Calendar(identifier: .gregorian))
    }

    private func waitForCalls(_ gate: SlicesGate, _ count: Int) async throws {
        while gate.pendingCount < count {
            try await Task.sleep(for: .milliseconds(5))
        }
    }

    @Test func anOlderPeriodAnswerCannotOverwriteANewerOne() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("a", "A", "a", creator: "openai", intelligence: 80),
            entry("b", "B", "b", creator: "anthropic", intelligence: 70),
        ])
        let gate = SlicesGate()
        let model = gatedModel(dir: dir, gate: gate)
        let loading = Task { await model.load() }
        try await waitForCalls(gate, 1)
        gate.resume(0, with: [slice("b", "claude", tokens: 50)])
        await loading.value
        #expect(model.rows.filter(\.used).map(\.logged) == ["b"])

        // Two period switches: the week's answer lands after today's and
        // must not write its older data over it.
        let older = Task { await model.set(period: .week) }
        try await waitForCalls(gate, 2)
        let newer = Task { await model.set(period: .today) }
        try await waitForCalls(gate, 3)
        gate.resume(2, with: [slice("a", "codex", tokens: 9)])
        await newer.value
        gate.resume(1, with: [slice("b", "claude", tokens: 5000)])
        await older.value
        #expect(model.period == .today)
        let used = model.rows.filter(\.used)
        #expect(used.map(\.logged) == ["a"])
        #expect(used.first?.tokens == 9)
    }

    @Test func aSecondConcurrentRefreshDoesNotDoubleTheFetches() async throws {
        let dir = try TempDir()
        let transport = StubHTTP()
        transport.handler = { _ in
            (#"{"data":[{"id":"m","name":"M","slug":"m","model_creator":{"id":"c","name":"C","slug":"c"},"evaluations":{},"pricing":{}}]}"#
                .data(using: .utf8)!, 200, [:])
        }
        let model = ModelsModel(store: ArtificialAnalysisStore(directory: dir.path("aa")),
                                keyReader: { "key" }, transport: transport,
                                linksURL: dir.path("links.json"),
                                slices: { _ in [] })
        // The guard lands before the first await, so whichever call runs
        // second returns immediately instead of fetching six endpoints again.
        async let first: Void = model.refresh()
        async let second: Void = model.refresh()
        await first
        await second
        #expect(transport.requests.count == 6)
    }

    @Test func loadIfNeededReadsTheCacheWithoutAnyRequests() async throws {
        let dir = try TempDir()
        // Stale cache (no meta): a plain load() would refresh first.
        let aa = dir.path("aa")
        try FileManager.default.createDirectory(at: aa, withIntermediateDirectories: true)
        try #"{"data":[{"id":"a","name":"A","slug":"a","model_creator":{"id":"o","name":"O","slug":"o"},"evaluations":{"artificial_analysis_intelligence_index":80},"pricing":{}}]}"#
            .data(using: .utf8)!.write(to: aa.appending(path: AAEndpoint.llms.fileName))
        let transport = StubHTTP()
        let model = ModelsModel(store: ArtificialAnalysisStore(directory: aa),
                                keyReader: { "key" }, transport: transport,
                                linksURL: dir.path("links.json"),
                                slices: { _ in [] })
        await model.loadIfNeeded()
        #expect(transport.requests.isEmpty)
        #expect(model.hasKey)
        #expect(!model.rows.isEmpty)
        // A second hover is a no-op, not another read.
        await model.loadIfNeeded()
        #expect(transport.requests.isEmpty)
    }

    /// A mutable slices answer, so a period switch can return a
    /// different set than the load did.
    private final class SlicesBox: @unchecked Sendable {
        private let state = Mutex<[ModelUsage]>([])
        var current: [ModelUsage] {
            get { state.withLock { $0 } }
            set { state.withLock { $0 = newValue } }
        }
    }

    /// A SlicesBox that also counts its calls.
    private final class SlicesCounter: @unchecked Sendable {
        private let state = Mutex<(rows: [ModelUsage], calls: Int)>(([], 0))
        var current: [ModelUsage] {
            get { state.withLock { $0.rows } }
            set { state.withLock { $0.rows = newValue } }
        }
        var calls: Int { state.withLock { $0.calls } }
        func call() -> [ModelUsage] {
            state.withLock { $0.calls += 1; return $0.rows }
        }
    }

    @Test func aNewUsageStampReloadsTheSlicesOnce() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("a", "A", "a", creator: "openai", intelligence: 80),
        ])
        let counter = SlicesCounter()
        counter.current = [slice("a", "claude", tokens: 100)]
        let model = ModelsModel(store: ArtificialAnalysisStore(directory: dir.path("aa")),
                                keyReader: { "key" }, transport: StubHTTP(),
                                linksURL: dir.path("config/model-links.json"),
                                slices: { _ in counter.call() },
                                now: { Date(timeIntervalSince1970: 1_800_000_000) },
                                calendar: Calendar(identifier: .gregorian))
        await model.load()
        #expect(counter.calls == 1)

        // The ledger re-scanned: the stamp moving reloads just the
        // usage, not the AA data.
        counter.current = [slice("a", "claude", tokens: 250)]
        await model.loadIfNeeded(usageStamp: 1)
        #expect(counter.calls == 2)
        #expect(model.rows.filter(\.used).first?.tokens == 250)

        // Same stamp: nothing moved, nothing re-read.
        counter.current = [slice("a", "claude", tokens: 999)]
        await model.loadIfNeeded(usageStamp: 1)
        #expect(counter.calls == 2)
        #expect(model.rows.filter(\.used).first?.tokens == 250)
    }

    @Test func aResetHarnessFilterShowsAllUsageAgain() async throws {
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("a", "A", "a", creator: "openai", intelligence: 80),
            entry("b", "B", "b", creator: "anthropic", intelligence: 70),
        ])
        let box = SlicesBox()
        box.current = [slice("a", "codex", tokens: 100), slice("b", "claude", tokens: 50)]
        let model = ModelsModel(store: ArtificialAnalysisStore(directory: dir.path("aa")),
                                keyReader: { "key" }, transport: StubHTTP(),
                                linksURL: dir.path("config/model-links.json"),
                                slices: { _ in box.current },
                                now: { Date(timeIntervalSince1970: 1_800_000_000) },
                                calendar: Calendar(identifier: .gregorian))
        await model.load()
        model.set(harnessFilter: "codex")
        #expect(model.rows.filter(\.used).map(\.logged) == ["a"])
        // The new period has no codex rows: the filter drops back to All
        // and the claude usage shows instead of an empty page.
        box.current = [slice("b", "claude", tokens: 50)]
        await model.set(period: .week)
        #expect(model.harnessFilter == nil)
        #expect(model.rows.filter(\.used).map(\.logged) == ["b"])
    }

    @Test func theMergedRowIsWhatTheHoverCardReuses() async throws {
        // Two logged names fold into one row; the card gets that row, not
        // a card(for:) resolve of the dominant name, which would lose the
        // other alias's tokens and harness.
        let dir = try TempDir()
        try writeLLMs(dir, [
            entry("grok", "Grok 4.7 (xhigh)", "grok-4-7-xhigh",
                  creator: "xai", intelligence: 46),
        ])
        let model = makeModel(dir: dir, slices: [
            slice("grok-4.7", "grok", tokens: 100),
            slice("grok-4.7-fast", "cursor", tokens: 50),
        ])
        await model.load()
        let row = try #require(model.rows.first { $0.used })
        let resolved = model.card(for: row.logged)
        #expect(row.tokens == 150)
        #expect(row.harnesses == ["grok", "cursor"])
        #expect(resolved.tokens == 100)
        #expect(resolved.harnesses == ["grok"])
    }
}
