import AppKit
import SwiftUI

/// The look, from the TokenTracker style package in the design-systems repo
/// (`systems/tokentracker/tokens.json`; values copied from there, not from
/// memory). Neutral surfaces one step apart with a hairline, one green used
/// for money and "on" states only, small quiet controls, sentence case
/// everywhere, and a visible focus ring (TokenTracker has none).
enum Theme {
    // MARK: Colour roles (tokens.json `light` / `dark`)

    static let wash = Color(light: "#f5f5f5", dark: "#0a0a0a")
    static let panel = Color(light: "#fafafa", dark: "#171717")
    static let card = Color(light: "#ffffff", dark: "#171717")
    static let field = Color(light: "#ffffff", dark: "#171717")
    static let subtle = Color(light: "#fafafa", dark: "#262626")
    static let hover = Color(light: "#e5e5e580", dark: "#26262699")
    static let selected = Color(light: "#f5f5f5", dark: "#262626")
    static let navActive = Color(light: "#e5e5e5b2", dark: "#262626")
    static let segmentTrack = Color(light: "#fafafa", dark: "#171717")
    static let segmentSelected = Color(light: "#ffffff", dark: "#262626")
    static let rowHover = Color(light: "#fafafa80", dark: "#26262680")

    static let textPrimary = Color(light: "#0a0a0a", dark: "#fafafa")
    static let textSecondary = Color(light: "#737373", dark: "#a3a3a3")
    static let textNav = Color(light: "#525252", dark: "#a3a3a3")
    static let textMuted = Color(light: "#737373", dark: "#737373")

    static let border = Color(light: "#e5e5e5", dark: "#262626")
    static let borderStrong = Color(light: "#d4d4d4", dark: "#404040")

    /// Money and "on": cost figures, usage fill, heatmap, switches.
    static let accent = Color(light: "#059669", dark: "#059669")
    static let accentFill = Color(light: "#10b981", dark: "#10b981")

    static let primaryBackground = Color(light: "#171717", dark: "#fafafa")
    static let primaryForeground = Color(light: "#ffffff", dark: "#171717")
    static let secondaryBackground = Color(light: "#fafafa", dark: "#171717")

    static let usageTrack = Color(light: "#f5f5f5", dark: "#40404080")
    static let success = Color(light: "#10b981", dark: "#34d399")
    static let warning = Color(light: "#f59e0b", dark: "#fbbf24")
    static let danger = Color(light: "#ef4444", dark: "#f87171")

    /// Heatmap steps, empty to busiest.
    static let heatmap: [Color] = [
        Color(light: "#ebedf0", dark: "#121212"),
        Color(light: "#a7f3d0", dark: "#065f46"),
        Color(light: "#6ee7b7", dark: "#059669"),
        Color(light: "#34d399", dark: "#10b981"),
        Color(light: "#10b981", dark: "#34d399"),
    ]

    // MARK: Type roles (tokens.json `core.typography`)

    static let pageTitle = Font.system(size: 36, weight: .semibold)
    static let pageSubtitle = Font.system(size: 16)
    static let sectionTitle = Font.system(size: 20, weight: .semibold)
    static let display = Font.system(size: 72, weight: .bold)
    static let figure = Font.system(size: 20, weight: .bold)
    static let cardTitle = Font.system(size: 14, weight: .medium)
    static let rowTitle = Font.system(size: 14, weight: .medium)
    static let bodySmall = Font.system(size: 14)
    static let nav = Font.system(size: 13)
    static let control = Font.system(size: 12, weight: .medium)
    static let caption = Font.system(size: 12)
    static let tableHeader = Font.system(size: 12, weight: .medium)

    // MARK: Shape and size (tokens.json `core.radius`, `size`, `space`)

    static let panelRadius: CGFloat = 16
    static let cardRadius: CGFloat = 16
    static let controlRadius: CGFloat = 8
    static let cardPadding: CGFloat = 20
    static let gutter: CGFloat = 16
    static let sidebarWidth: CGFloat = 220
    static let navRowHeight: CGFloat = 32
    static let controlHeight: CGFloat = 28
    static let iconButton: CGFloat = 32
    static let usageTrackHeight: CGFloat = 6
    static let heatmapCell: CGFloat = 12

