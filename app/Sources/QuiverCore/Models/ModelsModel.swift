import Foundation
import Observation

/// The Models page: benchmark data from Artificial Analysis crossed with
/// the ledger's per-model usage. The store owns the downloaded bytes; the
/// matcher joins logged names to AA entries; the ledger says which models
/// the user actually ran in the period.
@MainActor
@Observable
public final class ModelsModel {
    /// Chart metric: what each bar's height and the sort show.
    public enum Metric: String, CaseIterable, Identifiable, Sendable {
        case intelligence, coding, reasoning, speed, ttft, cost

        public var id: String { rawValue }

        public var title: String {
            switch self {
            case .intelligence: "Intelligence"
            case .coding: "Coding"
            case .reasoning: "Reasoning"
            case .speed: "Speed"
            case .ttft: "Time to first token"
            case .cost: "AA blended cost"
            }
        }

        /// The card title over the chart.
        public var chartTitle: String {
            switch self {
            case .intelligence: "Intelligence index"
            case .coding: "Terminal-Bench 4.0"
            case .reasoning: "Humanity's Last Exam"
            case .speed: "Output speed"
            case .ttft: "Time to first token"
            case .cost: "Artificial Analysis blended cost per 1M tokens"
            }
        }

        /// The card subtitle under the chart title.
        public var detail: String {
            switch self {
            case .intelligence:
                "Artificial Analysis Intelligence Index: a blend of their independent evaluations (reasoning, knowledge, coding, agentic tasks)."
            case .coding: "Agentic coding and terminal tasks passed, run by Artificial Analysis. Replaces their Coding Index, which they stopped computing for new models in September 2026."
            case .reasoning: "Expert-level questions answered correctly, about 40% of them maths, run by Artificial Analysis. Replaces their Maths Index, which they retired in January 2026."
            case .speed: "Median output tokens per second across providers."
            case .ttft: "Median seconds to the first token, lower is better."
            case .cost: "Artificial Analysis's blended price per 1M tokens (3:1 input to output), lower is better. The table's Cost column is the LiteLLM price the ledger charges."
            }
        }

        /// Whether a bigger number is the better bar (the sort runs the
        /// other way for TTFT and cost).
        public var higherIsBetter: Bool {
            switch self {
            case .intelligence, .coding, .reasoning, .speed: true
            case .ttft, .cost: false
            }
        }

        /// The value as the bar and table show it.
        public func format(_ value: Double) -> String {
            switch self {
            case .intelligence, .speed:
                String(Int(value.rounded()))
            case .coding, .reasoning:
                "\(Int(value.rounded()))%"
            case .ttft:
                String(format: "%.2fs", value)
            case .cost:
                UsageFormat.dollars(value)
            }
        }

        /// The AA evaluation key or plain field this metric reads.
        public func value(of model: AALanguageModel) -> Double? {
            switch self {
            case .intelligence: model.intelligence
            case .coding: model.coding
            case .reasoning: model.reasoning
            case .speed: model.speed
            case .ttft: model.timeToFirstToken
            case .cost: model.priceBlended
            }
        }

        /// Why a model has no value, for the dash's hover note, where AA
        /// skips this evaluation on some models.
        public var missingNote: String? {
            switch self {
            case .coding: AALanguageModel.noCodingScore
            case .reasoning: AALanguageModel.noReasoningScore
            case .intelligence, .speed, .ttft, .cost: nil
            }
        }
    }

    /// Which models the chart and table list while no search is running.
    /// A query replaces it: searching reads the whole catalog instead.
    public enum Scope: String, CaseIterable, Identifiable, Sendable {
        case yoursPlusTop, yours, top25

        public var id: String { rawValue }

        public var title: String {
            switch self {
            case .yoursPlusTop: "Your models + top 10"
            case .yours: "Your models"
            case .top25: "Top 25"
            }
        }
    }

    /// One bar or table row: an AA model, plus the user's numbers when
    /// they ran it in the period.
    public struct Row: Sendable, Equatable, Identifiable {
        public var id: String { aa?.id ?? "unmatched:\(logged)" }
        /// The ledger's name for it; equals the AA name once matched.
        public var logged: String
        public var aa: AALanguageModel?
        /// The harnesses that ran it in the period, most tokens first.
        public var harnesses: [String] = []
        /// The period's tokens, 0 for a leaderboard row nobody ran.
        public var tokens = 0
        /// The metric's value, for the bar height and sort.
        public var value: Double?
        /// Cost rank 0…1 across every priced AA model.
        public var costPercentile: Double?
        /// How the user ran it, when the logged name said: "High",
        /// "Fast", "High · Fast". The bar's scores are the foundation
        /// model's regardless.
        public var qualifier: String?
        /// The per-token price the Cost column shows, from LiteLLM (or
        /// the user's own or Quiver's price), unlike `aa`'s blend.
        public var listPrice: ListPrice?

