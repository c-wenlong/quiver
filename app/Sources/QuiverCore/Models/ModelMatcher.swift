import Foundation

/// Maps a model name the logs carry (`claude-opus-5-5-high-fast`,
/// `openai/gpt-6.1-sol@20261003`) to an Artificial Analysis model. Both
/// sides normalize the same way before comparing: lowercase, provider
/// prefix and snapshot date off, dots to dashes, `-fast` peeled into a
/// mode flag, and a trailing effort word (`-high`, `(max)`,
/// `(Max, Default Fallback)`) pulled out.
///
/// A logged name resolves to the foundation model, not to the effort or
/// mode a harness ran it at: `claude-opus-5-5-high-fast` is Claude Opus
/// 5.5, scored and priced as AA's headline entry for it (the one whose
/// slug carries no effort, `claude-opus-5-5`). The effort stays on the
/// row as its qualifier.
public struct ModelMatcher: Sendable {
    /// A name's comparable shape: the base model plus the qualifiers that
    /// pick between variants of it.
    public struct Parsed: Sendable, Equatable {
        public var base: String
        /// minimal|low|medium|high|xhigh|max, when the name said one.
        public var effort: String?
        /// The name ended `-fast`, which AA models never spell out.
        public var fast: Bool
    }

    public static let efforts = ["minimal", "low", "medium", "high", "xhigh", "max"]
    /// A family with no effort-free slug: which variant stands for it.
    private static let effortPreference = ["max", "xhigh", "high", "medium", "low", "minimal"]
    /// An AA name's effort tail, case-insensitive: "(max)",
    /// "(Xhigh, Default Fallback)". "(Non-reasoning, High)" is a model of
    /// its own and stays in the base.
    private static let effortTail =
        #"(?i)\s*\((minimal|low|medium|high|xhigh|max)(,[^)]*)?\)\s*$"#

    /// Splits a logged or AA-listed name into base, effort and fast flag.
    public static func parse(_ name: String) -> Parsed {
        var base = name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        // A parenthesised qualifier: "Claude Opus 5.5 (max)", or with a
        // note after the effort, "(Max, Default Fallback)".
        var effort: String?
        if let range = base.range(of: effortTail, options: .regularExpression) {
            let tail = base[range].drop { $0 != "(" }.dropFirst()
            effort = String(tail.prefix { $0.isLetter })
            base.removeSubrange(range)
            base = base.trimmingCharacters(in: .whitespaces)
        }
        // A provider prefix: "openai/gpt-6.1-sol".
        if let slash = base.lastIndex(of: "/") {
            base = String(base[base.index(after: slash)...])
        }
        // Modes and snapshot dates can stack in any order
        // ("model-max@20261003-fast"), so strip each until nothing moves.
        var fast = false
        var changed = true
        while changed {
            changed = false
            if let range = base.range(of: #"-fast$"#, options: .regularExpression) {
                fast = true
                base.removeSubrange(range)
                changed = true
            }
            if effort == nil,
               let range = base.range(of: #"-(minimal|low|medium|high|xhigh|max)$"#,
                                      options: .regularExpression) {
                effort = String(base[range].dropFirst())
                base.removeSubrange(range)
                changed = true
            }
            // Snapshot dates: "@20251001", "-2025-10-01", "-20251001".
            for pattern in [#"@\d{8}$"#, #"-\d{4}-\d{2}-\d{2}$"#, #"-\d{8}$"#] {
                if let range = base.range(of: pattern, options: .regularExpression) {
                    base.removeSubrange(range)
                    changed = true
                }
            }
        }
        base = base.replacingOccurrences(of: ".", with: "-")
            .replacingOccurrences(of: " ", with: "-")
        return Parsed(base: base, effort: effort, fast: fast)
    }

    /// What an AA entry's name parses to; the slug's trailing token is a
    /// fallback effort when the name has no qualifier.
    private static func parseAA(_ model: AALanguageModel) -> Parsed {
        var parsed = parse(model.name)
        if parsed.effort == nil,
           let range = model.slug.lowercased().range(of: #"-(minimal|low|medium|high|xhigh|max)$"#,
                                                     options: .regularExpression) {
            parsed.effort = String(model.slug.lowercased()[range].dropFirst())
        }
        return parsed
    }

