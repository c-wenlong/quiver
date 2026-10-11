import AppKit
import QuiverCore
import SwiftUI

/// A harness's logo as TokenTracker shows it: brand colours where the logo
/// has them, otherwise a single-colour mark in the text colour. A harness
/// with no bundled logo gets its initial on a rounded square, so rows
/// still line up.
struct HarnessIcon: View {
    var harness: String
    var size: CGFloat = 16

    var body: some View {
        Group {
            if let (image, isColor) = Self.image(for: harness) {
                if isColor {
                    Image(nsImage: image).resizable().interpolation(.high).scaledToFit()
                } else {
                    Image(nsImage: image)
                        .renderingMode(.template)
                        .resizable()
                        .interpolation(.high)
                        .scaledToFit()
                        .foregroundStyle(Theme.textPrimary)
                }
            } else {
                Text(UsageFormat.harness(harness).prefix(1).uppercased())
                    .font(.system(size: size * 0.6, weight: .semibold))
                    .foregroundStyle(Theme.card)
                    .frame(width: size, height: size)
                    .background(Theme.textSecondary, in: .rect(cornerRadius: size * 0.25))
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    @MainActor private static var cache: [String: (NSImage, Bool)] = [:]

    @MainActor static func image(for harness: String) -> (NSImage, Bool)? {
        if let cached = cache[harness] { return cached }
        guard let icon = HarnessIcons.icon(for: harness), let image = NSImage(contentsOf: icon.url) else { return nil }
        image.isTemplate = !icon.isColor
        cache[harness] = (image, icon.isColor)
        return (image, icon.isColor)
    }
}

/// The harness's chart colour, beside its logo wherever a chart shows it:
/// a brand logo (Devin in white, OpenCode in purple) isn't the colour of
/// its segment, so this is what ties a legend entry to the chart.
struct ChartSwatch: View {
    var harness: String
    var size: CGFloat = 8

    var body: some View {
        RoundedRectangle(cornerRadius: size / 4)
            .fill(Theme.harness(harness))
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