        public var used: Bool { tokens > 0 }
    }

    /// A model's list price, USD per 1M tokens of each kind. For a model
    /// the user ran it is the entry the ledger charges for the logged name;
    /// for a leaderboard row, the model's main LiteLLM key (a bare name
    /// before a provider-prefixed one, then the shortest).
    public struct ListPrice: Sendable, Equatable {
        /// The price list key: `claude-opus-5-5`, `xai/grok-4.20-0309`.
        public var key: String
        public var source: PricingCatalog.Source
        public var input: Double
        public var output: Double
        public var cacheRead: Double
        public var cacheWrite: Double

        public init(key: String, source: PricingCatalog.Source, input: Double, output: Double,
                    cacheRead: Double, cacheWrite: Double) {
            self.key = key
            self.source = source
            self.input = input
            self.output = output
            self.cacheRead = cacheRead
            self.cacheWrite = cacheWrite
        }
    }

    /// A used model the matcher couldn't place, for the warning popover.
    public struct Unmatched: Sendable, Equatable, Identifiable {
        public var id: String { model }
        public var model: String
        public var tokens: Int
    }

    public var metric: Metric = .intelligence
    public var scope: Scope = .yoursPlusTop
    public var period: OverviewPeriod = .month
    /// The harnesses whose models show; empty means every harness. While
    /// any is picked, only models those harnesses ran are rows: the
    /// leaderboard padding is nobody's usage, so it drops out. Under a
    /// search the filter still scopes the usage numbers but no longer
    /// limits which catalog rows show. Not persisted.
    public var harnessFilter: Set<String> = []
    /// The search box's text. A non-empty value swaps the scoped board
    /// for a catalog-wide match: every AA family a name matches, plus
    /// LiteLLM price keys no AA model claims. Not persisted. Keystrokes
    /// coalesce through `searchDelay` so typing never waits on a rebuild.
    public var query = "" {
        didSet {
            guard Self.normalized(query) != Self.normalized(oldValue) else { return }
            if searchDelay == .zero {
                rebuild()
            } else {
                scheduleSearch()
            }
        }
    }
    /// Whether the query is driving the row list — the scope picker's
    /// population (yours, top 10, top 25) no longer applies.
    public var searching: Bool { !Self.normalized(query).isEmpty }
    /// LiteLLM-only matches dropped by `searchLimit`, for the page's
    /// "+N more" note.
    public private(set) var searchOverflow = 0
    /// LiteLLM-only rows a search lists before the rest count as
    /// overflow: bare-name hits rank first.
    static let searchLimit = 60
    /// Whether the chart or the table shows.
    public enum ViewMode: String, Sendable { case chart, table }
    public var viewMode: ViewMode = .chart

    public private(set) var rows: [Row] = []
    public private(set) var harnessOptions: [String] = []
    public private(set) var unmatched: [Unmatched] = []
    /// Every AA model, for the link sheet's searchable list and the
    /// page's own search.
    public private(set) var allModels: [AALanguageModel] = [] {
        didSet {
            matcherInputsChanged()
            modelsByID = allModels.reduce(into: [:]) { $0[$1.id] = $1 }
            aaHay = allModels.map {
                (model: $0, hay: [Self.normalized($0.name), Self.normalized($0.slug),
                                  Self.normalized($0.creatorName),
                                  Self.normalized($0.creatorSlug)])
            }
            costPercentiles = Self.costRanks(in: allModels)
        }
    }
    public private(set) var isLoading = false
    public private(set) var isRefreshing = false
    /// When the models feed last landed.
    public private(set) var updatedAt: Date?
    /// The last refresh's message, for the reload button's hover help.
    public private(set) var refreshError: String?
    /// Whether an API key is saved; drives the empty state.
    public private(set) var hasKey = false

