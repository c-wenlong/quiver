import Foundation

/// A model lab as the Models page draws it: a display name, a bar colour
/// and, when the app bundles one, a logo. A view of `ModelBrands`, which
/// holds every brand: the AA entry's `model_creator.slug` decides, and
/// the model's name when the slug is unknown.
public struct ModelVendor: Sendable, Equatable {
    public var id: String
    public var name: String
    /// The bar colour as components; the app wraps it in `Color`.
    public var color: HarnessRGB
    /// The bundled harness icon id, when one doubles as the lab's logo.
    public var icon: String?

    init(_ brand: ModelBrand) {
        id = brand.id
        name = brand.name
        color = brand.color ?? ModelBrands.other.color!
        icon = brand.icon
    }

    /// The vendor an AA model belongs to: its creator slug first, then the
    /// model name, else "Other". Both read `ModelBrands`.
    public static func of(_ model: AALanguageModel) -> ModelVendor {
        ModelVendor(ModelBrands.forKeyword(model.creatorSlug) ?? ModelBrands.forModel(model.name))
    }

    /// The vendor a logged (possibly unmatched) name belongs to, for the
    /// card's "Anthropic · used by …" line when nothing matched.
    public static func of(logged name: String) -> ModelVendor {
        ModelVendor(ModelBrands.forModel(name))
    }
}
