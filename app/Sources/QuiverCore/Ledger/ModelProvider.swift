import Foundation

/// Who sells a model, decided from its name alone (any `provider/` prefix
/// stripped first, case-insensitive). The Pricing page groups models by
/// provider; the id drives the group icon.
public enum ModelProvider {
    public struct Provider: Sendable, Equatable {
        public var id: String
        public var name: String
        /// The bundled harness icon id, when there is one.
        public var icon: String?
    }

    public static func of(_ model: String) -> Provider {
        var name = model.lowercased()
        if let slash = name.lastIndex(of: "/") {
            name = String(name[name.index(after: slash)...])
        }
        func provider(_ id: String, _ titled: String, _ icon: String?) -> Provider {
            Provider(id: id, name: titled, icon: icon)
        }
        if name.hasPrefix("claude") { return provider("anthropic", "Anthropic", "claude") }
        if name.hasPrefix("gpt-") || name.hasPrefix("codex")
            || (name.hasPrefix("o") && name.dropFirst().first?.isNumber == true) {
            return provider("openai", "OpenAI", "openai")
        }
        if name.hasPrefix("gemini") || name.hasPrefix("gemma") {
            return provider("google", "Google", "gemini")
        }
        // Grok Bot before Grok: `grok-bot-*` is Cursor's bot, not xAI's API.
        if name.hasPrefix("grok-bot") { return provider("grok-bot", "Grok Bot", "grok") }
        if name.hasPrefix("grok") { return provider("xai", "xAI", "grok") }
        if name.hasPrefix("swe-") || name.hasPrefix("compactor") || name.hasPrefix("summarizer") {
            return provider("cognition", "Cognition", "devin")
        }
        if name.hasPrefix("glm") { return provider("zai", "Z.ai", "zai") }
        if name.hasPrefix("deepseek") { return provider("deepseek", "DeepSeek", "deepseek") }
        if name.hasPrefix("kimi") || name.hasPrefix("moonshot") {
            return provider("moonshot", "Moonshot", "kimi")
        }
        if name.hasPrefix("minimax") { return provider("minimax", "MiniMax", "minimax") }
        if name.hasPrefix("qwen") { return provider("qwen", "Qwen", "qwen-code") }
        if name.hasPrefix("mistral") || name.hasPrefix("devstral") || name.hasPrefix("codestral") {
            return provider("mistral", "Mistral", "mistral-vibe")
        }
        if name.hasPrefix("composer") || name.hasPrefix("cursor-") {
            return provider("cursor", "Cursor", "cursor")
        }
        return provider("other", "Other", nil)
    }
}
