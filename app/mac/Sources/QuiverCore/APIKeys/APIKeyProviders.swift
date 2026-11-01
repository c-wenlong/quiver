import Foundation

/// A provider the "Add key" sheet offers, mirrored from
/// `src/swe/providers/defaults.py` (`APIKeyProvidersSyncTests` fails
/// when the two drift apart). The slug is defaults.py's key, the env
/// vars its `env_vars` in the same order, and the key URL its `url`.
public struct APIKeyProvider: Equatable, Sendable, Identifiable {
    /// The slug from defaults.py, e.g. "together_ai".
    public let id: String
    /// The proper display name: "OpenAI", "Together AI", "xAI".
    public let name: String
    /// Same order as defaults.py; the first is the default variable.
    public let envVars: [String]
    /// defaults.py's "url": where the provider hands out keys.
    public let keyURL: URL

    public init(id: String, name: String, envVars: [String], keyURL: URL) {
        self.id = id
        self.name = name
        self.envVars = envVars
        self.keyURL = keyURL
    }

    public var logo: String? { APIKeyProviderLogo.logoID(for: name) }
    /// Whether Quiver can test a key for this provider.
    public var canCheck: Bool {
        envVars.first.map { APIKeyProbe.probe(forName: $0) != nil } ?? false
    }
}

public enum APIKeyProviders {
    /// Every entry in defaults.py's DEFAULT_PROVIDERS, same order.
    public static let all: [APIKeyProvider] = [
        APIKeyProvider(id: "openai", name: "OpenAI",
                       envVars: ["OPENAI_API_KEY"],
                       keyURL: URL(string: "https://platform.openai.com/api-keys")!),
        APIKeyProvider(id: "anthropic", name: "Anthropic",
                       envVars: ["ANTHROPIC_API_KEY"],
                       keyURL: URL(string: "https://console.anthropic.com/settings/keys")!),
        APIKeyProvider(id: "gemini", name: "Google Gemini",
                       envVars: ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
                       keyURL: URL(string: "https://aistudio.google.com/app/apikey")!),
        APIKeyProvider(id: "deepseek", name: "DeepSeek",
                       envVars: ["DEEPSEEK_API_KEY"],
                       keyURL: URL(string: "https://platform.deepseek.com/api_keys")!),
        APIKeyProvider(id: "zai", name: "ZAI (01.ai)",
                       envVars: ["ZAI_API_KEY"],
                       keyURL: URL(string: "https://z.ai/")!),
        APIKeyProvider(id: "minimax", name: "MiniMax",
                       envVars: ["MINIMAX_API_KEY"],
                       keyURL: URL(string: "https://minimax.io/")!),
        APIKeyProvider(id: "kimi", name: "Kimi (Moonshot AI)",
                       envVars: ["KIMI_API_KEY", "MOONSHOT_API_KEY"],
                       keyURL: URL(string: "https://platform.moonshot.ai/")!),
        APIKeyProvider(id: "qwen", name: "Qwen (Alibaba)",
                       envVars: ["QWEN_API_KEY", "DASHSCOPE_API_KEY"],
                       keyURL: URL(string: "https://bailian.console.aliyun.com/")!),
        APIKeyProvider(id: "mimo", name: "MiMo (Xiaomi)",
                       envVars: ["MIMO_API_KEY"],
                       keyURL: URL(string: "https://platform.xiaomi.com/")!),
        APIKeyProvider(id: "xai", name: "xAI (Grok)",
                       envVars: ["XAI_API_KEY"],
                       keyURL: URL(string: "https://console.x.ai/")!),
        APIKeyProvider(id: "stepfun", name: "StepFun",
                       envVars: ["STEPFUN_API_KEY"],
                       keyURL: URL(string: "https://platform.stepfun.ai/")!),
        APIKeyProvider(id: "groq", name: "Groq",
                       envVars: ["GROQ_API_KEY"],
                       keyURL: URL(string: "https://console.groq.com/keys")!),
        APIKeyProvider(id: "upstage", name: "Upstage",
                       envVars: ["UPSTAGE_API_KEY"],
                       keyURL: URL(string: "https://console.upstage.ai/")!),
        APIKeyProvider(id: "cerebras", name: "Cerebras",
                       envVars: ["CEREBRAS_API_KEY"],
                       keyURL: URL(string: "https://cloud.cerebras.ai/")!),
        APIKeyProvider(id: "mistral", name: "Mistral",
                       envVars: ["MISTRAL_API_KEY"],
                       keyURL: URL(string: "https://console.mistral.ai/api-keys/")!),
        APIKeyProvider(id: "routing_run", name: "Routing.Run",
                       envVars: ["ROUTING_RUN_API_KEY"],
                       keyURL: URL(string: "https://routing.run/")!),
        APIKeyProvider(id: "opencode_zen", name: "Opencode Zen",
                       envVars: ["OPENCODE_ZEN_API_KEY"],
                       keyURL: URL(string: "https://opencode.ai/zen")!),
        APIKeyProvider(id: "openrouter", name: "OpenRouter",
                       envVars: ["OPENROUTER_API_KEY"],
                       keyURL: URL(string: "https://openrouter.ai/keys")!),
        APIKeyProvider(id: "together_ai", name: "Together AI",
                       envVars: ["TOGETHER_AI_API_KEY", "TOGETHER_API_KEY"],
                       keyURL: URL(string: "https://api.together.xyz/")!),
        APIKeyProvider(id: "fireworks_ai", name: "Fireworks AI",
                       envVars: ["FIREWORKS_AI_API_KEY"],
                       keyURL: URL(string: "https://fireworks.ai/")!),
        APIKeyProvider(id: "vercel_gateway", name: "Vercel Gateway",
                       envVars: ["VERCEL_GATEWAY_API_KEY"],
                       keyURL: URL(string: "https://vercel.com/docs/ai-gateway")!),
        APIKeyProvider(id: "nebius", name: "Nebius",
                       envVars: ["NEBIUS_API_KEY"],
                       keyURL: URL(string: "https://studio.nebius.com/")!),
        APIKeyProvider(id: "featherless", name: "Featherless AI",
                       envVars: ["FEATHERLESS_API_KEY"],
                       keyURL: URL(string: "https://featherless.ai/")!),
        APIKeyProvider(id: "cohere", name: "Cohere",
                       envVars: ["COHERE_API_KEY"],
                       keyURL: URL(string: "https://dashboard.cohere.com/api-keys")!),
        APIKeyProvider(id: "perplexity", name: "Perplexity",
                       envVars: ["PERPLEXITY_API_KEY"],
                       keyURL: URL(string: "https://www.perplexity.ai/settings/api")!),
        APIKeyProvider(id: "github", name: "GitHub",
                       envVars: ["GITHUB_TOKEN", "GH_TOKEN"],
                       keyURL: URL(string: "https://github.com/settings/tokens")!),
        APIKeyProvider(id: "huggingface", name: "Hugging Face",
                       envVars: ["HUGGINGFACE_HUB_TOKEN", "HF_TOKEN"],
                       keyURL: URL(string: "https://huggingface.co/settings/tokens")!),
    ]

