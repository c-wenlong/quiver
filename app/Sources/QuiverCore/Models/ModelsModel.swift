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
        case intelligence, coding, maths, speed, ttft, cost

        public var id: String { rawValue }

        public var title: String {
            switch self {
            case .intelligence: "Intelligence"
            case .coding: "Coding"
            case .maths: "Maths"
            case .speed: "Speed"
            case .ttft: "Time to first token"
            case .cost: "Cost"
            }
        }

        /// The card title over the chart.
        public var chartTitle: String {
            switch self {
            case .intelligence: "Intelligence index"
            case .coding: "Coding index"
            case .maths: "Maths index"
            case .speed: "Output speed"
            case .ttft: "Time to first token"
            case .cost: "Blended cost per 1M tokens"
            }
        }

        /// The card subtitle under the chart title.
        public var detail: String {
            switch self {
            case .intelligence:
                "Artificial Analysis Intelligence Index: a blend of their independent evaluations (reasoning, knowledge, coding, agentic tasks)."
            case .coding: "Artificial Analysis Coding Index."
            case .maths: "Artificial Analysis Maths Index."
            case .speed: "Median output tokens per second across providers."
            case .ttft: "Median seconds to the first token, lower is better."
            case .cost: "Blended price per 1M tokens (3:1 input to output), lower is better."
            }
        }

        /// Whether a bigger number is the better bar (the sort runs the
        /// other way for TTFT and cost).
        public var higherIsBetter: Bool {
            switch self {
            case .intelligence, .coding, .maths, .speed: true
            case .ttft, .cost: false
            }
        }

        /// The value as the bar and table show it.
        public func format(_ value: Double) -> String {
            switch self {
            case .intelligence, .coding, .maths, .speed:
                String(Int(value.rounded()))
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
            case .maths: model.maths
            case .speed: model.speed
            case .ttft: model.timeToFirstToken
            case .cost: model.priceBlended
            }
        }
    }

    /// Which models the chart and table list.
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
        /// "High" when the logged name carried an effort or a -fast mode.
        public var qualifier: String?

        public var used: Bool { tokens > 0 }
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
    /// Same filter as the Pricing page's; not persisted.
    public var harnessFilter: String?
    /// Whether the chart or the table shows.
    public enum ViewMode: String, Sendable { case chart, table }
    public var viewMode: ViewMode = .chart

    public private(set) var rows: [Row] = []
    public private(set) var harnessOptions: [String] = []
    public private(set) var unmatched: [Unmatched] = []
    /// Every AA model, for the link sheet's searchable list.
    public private(set) var allModels: [AALanguageModel] = []
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
    private var links: [String: String] = [:]
    /// The period's per-model-per-harness usage; overridable in tests.
    private let slices: @Sendable (DateInterval) async -> [ModelUsage]
    private let now: () -> Date
    private let calendar: Calendar
    /// The usage the last load saw, kept so the scope/metric pickers can
    /// rebuild rows without hitting the ledger again.
    private var periodSlices: [ModelUsage] = []

    public init(store: ArtificialAnalysisStore,
                keyReader: @escaping @Sendable () async -> String?,
                transport: any HTTPTransport,
                linksURL: URL,
                slices: (@Sendable (DateInterval) async -> [ModelUsage])?,
                now: @escaping () -> Date = Date.init, calendar: Calendar = .current) {
        self.store = store
        self.keyReader = keyReader
        self.transport = transport
        self.linksURL = linksURL
        self.slices = slices ?? { _ in [] }
        self.now = now
        self.calendar = calendar
    }

    /// Loads the stored AA data and the period's usage, then rebuilds.
    /// When a saved key exists and the models file is missing or older
    /// than a week, a background refresh runs first.
    public func load() async {
        isLoading = true
        defer { isLoading = false }
        hasKey = await keyReader() != nil
        if hasKey && store.llmsIsStale() {
            await refresh()
        }
        links = ModelMatcher.loadLinks(from: linksURL)
        allModels = store.languageModels() ?? []
        updatedAt = store.meta()[AAEndpoint.llms.rawValue]?.fetchedAt
            .map { Date(timeIntervalSince1970: $0) }
        let range = period.range(now: now(), calendar: calendar)
        periodSlices = await slices(range)
        rebuild()
    }

    /// The toolbar's reload: fetches every endpoint now.
    public func refresh() async {
        guard let key = await keyReader() else {
            hasKey = false
            return
        }
        hasKey = true
        isRefreshing = true
        defer { isRefreshing = false }
        let errors = await store.refresh(key: key, transport: transport)
        refreshError = errors[.llms] ?? errors.values.sorted().first
        links = ModelMatcher.loadLinks(from: linksURL)
        allModels = store.languageModels() ?? []
        updatedAt = store.meta()[AAEndpoint.llms.rawValue]?.fetchedAt
            .map { Date(timeIntervalSince1970: $0) }
        rebuild()
    }

    /// The toolbar pickers set these; metric, scope and harness rebuild
    /// from the slices already in memory, period refetches them.
    public func set(metric value: Metric) { metric = value; rebuild() }
    public func set(scope value: Scope) { scope = value; rebuild() }
    public func set(harnessFilter value: String?) { harnessFilter = value; rebuild() }
    public func set(viewMode value: ViewMode) { viewMode = value }

    public func set(period value: OverviewPeriod) async {
        period = value
        periodSlices = await slices(value.range(now: now(), calendar: calendar))
        rebuild()
    }

    /// Saves a manual link for an unmatched logged model, then rebuilds.
    public func link(logged: String, to aaID: String) throws {
        var next = links
        next[ModelMatcher.parse(logged).base] = aaID
        try ModelMatcher.saveLinks(next, to: linksURL)
        links = next
        rebuild()
    }

    /// The matcher answer for a logged name, for hover cards elsewhere.
    public func resolve(_ logged: String) -> AALanguageModel? {
        ModelMatcher.match(logged, in: allModels, links: links)
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
        let parsed = ModelMatcher.parse(logged)
        return Row(logged: logged, aa: aa, harnesses: harnesses, tokens: tokens,
                   value: aa.flatMap { metric.value(of: $0) },
                   costPercentile: aa.flatMap { CostPercentile.rank(of: $0, in: allModels) },
                   qualifier: qualifier(parsed))
    }

    /// The effort/mode word a parsed name carried, display-cased.
    private func qualifier(_ parsed: ModelMatcher.Parsed) -> String? {
        if parsed.fast { return "Fast" }
        guard let effort = parsed.effort else { return nil }
        return effort.prefix(1).uppercased() + effort.dropFirst()
    }

    /// Rebuilds rows from `periodSlices`, `allModels` and `links`.
    private func rebuild() {
        // Per logged model: total tokens and the harnesses that ran it.
        var byModel: [String: (tokens: Int, harnesses: [String], byHarness: [String: Int])] = [:]
        var harnessTokens: [String: Int] = [:]
        for slice in periodSlices {
            harnessTokens[slice.harness, default: 0] += slice.totals.totalTokens
            guard harnessFilter == nil || slice.harness == harnessFilter else { continue }
            var entry = byModel[slice.model] ?? (0, [], [:])
            entry.tokens += slice.totals.totalTokens
            if !entry.harnesses.contains(slice.harness) { entry.harnesses.append(slice.harness) }
            entry.byHarness[slice.harness, default: 0] += slice.totals.totalTokens
            byModel[slice.model] = entry
        }
        harnessOptions = harnessTokens
            .sorted { ($0.value, $0.key) > ($1.value, $1.key) }
            .map(\.key)
        if let harnessFilter, harnessTokens[harnessFilter] == nil { self.harnessFilter = nil }

        // Order each model's harnesses by its tokens under them.
        for (model, entry) in byModel {
            byModel[model]?.harnesses = entry.harnesses
                .sorted { (entry.byHarness[$0] ?? 0) > (entry.byHarness[$1] ?? 0) }
        }

        // Names that resolve to the same AA model merge into one row —
        // "grok-4.7" and "grok-4.7-fast" are one bar, not two. The row
        // keeps the biggest contributor's logged name and qualifier.
        var used: [Row] = []
        var mergedIndex: [String: Int] = [:]
        var mergedHarness: [String: [String: Int]] = [:]
        var dominantTokens: [String: Int] = [:]
        var stillUnmatched: [Unmatched] = []
        var matchedIDs: Set<String> = []
        for (logged, entry) in byModel {
            let aa = ModelMatcher.match(logged, in: allModels, links: links)
            let parsed = ModelMatcher.parse(logged)
            guard let aa else {
                stillUnmatched.append(Unmatched(model: logged, tokens: entry.tokens))
                used.append(Row(logged: logged, aa: nil, harnesses: entry.harnesses,
                                tokens: entry.tokens, value: nil, costPercentile: nil,
                                qualifier: qualifier(parsed)))
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
                    used[index].logged = logged
                    used[index].qualifier = qualifier(parsed)
                }
            } else {
                mergedIndex[aa.id] = used.count
                dominantTokens[aa.id] = entry.tokens
                mergedHarness[aa.id] = entry.byHarness
                used.append(Row(logged: logged, aa: aa, harnesses: entry.harnesses,
                                tokens: entry.tokens,
                                value: metric.value(of: aa),
                                costPercentile: CostPercentile.rank(of: aa, in: allModels),
                                qualifier: qualifier(parsed)))
            }
        }
        for (id, byHarness) in mergedHarness {
            if let index = mergedIndex[id] {
                used[index].harnesses = byHarness
                    .sorted { ($0.value, $0.key) > ($1.value, $1.key) }.map(\.key)
            }
        }
        unmatched = stillUnmatched.sorted { ($0.tokens, $0.model) > ($1.tokens, $1.model) }

        func leaderboardRow(_ model: AALanguageModel) -> Row {
            used.first(where: { $0.aa?.id == model.id })
                ?? Row(logged: model.name, aa: model,
                       value: metric.value(of: model),
                       costPercentile: CostPercentile.rank(of: model, in: allModels))
        }
        let all: [Row]
        switch scope {
        case .yoursPlusTop:
            all = used + topAA(excluding: matchedIDs, count: 10).map(leaderboardRow)
        case .yours:
            all = used
        case .top25:
            let board = topAA(excluding: [], count: 25)
            let boardIDs = Set(board.map(\.id))
            all = board.map(leaderboardRow) + used.filter { !boardIDs.contains($0.aa?.id ?? "") }
        }
        rows = Self.sort(all, by: metric)
    }

    /// AA's leaders under the current metric, best first.
    private func topAA(excluding matched: Set<String>, count: Int) -> [AALanguageModel] {
        allModels
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
    public static func sort(_ rows: [Row], by metric: Metric) -> [Row] {
        rows.sorted { a, b in
            switch (a.value, b.value) {
            case let (av?, bv?):
                if av == bv { return a.used && !b.used }
                return metric.higherIsBetter ? av > bv : av < bv
            case (_?, nil): return true
            case (nil, _?): return false
            case (nil, nil):
                return (a.tokens, a.logged) > (b.tokens, b.logged)
            }
        }
    }
}