    static let focusRing: CGFloat = 2
    /// The keyboard focus ring: neutral, since green is kept for money and "on".
    static let focus = textPrimary

    // MARK: Data colours

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
        percent >= 90 ? danger : percent >= 70 ? warning : accentFill
    }
}

extension Color {
    /// A colour that follows the appearance, from `#rrggbb` or `#rrggbbaa`.
    init(light: String, dark: String) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
            return NSColor(hexString: isDark ? dark : light)
        })
    }
}

extension NSColor {
    convenience init(hexString: String) {
        var text = hexString.trimmingCharacters(in: .whitespaces)
        if text.hasPrefix("#") { text.removeFirst() }
        let value = UInt64(text, radix: 16) ?? 0
        let hasAlpha = text.count == 8
        let r = Double((value >> (hasAlpha ? 24 : 16)) & 0xff) / 255
        let g = Double((value >> (hasAlpha ? 16 : 8)) & 0xff) / 255
        let b = Double((value >> (hasAlpha ? 8 : 0)) & 0xff) / 255
        let a = hasAlpha ? Double(value & 0xff) / 255 : 1
        self.init(srgbRed: r, green: g, blue: b, alpha: a)
    }
}

// MARK: - Surfaces

/// A card: one hairline on `bg.card`, 16pt radius, 20pt padding, and an
/// optional sentence-case title at 14/500.
struct Card<Content: View, Accessory: View>: View {
    var title: String?
    var subtitle: String?
    @ViewBuilder var accessory: Accessory
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if title != nil || subtitle != nil {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        if let title { Text(title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary) }
                        if let subtitle { Text(subtitle).font(Theme.caption).foregroundStyle(Theme.textSecondary) }
                    }
                    Spacer(minLength: 8)
                    accessory
                }
            }
            content
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card, in: .rect(cornerRadius: Theme.cardRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.cardRadius).strokeBorder(Theme.border, lineWidth: 1))
    }
}

extension Card where Accessory == EmptyView {
    init(title: String? = nil, subtitle: String? = nil, @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, accessory: { EmptyView() }, content: content)
    }
}

/// Page scaffold inside the content panel: 36/40 title, 16/24 subtitle,
/// actions top right, content up to 1152pt wide and centred.
struct Page<Content: View, Actions: View>: View {
    var title: String
    var subtitle: String
    var maxWidth: CGFloat = 1152
    @ViewBuilder var actions: Actions
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                PageHeader(title: title, subtitle: subtitle) { actions }
                content
            }
            .padding(.horizontal, 40)
            .padding(.vertical, 36)
            .frame(maxWidth: maxWidth, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .scrollContentBackground(.hidden)
    }
}

extension Page where Actions == EmptyView {
    init(title: String, subtitle: String, maxWidth: CGFloat = 1152, @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, maxWidth: maxWidth, actions: { EmptyView() }, content: content)
    }
}

struct PageHeader<Actions: View>: View {
    var title: String
    var subtitle: String
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(Theme.pageTitle).tracking(-0.9).foregroundStyle(Theme.textPrimary)
                Text(subtitle).font(Theme.pageSubtitle).foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 16)
            HStack(spacing: 8) { actions }
        }
    }
}

// MARK: - Controls

/// Black in light, near-white in dark; never green.
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        StyledButton(configuration: configuration, background: Theme.primaryBackground,
                     foreground: Theme.primaryForeground, border: .clear)
    }
}

/// The quiet default: field fill and a strong hairline.
struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        StyledButton(configuration: configuration, background: Theme.secondaryBackground,
                     foreground: Theme.textPrimary, border: Theme.borderStrong)
    }
}

private struct StyledButton: View {
    let configuration: ButtonStyleConfiguration
    var background: Color
    var foreground: Color
    var border: Color
    @Environment(\.isEnabled) private var isEnabled
    @State private var hovering = false

