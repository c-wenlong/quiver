import Foundation

/// Who sells a model, decided from its name alone (any `provider/` prefix
/// stripped first, case-insensitive) by `ModelBrands`. The Pricing page
/// groups models by provider; the id drives the group icon.
public enum ModelProvider {
    public struct Provider: Sendable, Equatable {
        public var id: String
        public var name: String
        /// The bundled harness icon id, when there is one.
        public var icon: String?
    }

    /// The brand `ModelBrands` reads from the name.
    public static func of(_ model: String) -> Provider {
        let brand = ModelBrands.forModel(model)
        return Provider(id: brand.id, name: brand.name, icon: brand.icon)
    }
}
