import AppKit
import SWECore
import SwiftUI

/// A harness's logo as TokenTracker shows it: brand colours where the logo
/// has them, otherwise a single-colour mark in the text colour. A raw id
/// such as "codex-acc2" or "claude-desktop" draws its base harness's logo.
/// A harness with no bundled logo draws nothing in the same square, so rows
/// still line up and the name beside it says which harness it is; callers
/// that would rather drop the square check `exists(for:)`.
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
                Color.clear
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }

    /// Whether `harness` (or the harness a raw id like "codex-acc2" names)
    /// has a bundled logo.
    static func exists(for harness: String) -> Bool {
        HarnessIcons.logo(for: harness) != nil
    }

    @MainActor private static var cache: [String: (NSImage, Bool)] = [:]

    @MainActor static func image(for harness: String) -> (NSImage, Bool)? {
        if let cached = cache[harness] { return cached }
        guard let icon = HarnessIcons.logo(for: harness), let image = NSImage(contentsOf: icon.url) else { return nil }
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

/// A harness as its logo, plus a word only where the logo alone is
/// ambiguous ("CLI" and "Desktop" for Claude's two, "acc2" for a second
/// account). The full name is the tooltip and what VoiceOver reads. The word
/// takes the surrounding font and colour.
struct HarnessMarkView: View {
    var harness: String
    var size: CGFloat = 16

    var body: some View {
        let mark = HarnessMark(harness)
        HStack(spacing: 4) {
            if let icon = mark.icon { HarnessIcon(harness: icon, size: size) }
            if let word = mark.word { Text(word).lineLimit(1) }
        }
        .help(mark.name)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(mark.name)
    }
}

/// Full harness names for prose, menus and VoiceOver: "Claude Code, Codex".
func harnessNames(_ harnesses: [String]) -> String {
    harnesses.map { HarnessMark($0).name }.joined(separator: ", ")
}
