import AppKit
import QuiverCore
import SwiftUI

/// A harness's logo, drawn as a single-colour mark in the harness colour
/// (grey for harnesses without one). A harness with no bundled logo gets
/// its initial on a rounded square, so rows still line up.
struct HarnessIcon: View {
    var harness: String
    var size: CGFloat = 16
    /// Overrides the harness colour, for example to match surrounding text.
    var tint: Color?

    var body: some View {
        // The four charted harnesses keep their chart colour; the rest wear
        // the text colour rather than a grey that reads as disabled.
        let color = tint ?? (Theme.harnessOrder.contains(harness) ? Theme.harness(harness) : Theme.textPrimary)
        Group {
            if let image = Self.image(for: harness) {
                Image(nsImage: image)
                    .renderingMode(.template)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
                    .foregroundStyle(color)
            } else {
                Text(UsageFormat.harness(harness).prefix(1).uppercased())
                    .font(.system(size: size * 0.6, weight: .semibold))
                    .foregroundStyle(Theme.card)
                    .frame(width: size, height: size)
                    .background(color, in: .rect(cornerRadius: size * 0.25))
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    @MainActor private static var cache: [String: NSImage] = [:]

    @MainActor static func image(for harness: String) -> NSImage? {
        if let cached = cache[harness] { return cached }
        guard let url = HarnessIcons.url(for: harness), let image = NSImage(contentsOf: url) else { return nil }
        image.isTemplate = true
        cache[harness] = image
        return image
    }
}
