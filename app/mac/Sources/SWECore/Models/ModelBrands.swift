import Foundation

/// One company SWE names next to a model or a key: a model lab (Anthropic,
/// Meta, Xiaomi) or an API provider (Groq, OpenRouter).
public struct ModelBrand: Sendable, Equatable, Identifiable {
    public var id: String
    public var name: String
    /// The chart colour; nil for providers with no models of their own,
    /// which chart in "Other" grey.
    public var color: HarnessRGB?
    /// The bundled logo's id in `HarnessIcons`.
    public var icon: String?
    /// Every word that names the brand on its own, lower-cased: Artificial
    /// Analysis creator slugs, API-key file headings, short names.
    public var keywords: [String]
    /// Patterns a logged model name starts with (after any `provider/`
    /// prefix, lower-cased), as regular expressions anchored at the start.
    public var models: [String]

    public init(id: String, name: String, color: HarnessRGB? = nil, icon: String? = nil,
                keywords: [String] = [], models: [String] = []) {
        self.id = id
        self.name = name
        self.color = color
        self.icon = icon
        self.keywords = keywords
        self.models = models
    }
}

/// The one place a model or provider name turns into a brand: its display
/// name, chart colour and logo. The Models page (by Artificial Analysis
/// creator), Pricing (by logged model name) and API keys (by heading) all
/// read it, so a brand added here gets its logo everywhere at once.
public enum ModelBrands {
    private static func rgb(_ hex: String) -> HarnessRGB { HarnessRGB(hex: hex)! }

