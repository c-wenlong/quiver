import Foundation

/// Maps a model name the logs carry (`claude-opus-5-5-high-fast`,
/// `openai/gpt-6.1-sol@20261003`) to an Artificial Analysis model. Both
/// sides normalize the same way before comparing: lowercase, provider
/// prefix and snapshot date off, dots to dashes, `-fast` peeled into a
/// mode flag, and a trailing effort word (`-high`, `(max)`) pulled out.
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
    /// When no effort matches exactly, which available one to take.
    private static let effortPreference = ["high", "max", "medium", "low", "xhigh", "minimal"]

    /// Splits a logged or AA-listed name into base, effort and fast flag.
    public static func parse(_ name: String) -> Parsed {
        var base = name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        // A parenthesised qualifier: "Claude Opus 5.5 (max)".
        var effort: String?
        if let range = base.range(of: #"\((minimal|low|medium|high|xhigh|max)\)\s*$"#,
                                  options: .regularExpression) {
            effort = String(base[range].dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
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

    /// The AA model `logged` names, or nil. Order: manual link, base plus
    /// effort exact, base with no qualifier, base with the closest effort
    /// (prefer high, then max, then medium). Ties take the highest
    /// intelligence index.
    public static func match(_ logged: String, in models: [AALanguageModel],
                             links: [String: String] = [:]) -> AALanguageModel? {
        let parsed = parse(logged)
        if let linked = links[parsed.base],
           let model = models.first(where: { $0.id == linked }) {
            return model
        }
        let candidates = models.filter { parseAA($0).base == parsed.base }
        guard !candidates.isEmpty else { return nil }
        func best(_ pool: [AALanguageModel]) -> AALanguageModel? {
            pool.max { ($0.intelligence ?? 0) < ($1.intelligence ?? 0) }
        }
        if let effort = parsed.effort,
           let exact = best(candidates.filter { parseAA($0).effort == effort }) {
            return exact
        }
        if let unqualified = best(candidates.filter { parseAA($0).effort == nil }) {
            return unqualified
        }
        // Closest effort by the preference order.
        for wanted in effortPreference {
            if let hit = best(candidates.filter { parseAA($0).effort == wanted }) {
                return hit
            }
        }
        return best(candidates)
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
