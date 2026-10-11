import SwiftUI

/// The look, adapted from TokenTracker's DESIGN.md: quiet dark surfaces with
/// a green-tinted neutral ramp, one emerald accent used sparingly, cards
/// with a single hairline border, numbers in tabular figures.
enum Theme {
    static let accent = Color(red: 0.063, green: 0.725, blue: 0.506)      // #10b981
    static let warning = Color(red: 0.961, green: 0.620, blue: 0.043)     // #f59e0b
    static let danger = Color(red: 0.937, green: 0.267, blue: 0.267)      // #ef4444

    static let cardRadius: CGFloat = 14
    static let gutter: CGFloat = 20

    /// One fixed hue per harness, validated together for colour-blind
    /// separation on both the dark and light surface. Anything else is grey.
    static func harness(_ id: String) -> Color {
        switch id {
        case "claude": Color(hex: "#cf6848")
        case "codex": Color(hex: "#3b7ff0")
        case "opencode": Color(hex: "#139f8b")
        case "devin": Color(hex: "#c4860f")
        default: .gray
        }
    }

    /// Order the harnesses always appear in, so a colour never moves.
    static let harnessOrder = ["claude", "codex", "opencode", "devin"]

    /// Bar colour for a used percentage: calm, then amber at 70, red at 90.
    static func usage(_ percent: Int) -> Color {
        percent >= 90 ? danger : percent >= 70 ? warning : accent
    }
}

/// A card: one hairline border on a quiet fill, no nesting.
struct Card<Content: View>: View {
    var title: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title {
                Text(title.uppercased())
                    .font(.caption.weight(.medium))
                    .tracking(0.6)
                    .foregroundStyle(.secondary)
            }
            content
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary.opacity(0.6), in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(
            RoundedRectangle(cornerRadius: Theme.cardRadius)
                .strokeBorder(.separator, lineWidth: 1)
        )
    }
}

/// Page scaffold: large title, subtitle, scrolling content.
struct Page<Content: View>: View {
    var title: String
    var subtitle: String
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: Theme.gutter) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.largeTitle.weight(.semibold))
                    Text(subtitle).foregroundStyle(.secondary)
                }
                content
            }
            .padding(28)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
