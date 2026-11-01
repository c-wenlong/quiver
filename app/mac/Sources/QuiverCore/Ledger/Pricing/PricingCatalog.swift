import Foundation

/// Model prices from a LiteLLM `model_prices_and_context_window.json` (the
/// bundled trimmed snapshot or a refreshed full copy) plus Quiver's
/// supplement. Looks a logged model name up and prices token counts by kind.
///
/// Lookup is exact after normalizing, never a substring match, so an unknown
/// model stays unpriced instead of borrowing a neighbour's price. A name is
/// tried as logged, lower-cased, through the supplement's aliases, and with
/// these stripped, ccusage-style: a provider prefix (`anthropic/`,
/// `us.anthropic.`), a Bedrock version (`-v1:0`), a context tag (`[1m]`), a
/// date snapshot (`-20250929`, `-2026-09-30`, `@20250514`), and `.` versus
/// `-` in version numbers (`claude-opus-4.6`).
public struct PricingCatalog: Sendable {
    /// Where a model's price came from: a user override, Quiver's
    /// supplement, or the LiteLLM list.
    public enum Source: String, Sendable {
        case yours, quiver, liteLLM
    }

    /// What a logged model name resolves to. The ledger and the Pricing
    /// page both decide through `resolve`, so they can never disagree.
    public enum Resolution: Sendable, Equatable {
        /// A catalog entry prices it: the entry's rates, key and source,
        /// and the tier the model carried (`claude-opus-5-5-high-fast`
        /// resolves to the base entry with tier "fast", which `cost(tier:)`
        /// then prices at the tier's explicit rates or multiplier).
        case priced(rates: ModelRates, entry: String, source: Source, tier: String)
        /// A free model: priced at zero, no catalog entry needed.
        case free
    }

    private var entries: [String: ModelRates]
    /// Every downloaded entry, before user overrides replaced any keys,
    /// so `listLookup` can still find the price an override shadows.
    private var listEntries: [String: ModelRates]
    /// Normalized name to entry key, built once.
    private var index: [String: String]
    /// Same index over only the user-priced keys, checked first so a saved
    /// price wins under every equivalent name (`gpt-4-1` beats the
    /// published `gpt-4.1`, an undated key beats a dated published entry).
    private var overrideIndex: [String: String]
    /// `index` restricted to downloaded entries, so `listLookup` finds the
    /// price a model would have without the user's overrides.
    private var listIndex: [String: String]
    /// Which source each entry key came from; absent means LiteLLM.
    private var sources: [String: Source]
    /// `sources` before user overrides, matching `listEntries`.
    private var listSources: [String: Source]
    private var supplement: PricingSupplement
    /// Earlier prices of LiteLLM keys whose price changed or that left the
    /// list, oldest span first, so the ledger can price an event at the
    /// rate in force when it happened. Empty for a catalog with no history.
    private var history: [String: [Span]] = [:]

    /// One price a key held from `from` until `to` (nil: still in force).
    struct Span: Sendable {
        var from: Double
        var to: Double?
        var rates: ModelRates
    }

    public static let empty = PricingCatalog(entries: [:], supplement: PricingSupplement())

    init(entries: [String: ModelRates], supplement: PricingSupplement,
         sources: [String: Source] = [:], listEntries: [String: ModelRates]? = nil,
         listSources: [String: Source]? = nil) {
        self.entries = entries
        self.listEntries = listEntries ?? entries.filter { sources[$0.key] != .yours }
        self.supplement = supplement
        self.sources = sources
        self.listSources = listSources ?? sources.filter { $0.value != .yours }
        self.index = [:]
        self.overrideIndex = [:]
        self.listIndex = [:]
        // Bare keys win over provider-prefixed ones, then shorter prefixes.
        // Ranked once, not per comparison, and each key's forms worked out
        // once for all three indexes: a 3,000-key list otherwise spends
        // most of a second here, on every saved price.
        let rankOrder = { (keys: Dictionary<String, ModelRates>.Keys) in
            keys.map { (rank: Self.prefixRank($0), key: $0) }
                .sorted { ($0.rank, $0.key) < ($1.rank, $1.key) }
                .map(\.key)
        }
        let ordered = rankOrder(entries.keys)
        let listOrdered = rankOrder(self.listEntries.keys)
        var formsByPass: [[String: [String]]] = [[:], [:]]
        for key in ordered {
            formsByPass[0][key] = Self.forms(of: key, stripDates: false)
            formsByPass[1][key] = Self.forms(of: key, stripDates: true)
        }
        func forms(_ key: String, pass: Int) -> [String] {
            formsByPass[pass][key] ?? Self.forms(of: key, stripDates: pass == 1)
        }
        for pass in 0..<2 {
            for key in ordered {
                for form in forms(key, pass: pass) where index[form] == nil {
                    index[form] = key
                }
            }
        }
        for pass in 0..<2 {
            for key in ordered where sources[key] == .yours {
                for form in forms(key, pass: pass) where overrideIndex[form] == nil {
                    overrideIndex[form] = key
                }
            }
        }
        for pass in 0..<2 {
            for key in listOrdered {
                for form in forms(key, pass: pass) where listIndex[form] == nil {
                    listIndex[form] = key
                }
            }
        }
        thresholds = Self.thresholds(entries: entries, history: history)
    }