    var body: some View {
        configuration.label
            .font(Theme.control)
            .labelStyle(.titleAndIcon)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight + 4)
            .foregroundStyle(foreground)
            .background(background.opacity(configuration.isPressed ? 0.85 : 1),
                        in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                .strokeBorder(hovering ? Theme.textSecondary.opacity(0.6) : border, lineWidth: 1))
            .opacity(isEnabled ? 1 : 0.45)
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
            .onHover { hovering = $0 }
            .focusRing(cornerRadius: Theme.controlRadius)
    }
}

/// A 32pt square icon button with a hairline, for page actions.
struct IconButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        IconButtonBody(configuration: configuration)
    }
}

private struct IconButtonBody: View {
    let configuration: ButtonStyleConfiguration
    @Environment(\.isEnabled) private var isEnabled
    @State private var hovering = false

    var body: some View {
        configuration.label
            .labelStyle(.iconOnly)
            .font(.system(size: 14))
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(width: Theme.iconButton, height: Theme.iconButton)
            .background(hovering ? Theme.hover : Theme.card, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.border, lineWidth: 1))
            .opacity(isEnabled ? (configuration.isPressed ? 0.7 : 1) : 0.45)
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
            .onHover { hovering = $0 }
            .focusRing(cornerRadius: Theme.controlRadius)
    }
}

/// TokenTracker's period tabs: plain 12/500 labels, the current one on a
/// filled pill.
struct Tabs<Value: Hashable>: View {
    var options: [(value: Value, title: String)]
    @Binding var selection: Value
    @FocusState private var focused: Int?

    var body: some View {
        HStack(spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.element.value) { index, option in
                TabButton(title: option.title, isSelected: option.value == selection) { selection = option.value }
                    .focused($focused, equals: index)
            }
        }
        .padding(2)
        .background(Theme.segmentTrack, in: .rect(cornerRadius: Theme.controlRadius + 2))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius + 2).strokeBorder(Theme.border, lineWidth: 1))
        // Like a segmented control: the arrow keys move the selection.
        .onKeyPress(.leftArrow) { step(-1) }
        .onKeyPress(.rightArrow) { step(1) }
    }

    private func step(_ offset: Int) -> KeyPress.Result {
        guard let index = options.firstIndex(where: { $0.value == selection }) else { return .ignored }
        let next = index + offset
        guard options.indices.contains(next) else { return .handled }
        selection = options[next].value
        if focused != nil { focused = next }
        return .handled
    }
}

private struct TabButton: View {
    var title: String
    var isSelected: Bool
    var action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(Theme.control)
                .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textSecondary)
                .padding(.horizontal, 12)
                .frame(height: Theme.controlHeight)
                .background(isSelected ? Theme.segmentSelected : .clear, in: .rect(cornerRadius: Theme.controlRadius))
                .contentShape(.rect(cornerRadius: Theme.controlRadius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: Theme.controlRadius))
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

/// Plain, with the focus ring.
struct RingedPlainStyle: ButtonStyle {
    var cornerRadius: CGFloat
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.75 : 1)
            .focusRing(cornerRadius: cornerRadius)
    }
}

/// A small sentence-case tag.
struct Badge: View {
    var text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundStyle(Theme.textSecondary)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Theme.subtle, in: .rect(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Theme.border, lineWidth: 1))
    }
}

// MARK: - Focus

extension View {
    /// A 2pt neutral ring while the view itself has keyboard focus.
    ///
    /// Buttons get macOS's own focus ring (keyboard navigation on): a
    /// button style never sees its button's focus, so this draws nothing
    /// there. It is for focusable views that draw their own frame.
    func focusRing(cornerRadius: CGFloat) -> some View {
        modifier(FocusRing(cornerRadius: cornerRadius))
    }
}

private struct FocusRing: ViewModifier {
    var cornerRadius: CGFloat
    @Environment(\.isFocused) private var focused

    func body(content: Content) -> some View {
        content.overlay(
            RoundedRectangle(cornerRadius: cornerRadius + 3)
                .strokeBorder(Theme.focus, lineWidth: Theme.focusRing)
                .padding(-3)
                .opacity(focused ? 1 : 0)
                .allowsHitTesting(false))
    }
}