    /// The provider one of whose variables is `name`, if any.
    public static func provider(forVariable name: String) -> APIKeyProvider? {
        all.first { $0.envVars.contains(name) }
    }

    /// A provider from the picker's id, nil for "other" or none picked.
    public static func provider(forID id: String) -> APIKeyProvider? {
        all.first { $0.id == id }
    }

    /// The heading a new key for `provider` goes under: an existing group
    /// in `rows` that already holds one of its variables, else an existing
    /// group whose heading reads as the name or slug ignoring case and
    /// spaces/underscores (so "# ANTHROPIC" and "# Together AI" match),
    /// else `provider.name`.
    public static func heading(for provider: APIKeyProvider, in rows: [APIKeysModel.Row]) -> String {
        if let row = rows.first(where: { provider.envVars.contains($0.name) }) {
            return row.provider
        }
        var groups: [String] = []
        for row in rows where !groups.contains(row.provider) {
            groups.append(row.provider)
        }
        let wanted = Set([provider.name, provider.id].map(normalize))
        if let match = groups.first(where: { wanted.contains(normalize($0)) }) {
            return match
        }
        return provider.name
    }

    /// "Together AI", "together_ai" and "TOGETHER AI" all fold to the same key.
    private static func normalize(_ text: String) -> String {
        text.lowercased().filter { $0 != " " && $0 != "_" }
    }
}