    /// Decodes LiteLLM's JSON. Entries that are not priced per token, or are
    /// not chat models, are dropped. `overrides` (the user's own
    /// `config/pricing.json`, LiteLLM-shaped) merge last and win over both
    /// other sources under the same name normalization.
    public init(liteLLM data: Data, supplement: PricingSupplement = PricingSupplement(),
                overrides: [String: [String: Any]] = [:]) throws {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw PricingError.notACatalog
        }
        self.init(liteLLMEntries: object, supplement: supplement, overrides: overrides)
    }

    /// `init(liteLLM:)` over an already decoded list.
    init(liteLLMEntries object: [String: Any], supplement: PricingSupplement = PricingSupplement(),
         overrides: [String: [String: Any]] = [:]) {
        var entries: [String: ModelRates] = [:]
        var sources: [String: Source] = [:]
        for (key, value) in object where key != "sample_spec" {
            guard let entry = value as? [String: Any], let rates = Self.rates(from: entry) else { continue }
            entries[key] = rates
        }
        for (key, value) in supplement.prices {
            if let rates = Self.rates(from: value) {
                entries[key] = rates
                sources[key] = .swe
            }
        }
        // Snapshot before the user's prices replace any keys: this is the
        // price list an override can reset back to.
        var listEntries = entries
        let listSources = sources
        for (key, value) in overrides {
            if let rates = Self.rates(from: value) {
                entries[key] = rates
                sources[key] = .yours
            }
        }
        for key in entries.keys {
            let multipliers = Self.multipliers(for: key, supplement: supplement)
            entries[key]?.tierMultipliers = multipliers
            listEntries[key]?.tierMultipliers = multipliers
        }
        self.init(entries: entries, supplement: supplement, sources: sources,
                  listEntries: listEntries, listSources: listSources)
    }

    /// The snapshot and supplement that ship with the app.
    public static func bundled() throws -> PricingCatalog {
        try PricingCatalog(liteLLM: try bundledLiteLLM(), supplement: try PricingSupplement.bundled())
    }

    static func bundledLiteLLM() throws -> Data {
        guard let url = CoreResources.url("pricing", "json") else {
            throw PricingError.missingResource
        }
        return try Data(contentsOf: url)
    }

    public var modelCount: Int { entries.count }

    /// Devin logs fast mode in the model name (`claude-opus-5-5-high-fast`)
    /// with an empty tier. Split that suffix out so the lookup finds the
    /// base model and the cost call prices it at the fast multiplier.
    /// A logged tier always wins over the name.
    public static func splitTierSuffix(model: String, tier: String) -> (model: String, tier: String) {
        if tier.isEmpty, model.lowercased().hasSuffix("-fast") {
            return (String(model.dropLast(5)), "fast")
        }
        return (model, tier)
    }

    /// Free-tier models never carry a catalog price: OpenRouter's
    /// `provider/model:free` and `openrouter/free`, and the `-free`
    /// suffixed names OpenCode logs.
    public static func isFree(_ model: String) -> Bool {
        let bare = bareName(model)
        return bare == "free" || bare.hasSuffix(":free") || bare.hasSuffix("-free")
    }

    /// Rates for `model`, or nil when the catalog cannot price it. A nil
    /// model is reported as unpriced, never as free.
    public func rates(for model: String) -> ModelRates? {
        resolvedKey(for: model).flatMap { entries[$0] }
    }

    /// The lookup `rates(for:)` runs, with which entry answered and where
    /// that entry came from. The Pricing page shows the source.
    public func lookup(_ model: String) -> (rates: ModelRates, entry: String, source: Source)? {
        guard let key = resolvedKey(for: model), let rates = entries[key] else { return nil }
        return (rates, key, sources[key] ?? .liteLLM)
    }

    /// The price `model` would have without the user's overrides: the same
    /// lookup restricted to LiteLLM and Quiver entries (the `-fast` suffix
    /// splits the same way). The Pricing page shows it as the list price a
    /// saved price can reset to, and to prefill a listed model's rates.
    public func listLookup(_ model: String) -> (rates: ModelRates, entry: String, source: Source)? {
        let split = Self.splitTierSuffix(model: model, tier: "")
        for candidate in split.model == model ? [model] : [model, split.model] {
            if let key = resolvedKey(for: candidate, userPrices: false),
               let rates = listEntries[key] {
                return (rates, key, listSources[key] ?? .liteLLM)
            }
        }
        return nil
    }

    /// The whole resolution for a logged model: the literal name first
    /// (`xai/grok-code-fast` is a real entry), then the `-fast` tier
    /// suffix, then the free-model check. nil means the model is
    /// genuinely unpriced.
    public func resolve(model: String, tier: String = "") -> Resolution? {
        if let hit = lookup(model) {
            return .priced(rates: hit.rates, entry: hit.entry, source: hit.source, tier: tier)
        }
        let split = Self.splitTierSuffix(model: model, tier: tier)
        if split.model != model, let hit = lookup(split.model) {
            return .priced(rates: hit.rates, entry: hit.entry, source: hit.source, tier: split.tier)
        }
        if Self.isFree(model) { return .free }
        return nil
    }

    /// Every saved user key `model` can resolve to. The literal name, its
    /// normalized forms and the `-fast` split all apply, and several saved
    /// keys can match one model (`gpt-4.1` and `gpt-4-1` both price
    /// `gpt-4.1`, but only one lands in `overrideIndex`), so every `.yours`
    /// entry's own forms are compared, not just the index's single hit.
    public func userKeys(matching model: String) -> [String] {
        let lowered = model.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var forms = Self.forms(of: lowered, stripDates: true, stripEffort: true)
        forms.append(lowered)
        let split = Self.splitTierSuffix(model: lowered, tier: "")
        if split.model != lowered {
            forms.append(split.model)
            forms += Self.forms(of: split.model, stripDates: true, stripEffort: true)
        }
        let lookup = Set(forms)
        return entries.keys
            .filter { sources[$0] == .yours }
            .filter { key in
                // A saved key keeps its effort suffix, as in `overrideIndex`:
                // `swe-2-medium` and `swe-2-high` never price each other.
                lookup.contains(key.lowercased())
                    || Self.forms(of: key, stripDates: true)
                        .contains { lookup.contains($0) }
            }
            .sorted()
    }

    /// The model's default key for a user price: the logged name with any
    /// provider prefix, snapshot date and effort suffix stripped, so a
    /// price saved under it covers every form the lookup generates
    /// (`swe-2` covers `swe-2-medium`, `-high` and `-max`).
    public static func baseName(for model: String) -> String {
        forms(of: model, stripDates: true, stripEffort: true).last ?? model
    }

    /// Which entry key `model` resolves to, through aliases and the
    /// normalized forms. `userPrices: false` skips the override index and
    /// every user-saved entry, so the answer is always a downloaded price.
    /// `visited` holds the names an alias chain already passed through, so
    /// a cycle in the supplement (`a` → `b` → `a`) ends instead of
    /// recursing until the stack runs out.
    private func resolvedKey(for model: String, userPrices: Bool = true,
                             visited: Set<String> = []) -> String? {
        let trimmed = model.trimmingCharacters(in: .whitespacesAndNewlines)
        let lowered = trimmed.lowercased()
        guard !visited.contains(lowered) else { return nil }
        let visited = visited.union([lowered])
        // The user's own prices are consulted before anything else.
        if userPrices {
            if sources[trimmed] == .yours { return trimmed }
            for form in [lowered] + Self.forms(of: lowered, stripDates: true, stripEffort: true) {
                if let key = overrideIndex[form] { return key }
            }
        }
        if (userPrices ? entries : listEntries)[trimmed] != nil { return trimmed }
        if let alias = supplement.aliases[lowered], alias.lowercased() != lowered {
            return resolvedKey(for: alias, userPrices: userPrices, visited: visited)
        }
        let index = userPrices ? self.index : listIndex
        for form in Self.forms(of: lowered, stripDates: true, stripEffort: true) {
            if let alias = supplement.aliases[form], alias.lowercased() != form {
                return resolvedKey(for: alias, userPrices: userPrices, visited: visited)
            }
            if let key = index[form] { return key }
        }
        return nil
    }

    /// Every long-context threshold any model uses now or used before,
    /// ascending. Worked out once per catalog: the ledger reads it for
    /// every priced group, and a pass over a few thousand entries each
    /// time made year and all-time figures pay it tens of thousands of times.
    private(set) var thresholds: [Int] = []

    private static func thresholds(entries: [String: ModelRates], history: [String: [Span]]) -> [Int] {
        let earlier = history.values.joined().compactMap { $0.rates.longContext?.threshold }
        return Array(Set(entries.values.compactMap { $0.longContext?.threshold } + earlier)).sorted()
    }

    // MARK: - History

    /// This catalog with the earlier prices of the LiteLLM keys in
    /// `spans`. Keys a user override or Quiver's supplement prices keep
    /// that price at every time, so their LiteLLM history is dropped.
    func withHistory(_ spans: [String: [CatalogDatabase.PriceSpan]]) -> PricingCatalog {
        var copy = self
        copy.history = [:]
        for (key, list) in spans where sources[key] == nil || sources[key] == .liteLLM {
            let converted = list.compactMap { span -> Span? in
                guard var rates = Self.rates(from: span.entry) else { return nil }
                rates.tierMultipliers = entries[key]?.tierMultipliers
                    ?? Self.multipliers(for: key, supplement: supplement)
                return Span(from: span.from, to: span.to, rates: rates)
            }
            if !converted.isEmpty { copy.history[key] = converted }
        }
        copy.thresholds = Self.thresholds(entries: copy.entries, history: copy.history)
        return copy
    }

    /// Tier multipliers for `key`: the model's own, found through its
    /// dated or dotted variants too (`claude-opus-4-6-20260205`,
    /// `claude-opus-4.6`), over the supplement's defaults.
    private static func multipliers(for key: String, supplement: PricingSupplement) -> [String: Double] {
        let specific = Self.forms(of: key, stripDates: true).lazy
            .compactMap { supplement.tierMultipliers[$0] }.first
        var multipliers = supplement.defaultTierMultipliers
        multipliers.merge(specific ?? [:]) { _, specific in specific }
        return multipliers
    }

    /// Whether any key carries earlier prices.
    var hasHistory: Bool { !history.isEmpty }

    /// The moments, ascending, at which any of `keys` changed price: the
    /// edges between the spans their history holds. Within two adjacent
    /// boundaries every one of those keys has a single price.
    func priceChanges(for keys: some Sequence<String>) -> [Double] {
        var moments = Set<Double>()
        for key in keys {
            guard let spans = history[key] else { continue }
            for (index, span) in spans.enumerated() {
                if index > 0 { moments.insert(span.from) }
                if let to = span.to { moments.insert(to) }
            }
        }
        return moments.sorted()
    }

    /// The rates `entry` had at `time`, or nil to use the current ones.
    /// A time before the key's first known price takes that first price,
    /// so usage logged before the list was first fetched is still priced.
    func historicalRates(entry: String, at time: Double) -> ModelRates? {
        guard let spans = history[entry], let first = spans.first else { return nil }
        if time < first.from { return first.rates }
        return spans.last { $0.from <= time && ($0.to.map { time < $0 } ?? true) }?.rates
    }

    // MARK: - Name normalization

    private static let knownPrefixes = [
        "", "anthropic", "openai", "gemini", "vertex_ai", "xai", "deepseek", "moonshot", "zai",
        "minimax", "mistral", "dashscope", "github_copilot",
    ]

    private static func prefixRank(_ key: String) -> Int {
        let prefix = key.split(separator: "/", omittingEmptySubsequences: false).dropLast().joined(separator: "/")
        return knownPrefixes.firstIndex(of: prefix) ?? knownPrefixes.count
    }

    static func bareName(_ key: String) -> String {
        var name = key.lowercased()
        if let slash = name.lastIndex(of: "/") { name = String(name[name.index(after: slash)...]) }
        return name
    }

    /// The names `key` can be found under, most specific first.
    static func forms(of key: String, stripDates: Bool, stripEffort: Bool = false) -> [String] {
        var name = bareName(key)
        // Bedrock: `us.anthropic.claude-…-v1:0`, `anthropic.claude-…`.
        for pattern in Patterns.cleanup { pattern.removeFirstMatch(in: &name) }
        var forms = [name]
        if stripDates {
            var undated = name
            for pattern in Patterns.dates { pattern.removeFirstMatch(in: &undated) }
            if undated != name { forms.append(undated) }
        }
        for form in forms where form.contains(".") || form.contains("@") {
            forms.append(form.replacingOccurrences(of: ".", with: "-").replacingOccurrences(of: "@", with: "-"))
        }
        // A reasoning-effort suffix (`-high`, Devin and Cursor style) is tried
        // last, so a model whose real name ends that way still matches first.
        // Only for looked-up names: a catalog key never lends its price this way.
        if stripEffort {
            for form in forms {
                var bare = form
                if Patterns.effort.removeFirstMatch(in: &bare) { forms.append(bare) }
            }
        }
        return forms
    }

    /// `forms`' patterns, compiled once: `String.range(of:options:
    /// .regularExpression)` compiles its pattern on every call, and
    /// `forms` runs thousands of times per catalog and per lookup.
    private enum Patterns {
        /// Bedrock's `us.anthropic.claude-…-v1:0` and `anthropic.claude-…`
        /// prefix, a `-v1:0` version suffix, a `[1m]` bracket suffix.
        static let cleanup = [#"^([a-z]{2,4}\.)?anthropic\."#, #"-v\d+(:\d+)?$"#, #"\[[^\]]*\]$"#]
            .map(Pattern.init)
        static let dates = [#"@\d{8}$"#, #"-\d{4}-\d{2}-\d{2}$"#, #"-\d{8}$"#].map(Pattern.init)
        static let effort = Pattern(#"-(minimal|low|medium|high|xhigh|max)$"#)
    }

    private struct Pattern: @unchecked Sendable {
        let regex: NSRegularExpression

        init(_ pattern: String) {
            regex = try! NSRegularExpression(pattern: pattern)
        }

        /// Removes the first match from `text`; whether there was one.
        @discardableResult
        func removeFirstMatch(in text: inout String) -> Bool {
            let whole = NSRange(text.startIndex..., in: text)
            guard let match = regex.firstMatch(in: text, range: whole),
                  let range = Range(match.range, in: text) else { return false }
            text.removeSubrange(range)
            return true
        }
    }

    // MARK: - Decoding one entry

    static func rates(from entry: [String: Any]) -> ModelRates? {
        if let mode = entry["mode"] as? String, mode != "chat", mode != "responses" { return nil }
        func number(_ key: String) -> Double? { (entry[key] as? NSNumber)?.doubleValue }
        guard let input = number("input_cost_per_token"), let output = number("output_cost_per_token") else {
            return nil
        }
        let isAnthropic = (entry["litellm_provider"] as? String).map {
            $0 == "anthropic" || $0.contains("bedrock") || $0.contains("vertex_ai-anthropic")
        } ?? false

        func build(suffix: String) -> ModelRates? {
            guard let input = number("input_cost_per_token" + suffix) else { return nil }
            let output = number("output_cost_per_token" + suffix) ?? output
            let cacheRead = number("cache_read_input_token_cost" + suffix) ?? number("cache_read_input_token_cost") ?? input
            let cacheWrite = number("cache_creation_input_token_cost" + suffix) ?? input
            let cacheWrite1h = number("cache_creation_input_token_cost_above_1hr" + suffix)
                ?? (isAnthropic ? input * 2 : cacheWrite)
            let reasoning = number("output_cost_per_reasoning_token" + suffix) ?? output
            var longContext: ModelRates.LongContext?
            let pattern = #"^input_cost_per_token_above_(\d+)k_tokens"# + NSRegularExpression.escapedPattern(for: suffix) + "$"
            let thresholds = entry.keys.compactMap { key -> Int? in
                guard let match = key.range(of: pattern, options: .regularExpression) else { return nil }
                let digits = key[match].dropFirst("input_cost_per_token_above_".count).prefix { $0.isNumber }
                return Int(digits).map { $0 * 1_000 }
            }
            if let threshold = thresholds.min() {
                let tag = "_above_\(threshold / 1_000)k_tokens" + suffix
                let longInput = number("input_cost_per_token" + tag) ?? input
                let longOutput = number("output_cost_per_token" + tag) ?? output
                let longWrite = number("cache_creation_input_token_cost" + tag) ?? cacheWrite
                longContext = ModelRates.LongContext(
                    threshold: threshold, input: longInput, output: longOutput,
                    cacheRead: number("cache_read_input_token_cost" + tag) ?? cacheRead,
                    cacheWrite: longWrite,
                    cacheWrite1h: isAnthropic ? longInput * 2 : longWrite,
                    reasoning: number("output_cost_per_reasoning_token" + tag) ?? longOutput)
            }
            return ModelRates(
                input: input, output: output, cacheRead: cacheRead, cacheWrite: cacheWrite,
                cacheWrite1h: cacheWrite1h, reasoning: reasoning, longContext: longContext)
        }

        guard input >= 0, output >= 0, var base = build(suffix: "") else { return nil }
        for tier in ["priority", "flex", "ultrafast", "fast"] {
            if let rates = build(suffix: "_" + tier) { base.tiers[tier] = rates }
        }
        return base
    }
}

public enum PricingError: Error, Equatable {
    case notACatalog
    case missingResource
    case httpStatus(Int)
}

/// Quiver's additions to LiteLLM: aliases for names LiteLLM does not carry,
/// tier multipliers it does not publish, and prices for models it lacks.
public struct PricingSupplement {
    /// Logged name (lower-cased) to the catalog name that prices it.
    public var aliases: [String: String]
    /// Bare model name to tier ("fast", "priority") to multiplier, used when
    /// the catalog has no explicit rates for that tier.
    public var tierMultipliers: [String: [String: Double]]
    /// Tier multipliers for every model without a specific one.
    public var defaultTierMultipliers: [String: Double]
    /// LiteLLM-shaped entries for models LiteLLM does not list.
    var prices: [String: [String: Any]]

    public init(
        aliases: [String: String] = [:], tierMultipliers: [String: [String: Double]] = [:],
        defaultTierMultipliers: [String: Double] = [:]
    ) {
        self.aliases = aliases
        self.tierMultipliers = tierMultipliers
        self.defaultTierMultipliers = defaultTierMultipliers
        self.prices = [:]
    }

    /// The real name `aliases` gives `model` ("MODEL_PRIVATE_11" is
    /// "claude-haiku-4-5"), chains followed and forms tried as
    /// `resolvedKey` tries them; nil when no alias applies. Pages name and
    /// brand a model by it.
    public func aliasTarget(of model: String) -> String? {
        var target: String?
        var name = model.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        for _ in 0..<5 {
            let candidates = [name] + PricingCatalog.forms(of: name, stripDates: true, stripEffort: true)
            guard let hit = candidates.lazy.compactMap({ form in
                self.aliases[form].flatMap { $0.lowercased() != form ? $0 : nil }
            }).first else { break }
            target = hit
            name = hit.lowercased()
        }
        return target
    }

    public static func bundled() throws -> PricingSupplement {
        guard let url = CoreResources.url("pricing-supplement", "json") else {
            throw PricingError.missingResource
        }
        return try decode(Data(contentsOf: url))
    }

    static func decode(_ data: Data) throws -> PricingSupplement {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw PricingError.notACatalog
        }
        var supplement = PricingSupplement()
        for (key, value) in object["aliases"] as? [String: String] ?? [:] {
            supplement.aliases[key.lowercased()] = value
        }
        for (model, tiers) in object["tier_multipliers"] as? [String: [String: Any]] ?? [:] {
            supplement.tierMultipliers[model.lowercased()] = tiers.compactMapValues { ($0 as? NSNumber)?.doubleValue }
        }
        supplement.defaultTierMultipliers = (object["default_tier_multipliers"] as? [String: Any] ?? [:])
            .compactMapValues { ($0 as? NSNumber)?.doubleValue }
        supplement.prices = object["prices"] as? [String: [String: Any]] ?? [:]
        return supplement
    }
}

// `[String: Any]` holds only JSON values here, which are immutable once decoded.
extension PricingSupplement: @unchecked Sendable {}