    public static let all: [ModelBrand] = [
        // Model labs. Anthropic takes the app's Claude harness colour; the
        // rest are brand picks where the brand has a colour, and a free hue
        // where it is black or would be a third blue (Z.ai yellow, Moonshot
        // bronze, InclusionAI magenta), so neighbouring bars stay apart.
        .init(id: "anthropic", name: "Anthropic",
              color: HarnessColor.of("claude"), icon: "claude",
              keywords: ["anthropic", "claude"], models: ["claude"]),
        .init(id: "openai", name: "OpenAI", color: rgb("#c9c9cf"), icon: "openai",
              keywords: ["openai"], models: ["gpt-", "chatgpt", "codex", #"o\d"#]),
        .init(id: "google", name: "Google", color: rgb("#3fa35b"), icon: "gemini",
              keywords: ["google", "gemini"], models: ["gemini", "gemma"]),
        // Grok Bot is Cursor's bot, not xAI's API, so `grok` skips it.
        .init(id: "xai", name: "XAI", color: rgb("#8a73e8"), icon: "grok",
              keywords: ["xai", "x-ai", "grok"], models: ["grok(?!-bot)"]),
        .init(id: "grok-bot", name: "Grok Bot", color: rgb("#8a73e8"), icon: "grok",
              keywords: ["grok-bot"], models: ["grok-bot"]),
        .init(id: "meta", name: "Meta", color: rgb("#2f7bf6"), icon: "meta",
              keywords: ["meta", "meta-llama", "llama", "muse"], models: ["llama", "muse"]),
        .init(id: "zai", name: "Z.ai", color: rgb("#e3cf4a"), icon: "zai",
              keywords: ["zai", "z-ai", "z.ai", "zhipu"], models: ["glm"]),
        .init(id: "deepseek", name: "DeepSeek", color: rgb("#5b6ef0"), icon: "deepseek",
              keywords: ["deepseek", "deepseek-ai"], models: ["deepseek"]),
        .init(id: "moonshot", name: "Moonshot", color: rgb("#b8864b"), icon: "kimi",
              keywords: ["moonshot", "moonshot-ai", "moonshotai", "kimi"], models: ["kimi", "moonshot"]),
        .init(id: "minimax", name: "MiniMax", color: rgb("#e2466e"), icon: "minimax",
              keywords: ["minimax"], models: ["minimax"]),
        .init(id: "alibaba", name: "Alibaba", color: rgb("#ff6a00"), icon: "qwen-code",
              keywords: ["alibaba", "qwen"], models: ["qwen"]),
        .init(id: "mistral", name: "Mistral", color: rgb("#f26b1d"), icon: "mistral-vibe",
              keywords: ["mistral", "mistral-ai"], models: ["mistral", "devstral", "codestral", "magistral"]),
        .init(id: "xiaomi", name: "Xiaomi", color: rgb("#f5a524"), icon: "xiaomimimo",
              keywords: ["xiaomi", "mimo"], models: ["mimo"]),
        .init(id: "stepfun", name: "StepFun", color: rgb("#14c8c8"), icon: "stepfun",
              keywords: ["stepfun"], models: [#"step[- ]?\d"#]),
        // InclusionAI is Ant Group's lab; Ant Group's mark stands for it.
        .init(id: "inclusionai", name: "InclusionAI", color: rgb("#c45bdc"), icon: "antgroup",
              keywords: ["inclusionai", "antgroup"], models: ["ling", #"ring-"#]),
        .init(id: "nvidia", name: "NVIDIA", color: rgb("#76b900"), icon: "nvidia",
              keywords: ["nvidia"], models: ["nemotron"]),
        .init(id: "upstage", name: "Upstage", icon: "upstage",
              keywords: ["upstage"], models: ["solar"]),
        .init(id: "yi", name: "01.AI", icon: "yi", keywords: ["01", "yi"], models: ["yi-"]),
        .init(id: "cognition", name: "Cognition", color: rgb("#9aa0aa"), icon: "devin",
              keywords: ["cognition", "cognition-ai", "cognition_ai"],
              models: ["swe-", "compactor", "summarizer"]),
        .init(id: "cursor", name: "Cursor", icon: "cursor",
              keywords: ["cursor"], models: ["composer", "cursor-"]),
        // API providers: keys and gateways, no models of their own.
        .init(id: "groq", name: "Groq", icon: "groq", keywords: ["groq"]),
        .init(id: "cerebras", name: "Cerebras", icon: "cerebras", keywords: ["cerebras"]),
        .init(id: "opencode", name: "OpenCode", icon: "opencode", keywords: ["opencode"]),
        .init(id: "openrouter", name: "OpenRouter", icon: "openrouter", keywords: ["openrouter"]),
        .init(id: "together", name: "Together AI", icon: "together", keywords: ["together"]),
        .init(id: "fireworks", name: "Fireworks AI", icon: "fireworks", keywords: ["fireworks"]),
        .init(id: "vercel", name: "Vercel", icon: "vercel", keywords: ["vercel"]),
        .init(id: "nebius", name: "Nebius", icon: "nebius", keywords: ["nebius"]),
        .init(id: "featherless", name: "Featherless AI", icon: "featherless", keywords: ["featherless"]),
        .init(id: "cohere", name: "Cohere", icon: "cohere", keywords: ["cohere"]),
        .init(id: "perplexity", name: "Perplexity", icon: "perplexity", keywords: ["perplexity"]),
        .init(id: "github", name: "GitHub", icon: "github", keywords: ["github"]),
        .init(id: "huggingface", name: "Hugging Face", icon: "huggingface",
              keywords: ["huggingface", "hugging"]),
        .init(id: "routing-run", name: "Routing.Run", icon: "routing-run",
              keywords: ["routing", "routingrun"]),
        // Inference plan vendors and API key providers, so a key filed
        // under their name shows the logo too.
        .init(id: "tencent", name: "Tencent", icon: "tencentcloud", keywords: ["tencent", "hunyuan", "tokenhub"]),
        .init(id: "baidu", name: "Baidu", icon: "baiducloud", keywords: ["baidu", "qianfan", "ernie"]),
        .init(id: "huawei", name: "Huawei", icon: "huaweicloud", keywords: ["huawei", "pangu", "modelarts"]),
        .init(id: "iflytek", name: "Astron (iFlytek)", icon: "iflytek", keywords: ["iflytek", "xfyun", "astron"]),
        .init(id: "sensetime", name: "SenseTime", icon: "sensenova", keywords: ["sensetime", "sensenova"]),
        .init(id: "kuaishou", name: "Kuaishou", icon: "kwaikat",
              keywords: ["kuaishou", "kwaikat", "kwaipilot", "streamlake"]),
        .init(id: "ollama", name: "Ollama", icon: "ollama", keywords: ["ollama"]),
        .init(id: "poe", name: "Poe", icon: "poe", keywords: ["poe"]),
        .init(id: "synthetic", name: "Synthetic", icon: "synthetic", keywords: ["synthetic"]),
        .init(id: "zenmux", name: "ZenMux", icon: "zenmux", keywords: ["zenmux"]),
        .init(id: "atlascloud", name: "Atlas Cloud", icon: "atlascloud", keywords: ["atlascloud"]),
        .init(id: "taotoken", name: "TaoToken", icon: "taotoken", keywords: ["taotoken"]),
        .init(id: "elevenlabs", name: "ElevenLabs", icon: "elevenlabs", keywords: ["elevenlabs"]),
        .init(id: "jdcloud", name: "JD Cloud", icon: "jdcloud", keywords: ["jdcloud", "joybuilder"]),
        .init(id: "mthreads", name: "Moore Threads", icon: "mthreads", keywords: ["mthreads", "moorethreads", "kuae"]),
        .init(id: "volcengine", name: "Volcengine", icon: "ark", keywords: ["volcengine", "byteplus", "ark"]),
        .init(id: "ucloud", name: "UCloud", icon: "ucloud", keywords: ["ucloud", "compshare"]),
        .init(id: "unicom", name: "China Unicom", icon: "unicom", keywords: ["unicom", "cucloud"]),
        .init(id: "ctyun", name: "China Telecom", icon: "ctyun", keywords: ["ctyun", "telecom"]),
        .init(id: "scnet", name: "SCNet", icon: "scnet", keywords: ["scnet"]),
        .init(id: "charm", name: "Charm", icon: "charm", keywords: ["charm", "hyper"]),
        .init(id: "llmgateway", name: "LLM Gateway", icon: "llmgateway", keywords: ["llmgateway", "devpass"]),
        .init(id: "standardcompute", name: "Standard Compute", icon: "standardcompute", keywords: ["stdcmpt"]),
        .init(id: "cheapestinference", name: "CheapestInference", icon: "cheapestinference",
              keywords: ["cheapestinference"]),
        // The Models page's benchmark source; a key filed under its name
        // shows the mark too. "artificial" alone is too generic to claim,
        // so only the run-together words match, through the id itself.
        .init(id: "artificialanalysis", name: "Artificial Analysis", icon: "artificial-analysis"),
    ]

    /// Anything no brand claims: a dark grey that recedes behind the
    /// named brands.
    public static let other = ModelBrand(id: "other", name: "Other", color: HarnessRGB(hex: "#45454b")!)

    private static let byId = Dictionary(all.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
    private static let byKeyword: [String: ModelBrand] = {
        var map: [String: ModelBrand] = [:]
        for brand in all {
            for word in brand.keywords + [brand.id] where map[word] == nil { map[word] = brand }
        }
        return map
    }()
    private static let patterns: [(NSRegularExpression, ModelBrand)] = all.flatMap { brand in
        brand.models.map { (try! NSRegularExpression(pattern: "^(?:\($0))"), brand) }
    }

    public static func brand(id: String) -> ModelBrand? { byId[id] }

    /// The brand a single word names: a creator slug like "x-ai" or a
    /// short name like "kimi".
    public static func forKeyword(_ word: String) -> ModelBrand? {
        byKeyword[word.lowercased()]
    }

    /// The bundled price supplement, for its aliases.
    private static let supplement = (try? PricingSupplement.bundled()) ?? PricingSupplement()

    /// The real name the bundled price supplement aliases a logged id to,
    /// such as "claude-haiku-4-5" for Devin's "MODEL_PRIVATE_11"; nil when
    /// the name is its own.
    public static func resolvedName(_ model: String) -> String? {
        supplement.aliasTarget(of: model)
    }

    /// The brand of a logged model name such as "claude-opus-5-5" or
    /// "openrouter/qwen3-coder", through any alias first; `other` when
    /// nothing matches.
    public static func forModel(_ model: String) -> ModelBrand {
        var name = (resolvedName(model) ?? model).lowercased()
        if let slash = name.lastIndex(of: "/") { name = String(name[name.index(after: slash)...]) }
        let range = NSRange(name.startIndex..., in: name)
        return patterns.first { $0.0.firstMatch(in: name, range: range) != nil }?.1 ?? other
    }

    /// The brand a free-text heading names, such as "# Moonshot / Kimi" or
    /// "x.ai": each whole word in order, then the words run together, so
    /// a name inside another word never matches.
    public static func inText(_ text: String) -> ModelBrand? {
        let words = text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
        for word in words + [words.joined()] {
            if let brand = byKeyword[word] { return brand }
        }
        return nil
    }
}
