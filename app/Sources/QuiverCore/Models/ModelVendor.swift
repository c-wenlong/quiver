import Foundation

/// A model lab as the Models page draws it: a display name, a bar colour
/// and, when the app bundles one, a harness icon. Two names map in: the AA
/// entry's `model_creator.slug` and, when that misses, the logged name's
/// `ModelProvider` read.
public struct ModelVendor: Sendable, Equatable {
    public var id: String
    public var name: String
    /// The bar colour as components; the app wraps it in `Color`.
    public var color: HarnessRGB
    /// The bundled harness icon id, when one doubles as the lab's logo.
    public var icon: String?

    /// Anthropic bars take the app's Claude harness colour; the rest are
    /// the brand picks the mockup settled on.
    private static let catalog: [String: ModelVendor] = [
        "anthropic": .init(id: "anthropic", name: "Anthropic",
                           color: HarnessColor.resolve(id: "claude", primaryColor: nil), icon: "claude"),
        "openai": .init(id: "openai", name: "OpenAI",
                        color: HarnessRGB(hex: "#c9c9cf")!, icon: "openai"),
        "google": .init(id: "google", name: "Google",
                        color: HarnessRGB(hex: "#3fa35b")!, icon: "gemini"),
        "xai": .init(id: "xai", name: "xAI",
                     color: HarnessRGB(hex: "#8a73e8")!, icon: "grok"),
        "zai": .init(id: "zai", name: "Z.ai",
                     color: HarnessRGB(hex: "#4a7cf0")!, icon: "zai"),
        "moonshot": .init(id: "moonshot", name: "Moonshot",
                          color: HarnessRGB(hex: "#5b5f6b")!, icon: "kimi"),
        "minimax": .init(id: "minimax", name: "MiniMax",
                         color: HarnessRGB(hex: "#e2466e")!, icon: "minimax"),
        "deepseek": .init(id: "deepseek", name: "DeepSeek",
                          color: HarnessRGB(hex: "#5b6ef0")!, icon: "deepseek"),
        "meta": .init(id: "meta", name: "Meta",
                      color: HarnessRGB(hex: "#2f7bf6")!, icon: nil),
        "mistral": .init(id: "mistral", name: "Mistral",
                         color: HarnessRGB(hex: "#f26b1d")!, icon: "mistral-vibe"),
        "alibaba": .init(id: "alibaba", name: "Alibaba",
                         color: HarnessRGB(hex: "#ff6a00")!, icon: "qwen-code"),
        "cognition": .init(id: "cognition", name: "Cognition",
                           color: HarnessRGB(hex: "#6b6b72")!, icon: "devin"),
    ]

    private static let other = ModelVendor(id: "other", name: "Other",
                                           color: HarnessRGB(hex: "#6b6b72")!, icon: nil)

    /// AA's creator slugs where they differ from our ids.
    private static let creators = [
        "x-ai": "xai", "z-ai": "zai", "z.ai": "zai",
        "moonshot-ai": "moonshot", "moonshotai": "moonshot",
        "meta-llama": "meta", "mistral-ai": "mistral",
        "qwen": "alibaba", "deepseek-ai": "deepseek",
        "cognition-ai": "cognition", "cognition_ai": "cognition",
    ]

    /// The vendor an AA model belongs to: the creator slug first, then the
    /// name read `ModelProvider` gives, else "other".
    public static func of(_ model: AALanguageModel) -> ModelVendor {
        let slug = model.creatorSlug.lowercased()
        if let id = creators[slug] ?? (catalog[slug] != nil ? slug : nil) {
            return catalog[id] ?? other
        }
        let named = ModelProvider.of(model.name).id
        if let mapped = creators[named] ?? (catalog[named] != nil ? named : nil) {
            return catalog[mapped] ?? other
        }
        return other
    }

    /// The vendor a logged (possibly unmatched) name belongs to, for the
    /// card's "Anthropic · used by …" line when nothing matched.
    public static func of(logged name: String) -> ModelVendor {
        let id = ModelProvider.of(name).id
        return creators[id].flatMap { catalog[$0] } ?? catalog[id] ?? other
    }
}