    private let store: ArtificialAnalysisStore
    private let keyReader: @Sendable () async -> String?
    private let transport: any HTTPTransport
    /// `~/.quiver/config/model-links.json`.
    private let linksURL: URL
    private var links: [String: String] = [:] { didSet { matcherInputsChanged() } }
    /// The period's per-model-per-harness usage; overridable in tests.
    private let slices: @Sendable (DateInterval) async -> [ModelUsage]
    /// The price catalog the ledger charges with; nil leaves used rows
    /// to the catalog join like leaderboard rows.
    private let pricing: @Sendable () async -> PricingCatalog?
    private var pricingCatalog: PricingCatalog?
    /// Current LiteLLM rows per AA model id, from the catalog join.
    private var catalogPrices: [String: [CatalogDatabase.PriceRow]] = [:] {
        didSet {
            catalogHay = catalogPrices
                .map { (id: $0.key, hay: $0.value.map { Self.normalized($0.key) }) }
                .sorted { $0.id < $1.id }
        }
    }
    /// Current LiteLLM rows no alias links to an AA model: the search's
    /// price-only side.
    private var unmatchedPrices: [CatalogDatabase.PriceRow] = [] {
        didSet {
            liteHay = unmatchedPrices.map {
                (row: $0, hay: Self.normalized($0.key),
                 bare: Self.normalized(PricingCatalog.bareName($0.key)))
            }
        }
    }
    /// The parse index over `allModels` and `links`, built once and
    /// reused across per-keystroke rebuilds; nilled when either changes.
    private var matchIndex: ModelMatcher.Index?
    /// `match` and `parse` answers by name: both run a dozen regexes per
    /// call, which a per-keystroke search felt. `matchCache` drops with
    /// `matchIndex` since links and `allModels` decide it; a stored nil
    /// still means "checked, no match". `parse` is a pure function of
    /// the name, so `parsedCache` never expires.
    private var matchCache: [String: (model: AALanguageModel, linked: Bool)?] = [:]
    private var parsedCache: [String: ModelMatcher.Parsed] = [:]
    /// The lookup tables `allModels`, `catalogPrices` and
    /// `unmatchedPrices` fill once: the id join, the normalized
    /// haystacks a query scans, and the percentile every row reads.
    private var modelsByID: [String: AALanguageModel] = [:]
    private var aaHay: [(model: AALanguageModel, hay: [String])] = []
    private var catalogHay: [(id: String, hay: [String])] = []
    private var liteHay: [(row: CatalogDatabase.PriceRow, hay: String, bare: String)] = []
    private var costPercentiles: [String: Double] = [:]
    /// Typing coalesces: each keystroke re-arms this instead of
    /// rebuilding, so a fast typist waits for one rebuild, not seven.
    private var searchTask: Task<Void, Never>?
    private let searchDelay: Duration
    private let now: () -> Date
    private let calendar: Calendar
    /// The usage the last load saw, kept so the scope/metric pickers can
    /// rebuild rows without hitting the ledger again.
    private var periodSlices: [ModelUsage] = []
    /// Bumped by every load, so a newer load supersedes an older one's
    /// remaining steps. A usage reload never bumps it: a ledger re-scan
    /// landing mid-load must not cancel the load's key check and reads.
    private var loadGeneration = 0
    /// Bumped by every usage read, a load's or a reload's, so a slower
    /// older answer cannot write its slices over a newer one's.
    private var usageGeneration = 0
    /// Whether a load has completed; the hover card's `loadIfNeeded`
    /// reads it so hovering never re-runs the fetch, and the page shows a
    /// loading card until then (`hasKey` reads false before the first
    /// key check).
    public private(set) var hasLoaded = false
    /// The benchmarks are on screen but the first usage read is still
    /// out: token cells show a spinner rather than "—", and an empty
    /// board says it is reading rather than "No usage".
    public private(set) var usagePending = false
    /// The overview scan counter the last usage read saw, so a ledger
    /// re-scan can flag the slices as stale without re-fetching AA data.
    private var loadedUsageStamp: Int?
    /// The newest scan counter the page has passed in; a load marks only
    /// the stamp it saw before its read as loaded.
    private var requestedUsageStamp: Int?

    public init(store: ArtificialAnalysisStore,
                keyReader: @escaping @Sendable () async -> String?,
                transport: any HTTPTransport,
                linksURL: URL,
                slices: (@Sendable (DateInterval) async -> [ModelUsage])?,
                pricing: @escaping @Sendable () async -> PricingCatalog? = { nil },
                now: @escaping () -> Date = Date.init, calendar: Calendar = .current,
                searchDelay: Duration = .milliseconds(150)) {
        self.store = store
        self.keyReader = keyReader
        self.transport = transport
        self.linksURL = linksURL
        self.slices = slices ?? { _ in [] }
        self.pricing = pricing
        self.now = now
        self.calendar = calendar
        self.searchDelay = searchDelay
    }

