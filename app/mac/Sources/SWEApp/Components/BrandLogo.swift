import SWECore
import SwiftUI

/// A model's lab logo, from `ModelBrands` (the same one the Models chart
/// draws under each bar), or an empty slot of the same size so names stay
/// aligned. Every view that names a model puts this before the name.
struct BrandLogo: View {
    var icon: String?
    var name: String
    var size: CGFloat = 16

    init(vendor: ModelVendor, size: CGFloat = 16) {
        icon = vendor.icon
        name = vendor.name
        self.size = size
    }

    /// For a logged model name such as "claude-opus-5-5".
    init(model: String, size: CGFloat = 16) {
        self.init(vendor: ModelVendor.of(logged: model), size: size)
    }

    var body: some View {
        Group {
            if let icon, HarnessIcon.exists(for: icon) {
                HarnessIcon(harness: icon, size: size)
            } else {
                Color.clear.frame(width: size, height: size)
            }
        }
        .help(name)
        .accessibilityLabel(name)
    }
}
