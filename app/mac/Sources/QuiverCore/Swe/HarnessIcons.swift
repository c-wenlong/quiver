import Foundation

/// Harness logos, bundled from app/mac/script/import-harness-icons.py. A logo
/// that has brand colours keeps them (`harness-<id>-color.svg`), as
/// TokenTracker shows it; the rest are single-colour marks
/// (`harness-<id>.svg`) for the app to draw in the text colour. Where each
/// came from is in `harness-icons.tsv` beside them.
public enum HarnessIcons {
    public struct Icon: Equatable, Sendable {
        public var url: URL
        /// Draw as is; otherwise draw as a template in the text colour.
        public var isColor: Bool
    }

    /// Other names the same harness goes by.
    static let aliases = ["claude-code": "claude", "github-copilot": "copilot", "factory": "droid",
                          "qwen": "qwen-code", "mistral": "mistral-vibe", "codebuff": "freebuff",
                          "command-code": "commandcode"]

    /// Ids with a bundled icon, sorted.
    public static let available: [String] = {
        let urls = CoreResources.bundle.urls(forResourcesWithExtension: "svg", subdirectory: nil) ?? []
        let ids = urls.map(\.lastPathComponent)
            .filter { $0.hasPrefix("harness-") }
            .map { name in
                var id = String(name.dropFirst("harness-".count).dropLast(".svg".count))
                if id.hasSuffix("-color") { id.removeLast("-color".count) }
                return id
            }
        return Array(Set(ids)).sorted()
    }()

    public static func icon(for harness: String) -> Icon? {
        let id = aliases[harness.lowercased()] ?? harness.lowercased()
        if let url = CoreResources.url("harness-\(id)-color", "svg") { return Icon(url: url, isColor: true) }
        if let url = CoreResources.url("harness-\(id)", "svg") { return Icon(url: url, isColor: false) }
        return nil
    }

    /// The logo for any harness id, as `HarnessMark` picks it: Claude's for
    /// "claude-desktop", the base harness's for a second account
    /// ("codex-acc2"), nil when there is none. For views that show the logo
    /// beside a title that already names the harness, such as the widgets.
    public static func logo(for harness: String) -> Icon? {
        HarnessMark(harness).icon.flatMap { icon(for: $0) }
    }
}

/// How a harness id shows where space is short: its logo, plus a word only
/// where the logo alone is ambiguous, and the full name for tooltips and
/// VoiceOver.
///
/// Claude's logo stands for two harnesses: "claude" is Claude Code, the CLI
/// (swe reads its MCP servers from ~/.claude.json), and "claude-desktop" is
/// the Claude app. They show as "CLI" and "Desktop". A second account of a
/// harness ("codex-acc2") shows that harness's logo and its suffix. A
/// harness with no bundled logo keeps its name.
public struct HarnessMark: Equatable, Sendable {
    /// The logo to draw, or nil when there is none.
    public var icon: String?
    /// Shown after the logo, or alone when there is no logo.
    public var word: String?
    public var name: String

    public init(icon: String?, word: String?, name: String) {
        self.icon = icon
        self.word = word
        self.name = name
    }

    public init(_ harness: String) {
        switch harness {
        case "claude":
            self.init(icon: "claude", word: "CLI", name: Self.name(of: "claude"))
        case "claude-desktop":
            self.init(icon: "claude", word: "Desktop", name: Self.name(of: "claude-desktop"))
        // Grok Bot events come from Cursor's usage feed; the name would
        // otherwise split at the dash and show as "Grok (bot)".
        case "grok-bot":
            self.init(icon: "grok", word: nil, name: Self.name(of: "grok-bot"))
        // Devin's API sessions; the default would read "Devin (cloud)".
        case DevinCloud.harness:
            self.init(icon: "devin", word: "Cloud", name: Self.name(of: DevinCloud.harness))
        case _ where HarnessIcons.icon(for: harness) != nil:
            self.init(icon: harness, word: nil, name: Self.name(of: harness))
        default:
            if let dash = harness.lastIndex(of: "-") {
                let base = String(harness[..<dash])
                let suffix = String(harness[harness.index(after: dash)...])
                if !suffix.isEmpty, HarnessIcons.icon(for: base) != nil {
                    self.init(icon: base, word: suffix, name: "\(Self.name(of: base)) (\(suffix))")
                    return
                }
            }
            let name = Self.name(of: harness)
            self.init(icon: nil, word: name, name: name)
        }
    }

    /// The one harness name table: each product's own spelling where
    /// capitalising the id gets it wrong. Every page names a harness through
    /// `HarnessMark(id).name` (or `UsageFormat.harness`, which reads it), so
    /// a name changed here changes everywhere. Names always start with a
    /// capital letter.
    static let names = ["claude": "Claude Code", "claude-desktop": "Claude Desktop", "codex": "Codex",
                        "opencode": "OpenCode", "devin": "Devin", "devin-cloud": "Devin cloud",
                        "droid": "Factory Droid", "copilot": "GitHub Copilot", "lmstudio": "LM Studio",
                        "qwen-code": "Qwen Code", "mistral-vibe": "Mistral Vibe", "commandcode": "Command Code",
                        "qoder-cn": "Qoder CN", "grok-bot": "Grok Bot", "grok": "Grok Build",
                        "kilo": "Kilo Code", "goose": "Goose", "aider": "Aider", "zcode": "ZCode",
                        "artificial-analysis": "Artificial Analysis"]

    static func name(of harness: String) -> String {
        names[harness] ?? harness.prefix(1).uppercased() + harness.dropFirst()
    }

    /// The name of an account or usage provider for a harness: the same as
    /// the harness name, except that a Claude account is "Claude". It is
    /// the Anthropic subscription, which covers the app and the CLI alike.
    public static func accountName(_ id: String) -> String {
        id == "claude" ? "Claude" : HarnessMark(id).name
    }
}

/// Marks for the Pricing page's price sources that aren't harnesses.
public enum PricingSourceIcons {
    /// LiteLLM's monogram as a single-colour mark (source and licence are
    /// in the SVG's comment).
    public static let liteLLM: URL? = CoreResources.url("source-litellm", "svg")
}