    /// Loads the stored AA data and the period's usage, then rebuilds.
    /// When a saved key exists and the models file is missing or older
    /// than a week, a background refresh runs first — unless
    /// `allowRefresh` is off, as in the hover card's cached read.
    public func load(allowRefresh: Bool = true) async {
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        // A superseded load leaves the flag to whichever load owns it.
        defer { if generation == loadGeneration { isLoading = false } }
        let key = await keyReader()
        guard generation == loadGeneration else { return }
        hasKey = key != nil
        if allowRefresh && hasKey && store.llmsIsStale() {
            // Unstructured, so leaving the page (which cancels the page's
            // task) can't cancel the fetches mid-way and record every
            // endpoint left as a "cancelled" failure.
            await Task { await refresh() }.value
            guard generation == loadGeneration else { return }
        }
        links = ModelMatcher.loadLinks(from: linksURL)
        allModels = store.languageModels() ?? []
        updatedAt = store.meta()[AAEndpoint.llms.rawValue]?.fetchedAt
            .map { Date(timeIntervalSince1970: $0) }
        await loadPrices()
        guard generation == loadGeneration else { return }
        if !hasLoaded {
            // Draw the leaderboard now; the usage read fills in tokens
            // and the user's own models when it lands.
            usagePending = true
            rebuild()
        }
        defer { if generation == loadGeneration { usagePending = false } }
        usageGeneration += 1
        let usage = usageGeneration
        let covered = requestedUsageStamp
        let fresh = await slices(period.range(now: now(), calendar: calendar))
        guard generation == loadGeneration else { return }
        // A reload that started after this read has the newer slices.
        if usage == usageGeneration { periodSlices = fresh }
        hasLoaded = true
        usagePending = false
        loadedUsageStamp = covered
        rebuild()
        // A scan that finished while the read was out isn't in it, and
        // the page won't pass that stamp again: read once more.
        if requestedUsageStamp != covered {
            loadedUsageStamp = requestedUsageStamp
            await reloadUsage()
        }
    }

    /// Re-reads the current period's usage slices and rebuilds — the AA
    /// data is untouched. Before the first load it runs that load.
    public func reloadUsage() async {
        guard hasLoaded else { await load(allowRefresh: false); return }
        usageGeneration += 1
        let usage = usageGeneration
        let fresh = await slices(period.range(now: now(), calendar: calendar))
        guard usage == usageGeneration else { return }
        periodSlices = fresh
        rebuild()
    }

    /// The hover card's read: the cached data only, and only once — a
    /// hover must never kick off the six-endpoint refresh. `usageStamp`
    /// is the overview's scan counter; a new value means the ledger
    /// re-scanned and the slices need a reload.
    public func loadIfNeeded(usageStamp: Int? = nil) async {
        if let usageStamp { requestedUsageStamp = usageStamp }
        if !hasLoaded {
            // A load in flight records the stamp its read covers, and
            // reads again if this one arrived after that read began.
            guard !isLoading else { return }
            await load(allowRefresh: false)
            return
        }
        if let usageStamp, usageStamp != loadedUsageStamp {
            loadedUsageStamp = usageStamp
            await reloadUsage()
        }
    }