    /// `~/.quiver/config/model-links.json`: `{"logged base": "aa id"}`.
    /// A manual link beats every rule.
    public static func loadLinks(from url: URL) -> [String: String] {
        guard let data = FileManager.default.contents(atPath: url.path),
              let decoded = try? JSONDecoder().decode([String: String].self, from: data)
        else { return [:] }
        return decoded
    }

    public static func saveLinks(_ links: [String: String], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try JSONEncoder().encode(links).write(to: url, options: .atomic)
    }

    /// The AA model `logged` names, or nil: the headline entry of the
    /// family a manual link points into, else of the family whose base
    /// matches. A link saved against "Claude Opus 5.5 (High)" lands on
    /// the same Opus 5.5 entry the leaderboard shows.
    public static func match(_ logged: String, in models: [AALanguageModel],
                             links: [String: String] = [:]) -> AALanguageModel? {
        let parsed = parse(logged)
        if let linked = links[parsed.base],
           let model = models.first(where: { $0.id == linked }) {
            let base = parseAA(model).base
            return headline(of: models.filter { parseAA($0).base == base }) ?? model
        }
        return headline(of: models.filter { parseAA($0).base == parsed.base })
    }

    /// One entry per foundation model, in the order each first appears:
    /// what the leaderboard lists, so Opus 5.5 is one bar, not four.
    public static func headlines(in models: [AALanguageModel]) -> [AALanguageModel] {
        var order: [String] = []
        var families: [String: [AALanguageModel]] = [:]
        for model in models {
            let base = parseAA(model).base
            if families[base] == nil { order.append(base) }
            families[base, default: []].append(model)
        }
        return order.compactMap { families[$0].flatMap(headline) }
    }

    /// The entry AA headlines a family with: the slug with no effort
    /// suffix (`claude-opus-5-5`, AA's own default run), else the highest
    /// effort listed. Ties take the highest intelligence index.
    private static func headline(of family: [AALanguageModel]) -> AALanguageModel? {
        func best(_ pool: [AALanguageModel]) -> AALanguageModel? {
            pool.max { ($0.intelligence ?? 0) < ($1.intelligence ?? 0) }
        }
        if let plain = best(family.filter { !slugHasEffort($0) }) { return plain }
        for wanted in effortPreference {
            if let hit = best(family.filter { parseAA($0).effort == wanted }) { return hit }
        }
        return best(family)
    }

    private static func slugHasEffort(_ model: AALanguageModel) -> Bool {
        model.slug.lowercased()
            .range(of: #"-(minimal|low|medium|high|xhigh|max)$"#, options: .regularExpression) != nil
    }

    /// The foundation model's name: "Claude Opus 5.5" for "Claude Opus
    /// 5.5 (Max, Default Fallback)". Other parentheses stay.
    public static func displayName(_ model: AALanguageModel) -> String {
        guard let range = model.name.range(of: effortTail, options: .regularExpression)
        else { return model.name }
        return String(model.name[..<range.lowerBound])
    }
}

/// Where a model's blended price lands on AA's whole price scale:
/// `(cheaper + 0.5 × same-priced others) / (count − 1)`, clamped to 0…1.
/// Equal-priced models share a spot, which is how pickers rank them.
public enum CostPercentile {
    public static func rank(of model: AALanguageModel, in models: [AALanguageModel]) -> Double? {
        let priced = models.compactMap(\.priceBlended).filter { $0 > 0 }
        guard let price = model.priceBlended, price > 0, priced.count > 1 else { return nil }
        let lower = priced.filter { $0 < price }.count
        let equal = priced.filter { $0 == price }.count - 1 // itself excluded
        return min(max((Double(lower) + 0.5 * Double(equal)) / Double(priced.count - 1), 0), 1)
    }
}
