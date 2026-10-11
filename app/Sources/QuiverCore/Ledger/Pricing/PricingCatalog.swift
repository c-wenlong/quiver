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
    private var entries: [String: ModelRates]
    /// Normalized name to entry key, built once.
    private var index: [String: String]
    private var supplement: PricingSupplement

    public static let empty = PricingCatalog(entries: [:], supplement: PricingSupplement())

    init(entries: [String: ModelRates], supplement: PricingSupplement) {
        self.entries = entries
        self.supplement = supplement
        self.index = [:]
        // Bare keys win over provider-prefixed ones, then shorter prefixes.
        let ordered = entries.keys.sorted { lhs, rhs in
            let l = (Self.prefixRank(lhs), lhs), r = (Self.prefixRank(rhs), rhs)
            return l < r
        }
        for pass in 0..<2 {
            for key in ordered {
                for form in Self.forms(of: key, stripDates: pass == 1) where index[form] == nil {
                    index[form] = key
                }
            }
        }
    }

    /// Decodes LiteLLM's JSON. Entries that are not priced per token, or are
    /// not chat models, are dropped.
    public init(liteLLM data: Data, supplement: PricingSupplement = PricingSupplement()) throws {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw PricingError.notACatalog
        }
        var entries: [String: ModelRates] = [:]
        for (key, value) in object where key != "sample_spec" {
            guard let entry = value as? [String: Any], let rates = Self.rates(from: entry) else { continue }
            entries[key] = rates
        }
        for (key, value) in supplement.prices {
            if let rates = Self.rates(from: value) { entries[key] = rates }
        }
        for key in entries.keys {
            // The model's own multipliers, found through its dated or dotted
            // variants too (`claude-opus-4-6-20260205`, `claude-opus-4.6`).
            let specific = Self.forms(of: key, stripDates: true).lazy
                .compactMap { supplement.tierMultipliers[$0] }.first
            var multipliers = supplement.defaultTierMultipliers
            multipliers.merge(specific ?? [:]) { _, specific in specific }
            entries[key]?.tierMultipliers = multipliers
        }
        self.init(entries: entries, supplement: supplement)
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

    /// Rates for `model`, or nil when the catalog cannot price it. A nil
    /// model is reported as unpriced, never as free.
    public func rates(for model: String) -> ModelRates? {
        let trimmed = model.trimmingCharacters(in: .whitespacesAndNewlines)
        if let exact = entries[trimmed] { return exact }
        let lowered = trimmed.lowercased()
        if let alias = supplement.aliases[lowered], alias.lowercased() != lowered {
            return rates(for: alias)
        }
        for form in Self.forms(of: lowered, stripDates: true, stripEffort: true) {
            if let alias = supplement.aliases[form], alias != form { return rates(for: alias) }
            if let key = index[form] { return entries[key] }
        }
        return nil
    }

    /// Every long-context threshold any model uses, ascending.
    var thresholds: [Int] {
        Array(Set(entries.values.compactMap { $0.longContext?.threshold })).sorted()
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
        if let range = name.range(of: #"^([a-z]{2,4}\.)?anthropic\."#, options: .regularExpression) {
            name.removeSubrange(range)
        }
        if let range = name.range(of: #"-v\d+(:\d+)?$"#, options: .regularExpression) { name.removeSubrange(range) }
        if let range = name.range(of: #"\[[^\]]*\]$"#, options: .regularExpression) { name.removeSubrange(range) }
        var forms = [name]
        if stripDates {
            var undated = name
            for pattern in [#"@\d{8}$"#, #"-\d{4}-\d{2}-\d{2}$"#, #"-\d{8}$"#] {
                if let range = undated.range(of: pattern, options: .regularExpression) {
                    undated.removeSubrange(range)
                }
            }
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
                if let range = form.range(of: #"-(minimal|low|medium|high|xhigh|max)$"#, options: .regularExpression) {
                    var bare = form
                    bare.removeSubrange(range)
                    forms.append(bare)
                }
            }
        }
        return forms
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
    case tooFewModels(Int)
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