    /// The toolbar's reload: fetches every endpoint now. A second call
    /// while one is running returns at once rather than doubling the
    /// requests.
    public func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        guard let key = await keyReader() else {
            hasKey = false
            return
        }
        hasKey = true
        let errors = await store.refresh(key: key, transport: transport)
        refreshError = errors[.llms] ?? errors.values.sorted().first
        links = ModelMatcher.loadLinks(from: linksURL)
        allModels = store.languageModels() ?? []
        updatedAt = store.meta()[AAEndpoint.llms.rawValue]?.fetchedAt
            .map { Date(timeIntervalSince1970: $0) }
        await loadPrices()
        rebuild()
    }

    /// The toolbar pickers set these; metric, scope and harness rebuild
    /// from the slices already in memory, period refetches them.
    public func set(metric value: Metric) { metric = value; rebuild() }
    public func set(scope value: Scope) { scope = value; rebuild() }
    public func set(harnessFilter value: Set<String>) { harnessFilter = value; rebuild() }
    /// Adds or removes one harness from the filter.
    public func toggle(harness: String) {
        if harnessFilter.contains(harness) { harnessFilter.remove(harness) } else { harnessFilter.insert(harness) }
        rebuild()
    }
    public func set(viewMode value: ViewMode) { viewMode = value }

    public func set(period value: OverviewPeriod) async {
        period = value
        // Before the first load lands, superseding it would leave the
        // benchmarks unread; run the whole load for the new period.
        guard hasLoaded else { await load(); return }
        await reloadUsage()
    }

    /// Saves a manual link for an unmatched logged model, then rebuilds.
    public func link(logged: String, to aaID: String) throws {
        var next = links
        next[ModelMatcher.parse(logged).base] = aaID
        try ModelMatcher.saveLinks(next, to: linksURL)
        links = next
        rebuild()
        // The catalog's price-key aliases follow the links file.
        let database = store.database
        Task.detached { database.rebuildAliases() }
    }

    /// The matcher answer for a logged name, for hover cards elsewhere.
    public func resolve(_ logged: String) -> AALanguageModel? {
        cachedMatch(logged)?.model
    }

    /// A logged name's card data for other pages' hovers: the AA model it
    /// matched, its percentile, and the period's tokens and harnesses.
    public func card(for logged: String) -> Row {
        let aa = resolve(logged)
        let slices = periodSlices.filter { $0.model == logged }
        var harnesses: [String] = []
        var tokens = 0
        for slice in slices.sorted(by: { $0.totals.totalTokens > $1.totals.totalTokens }) {
            if !harnesses.contains(slice.harness) { harnesses.append(slice.harness) }
            tokens += slice.totals.totalTokens
        }
        return Row(logged: logged, aa: aa, harnesses: harnesses, tokens: tokens,
                   value: aa.flatMap { metric.value(of: $0) },
                   costPercentile: aa.flatMap { costPercentiles[$0.id] },
                   qualifier: qualifier(parsed(logged)),
                   listPrice: listPrice(logged: logged, aa: aa))
    }

    /// Reads the ledger's catalog and the catalog join's current prices,
    /// both halves: the keys an AA model claims and the LiteLLM-only
    /// tail the search lists on its own.
    private func loadPrices() async {
        pricingCatalog = await pricing()
        let database = store.database
        let prices = await Task.detached {
            (models: database.currentPricesByModel(),
             unmatched: database.currentPricesUnmatched())
        }.value
        catalogPrices = prices.models
        unmatchedPrices = prices.unmatched
    }

    /// The list price a row shows: the ledger's entry for `logged` when it
    /// prices that name, else `aa`'s main key in the catalog join.
    func listPrice(logged: String?, aa: AALanguageModel?) -> ListPrice? {
        if let logged, let pricingCatalog,
           case .priced(let rates, let entry, let source, _) = pricingCatalog.resolve(model: logged, tier: "") {
            return ListPrice(key: entry, source: source, input: rates.input * 1e6, output: rates.output * 1e6,
                             cacheRead: rates.cacheRead * 1e6, cacheWrite: rates.cacheWrite * 1e6)
        }
        guard let aa, let rows = catalogPrices[aa.id] else { return nil }
        func rank(_ row: CatalogDatabase.PriceRow) -> (Int, Int, String) {
            (row.key.contains("/") ? 1 : 0, row.key.count, row.key)
        }
        guard let best = rows.min(by: { rank($0) < rank($1) }) else { return nil }
        return ListPrice(key: best.key, source: .liteLLM, input: best.input * 1e6, output: best.output * 1e6,
                         cacheRead: best.cacheRead * 1e6, cacheWrite: best.cacheWrite * 1e6)
    }

    /// The parse of `allModels` and `links` `match` calls share; built on
    /// first use and dropped when either input changes.
    private var matcherIndex: ModelMatcher.Index? {
        if matchIndex == nil, !allModels.isEmpty {
            matchIndex = ModelMatcher.Index(models: allModels, links: links)
        }
        return matchIndex
    }

    /// `match` through `matchCache`: the index's parse runs once per
    /// name, then the answer is a dictionary read.
    private func cachedMatch(_ name: String) -> (model: AALanguageModel, linked: Bool)? {
        if let cached = matchCache[name] { return cached }
        let result = matcherIndex?.match(name)
        matchCache[name] = result
        return result
    }

    /// `ModelMatcher.parse` memoized for `qualifier`.
    private func parsed(_ name: String) -> ModelMatcher.Parsed {
        if let hit = parsedCache[name] { return hit }
        let hit = ModelMatcher.parse(name)
        parsedCache[name] = hit
        return hit
    }

    /// `allModels` or `links` changed: every matcher-derived answer goes.
    private func matcherInputsChanged() {
        matchIndex = nil
        matchCache = [:]
    }

    /// Rebuild once the typist pauses; a fresh keystroke re-arms.
    private func scheduleSearch() {
        searchTask?.cancel()
        searchTask = Task {
            try? await Task.sleep(for: searchDelay)
            guard !Task.isCancelled else { return }
            rebuild()
        }
    }

    /// Waits out a pending coalesced rebuild: tests await the rows a
    /// typed query produces without racing the delay.
    func settleSearch() async {
        await searchTask?.value
    }

    /// The effort and mode words a parsed name carried, display-cased.
    private func qualifier(_ parsed: ModelMatcher.Parsed) -> String? {
        var words: [String] = []
        if parsed.thinking { words.append("Thinking") }
        if let effort = parsed.effort { words.append(effort.prefix(1).uppercased() + effort.dropFirst()) }
        if parsed.fast { words.append("Fast") }
        return words.isEmpty ? nil : words.joined(separator: " · ")
    }

    /// Rebuilds rows from `periodSlices`, `allModels` and `links`.
    private func rebuild() {
        // The filter menu counts every harness in the period, whether or
        // not the filter lets it through.
        var harnessTokens: [String: Int] = [:]
        for slice in periodSlices {
            harnessTokens[slice.harness, default: 0] += slice.totals.totalTokens
        }
        harnessOptions = harnessTokens
            .sorted { ($0.value, $0.key) > ($1.value, $1.key) }
            .map(\.key)
        // A harness that left the period drops out of the filter, before
        // the rows are filtered: once none is left, everything shows again.
        // Not before the first usage read, which would drop them all.
        if !usagePending {
            let live = harnessFilter.filter { harnessTokens[$0] != nil }
            if live != harnessFilter { harnessFilter = live }
        }

        // Per logged model: total tokens and the harnesses that ran it.
        var byModel: [String: (tokens: Int, harnesses: [String], byHarness: [String: Int])] = [:]
        for slice in periodSlices {
            guard harnessFilter.isEmpty || harnessFilter.contains(slice.harness) else { continue }
            var entry = byModel[slice.model] ?? (0, [], [:])
            entry.tokens += slice.totals.totalTokens
            if !entry.harnesses.contains(slice.harness) { entry.harnesses.append(slice.harness) }
            entry.byHarness[slice.harness, default: 0] += slice.totals.totalTokens
            byModel[slice.model] = entry
        }

        // Order each model's harnesses by its tokens under them.
        for (model, entry) in byModel {
            byModel[model]?.harnesses = entry.harnesses
                .sorted { (entry.byHarness[$0] ?? 0) > (entry.byHarness[$1] ?? 0) }
        }

        // Names that resolve to the same AA model merge into one row —
        // "grok-4.7" and "grok-4.7-high-fast" are one bar, not two. The
        // row keeps the biggest contributor's logged name and qualifier.
        var used: [Row] = []
        var mergedIndex: [String: Int] = [:]
        var mergedHarness: [String: [String: Int]] = [:]
        var dominantTokens: [String: Int] = [:]
        var stillUnmatched: [Unmatched] = []
        var matchedIDs: Set<String> = []
        // Per AA model, the logged name with the most tokens: its ledger
        // price is the row's list price.
        var pricedNames: [String: String] = [:]
        // One match answer per name: `ModelMatcher.match` re-parses the
        // name's dozen regexes per call, which held the main thread over a
        // second with a month of usage. `cachedMatch` reads its index and
        // memoizes; `didSet` on `allModels` or `links` drops both.
        // Sorted, so which alias names a merged row (on a token tie) and
        // the order rows enter the sort don't follow dictionary order.
        for (logged, entry) in byModel.sorted(by: { $0.key < $1.key }) {
            let aa = cachedMatch(logged)?.model
            let parsedName = parsed(logged)
            guard let aa else {
                // Unmatched names only surface in the warning popover.
                stillUnmatched.append(Unmatched(model: logged, tokens: entry.tokens))
                continue
            }
            matchedIDs.insert(aa.id)
            if let index = mergedIndex[aa.id] {
                used[index].tokens += entry.tokens
                for (harness, tokens) in entry.byHarness {
                    mergedHarness[aa.id, default: [:]][harness, default: 0] += tokens
                }
                if entry.tokens > 0, entry.tokens > dominantTokens[aa.id] ?? 0 {
                    dominantTokens[aa.id] = entry.tokens
                    pricedNames[aa.id] = logged
                    used[index].logged = logged
                    used[index].qualifier = qualifier(parsedName)
                }
            } else {
                mergedIndex[aa.id] = used.count
                dominantTokens[aa.id] = entry.tokens
                mergedHarness[aa.id] = entry.byHarness
                used.append(Row(logged: logged, aa: aa, harnesses: entry.harnesses,
                                tokens: entry.tokens,
                                value: metric.value(of: aa),
                                costPercentile: costPercentiles[aa.id],
                                qualifier: qualifier(parsedName)))
                pricedNames[aa.id] = logged
            }
        }
        for (id, byHarness) in mergedHarness {
            if let index = mergedIndex[id] {
                used[index].harnesses = byHarness
                    .sorted { ($0.value, $0.key) > ($1.value, $1.key) }.map(\.key)
            }
        }
        for index in used.indices {
            used[index].listPrice = listPrice(logged: used[index].aa.flatMap { pricedNames[$0.id] },
                                              aa: used[index].aa)
        }
        unmatched = stillUnmatched.sorted { ($0.tokens, $0.model) > ($1.tokens, $1.model) }

        func leaderboardRow(_ model: AALanguageModel) -> Row {
            used.first(where: { $0.aa?.id == model.id })
                ?? Row(logged: model.name, aa: model,
                       value: metric.value(of: model),
                       costPercentile: costPercentiles[model.id],
                       listPrice: listPrice(logged: nil, aa: model))
        }

        // A query swaps the scoped board for a catalog-wide match: every
        // AA family a name, slug or lab hits — one headline row each,
        // reusing the used row when it ran — then LiteLLM price keys no
        // AA model claims. `searching` is what the toolbar reads. Every
        // haystack below was normalized once, so a keystroke only pays
        // substring scans and dictionary reads.
        let needle = Self.normalized(query)
        if !needle.isEmpty {
            var seen: Set<String> = []
            var pool: [Row] = []
            for (model, hay) in aaHay where hay.contains(where: { $0.contains(needle) }) {
                let headline = cachedMatch(model.name)?.model ?? model
                guard seen.insert(headline.id).inserted else { continue }
                pool.append(leaderboardRow(headline))
            }
            // A price key can name an AA model its name can't reach:
            // "moonshot/kimi-k3" finds Kimi K3 through the alias join.
            for (id, hay) in catalogHay
            where hay.contains(where: { $0.contains(needle) }) {
                guard let entry = modelsByID[id] else { continue }
                let headline = cachedMatch(entry.name)?.model ?? entry
                guard seen.insert(headline.id).inserted else { continue }
                pool.append(leaderboardRow(headline))
            }
            // A logged name can match while its AA name doesn't: a
            // manual link, a proxy alias, or a merged family's
            // non-dominant name — check every name the period logged,
            // not only the one that ended up on the row.
            for logged in byModel.keys where Self.matches(needle, logged) {
                guard let matched = cachedMatch(logged),
                      seen.insert(matched.model.id).inserted,
                      let merged = mergedIndex[matched.model.id] else { continue }
                pool.append(used[merged])
            }
            var result = Self.sort(pool, by: metric)
            // Used names no AA entry covers are rows too when they match.
            for un in unmatched where Self.matches(needle, un.model) {
                guard seen.insert("unmatched:\(un.model)").inserted else { continue }
                result.append(Row(logged: un.model, aa: nil,
                                  harnesses: byModel[un.model]?.harnesses ?? [],
                                  tokens: un.tokens,
                                  listPrice: listPrice(logged: un.model, aa: nil)))
            }
            // The LiteLLM-only tail, capped; the bare name decides which
            // hits lead, the overflow count becomes the page's footnote.
            // Keys already shown — as a usage row or through the alias
            // join — don't count toward either side.
            let lite = liteHay
                .filter { $0.hay.contains(needle) }
                .filter { !seen.contains("unmatched:\($0.row.key)")
                          && cachedMatch($0.row.key) == nil }
                .sorted { Self.liteRank(needle, $0) < Self.liteRank(needle, $1) }
            searchOverflow = max(0, lite.count - Self.searchLimit)
            for hit in lite.prefix(Self.searchLimit) {
                let price = hit.row
                result.append(Row(logged: price.key, aa: nil,
                                  listPrice: listPrice(logged: price.key, aa: nil)
                                      ?? ListPrice(key: price.key, source: .liteLLM,
                                                   input: price.input * 1e6,
                                                   output: price.output * 1e6,
                                                   cacheRead: price.cacheRead * 1e6,
                                                   cacheWrite: price.cacheWrite * 1e6)))
            }
            rows = result
            return
        }
        searchOverflow = 0

        let all: [Row]
        switch scope {
        case .yoursPlusTop:
            // The real top ten, minus the ones already in `used`. Under a
            // harness filter the page lists what those harnesses ran: a
            // leader nobody ran there is no answer to "what does Codex use".
            all = harnessFilter.isEmpty
                ? used + topAA(excluding: [], count: 10)
                    .filter { !matchedIDs.contains($0.id) }.map(leaderboardRow)
                : used
        case .yours:
            all = used
        case .top25:
            // Leaders only: `leaderboardRow` still reuses the used row
            // for a model on the board, so its tokens stay marked. Under
            // a harness filter, only the leaders those harnesses ran.
            let board = topAA(excluding: [], count: 25).map(leaderboardRow)
            // Membership, not `used`: a run can log cost with no tokens.
            all = harnessFilter.isEmpty ? board : board.filter { matchedIDs.contains($0.id) }
        }
        rows = Self.sort(all, by: metric)
    }

    /// AA's leaders under the current metric, best first: one entry per
    /// foundation model, the same entry a logged name resolves to.
    private func topAA(excluding matched: Set<String>, count: Int) -> [AALanguageModel] {
        ModelMatcher.headlines(in: allModels)
            .filter { !matched.contains($0.id) }
            .filter { metric.value(of: $0) != nil }
            .sorted { a, b in
                let av = metric.value(of: a) ?? 0, bv = metric.value(of: b) ?? 0
                return metric.higherIsBetter ? av > bv : av < bv
            }
            .prefix(count)
            .map { $0 }
    }

    /// Best first: descending for the indexes and speed, ascending for
    /// TTFT and cost; rows with no value sit last, used before unused.
    /// Equal values fall back to tokens then name, so tied bars keep
    /// their places across rebuilds instead of trading them.
    public static func sort(_ rows: [Row], by metric: Metric) -> [Row] {
        rows.sorted { a, b in
            switch (a.value, b.value) {
            case let (av?, bv?):
                if av == bv { return (a.tokens, a.logged) > (b.tokens, b.logged) }
                return metric.higherIsBetter ? av > bv : av < bv
            case (_?, nil): return true
            case (nil, _?): return false
            case (nil, nil):
                return (a.tokens, a.logged) > (b.tokens, b.logged)
            }
        }
    }

    /// Search compares the alphanumeric skeleton: "kimi k3" hits
    /// "Kimi K3", "kimi-k3" and "us.moonshotai.kimi-k3" alike.
    static func normalized(_ name: String) -> String {
        name.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    /// A logged name or LiteLLM key matches anywhere, provider prefix
    /// and reseller tags included.
    private static func matches(_ needle: String, _ key: String) -> Bool {
        normalized(key).contains(needle)
    }

    /// LiteLLM-only hits rank a bare-name exact match first, then a
    /// bare-name prefix, then anything the key merely contains; shorter
    /// keys lead inside each so "kimi-k3" beats "vendor/kimi-k3-eu". The
    /// caller's `liteHay` carried the normalized forms already.
    private static func liteRank(
        _ needle: String,
        _ hit: (row: CatalogDatabase.PriceRow, hay: String, bare: String)
    ) -> (Int, Int, String) {
        let group = hit.bare == needle ? 0 : hit.bare.hasPrefix(needle) ? 1
            : hit.bare.contains(needle) ? 2 : 3
        return (group, hit.row.key.count, hit.row.key)
    }

    /// Every priced model's percentile against the rest, from one pass
    /// over the sorted prices; `CostPercentile.rank` rescans the whole
    /// list per row, which a per-keystroke rebuild felt.
    private static func costRanks(in models: [AALanguageModel]) -> [String: Double] {
        let priced = models.compactMap(\.priceBlended).filter { $0 > 0 }.sorted()
        guard priced.count > 1 else { return [:] }
        // First index the predicate stops holding: `< price` counts the
        // cheaper, `<= price` the equal run's end.
        func bound(_ pred: (Double) -> Bool) -> Int {
            var lo = priced.startIndex, hi = priced.endIndex
            while lo < hi {
                let mid = lo + (hi - lo) / 2
                if pred(priced[mid]) { lo = mid + 1 } else { hi = mid }
            }
            return lo
        }
        var ranks: [String: Double] = [:]
        for model in models {
            guard let price = model.priceBlended, price > 0 else { continue }
            let lower = bound { $0 < price }
            let equal = bound { $0 <= price } - lower - 1
            ranks[model.id] = min(max((Double(lower) + 0.5 * Double(equal))
                                      / Double(priced.count - 1), 0), 1)
        }
        return ranks
    }
}

extension ModelsModel.Row {
    /// The lab behind the model, from `ModelBrands`: the AA creator once
    /// matched, else the logged name.
    public var vendor: ModelVendor {
        aa.map { ModelVendor.of($0) } ?? ModelVendor.of(logged: logged)
    }
}
