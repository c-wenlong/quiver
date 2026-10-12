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
    static let textStrong = Color(light: "#0a0a0a", dark: "#ffffff")
    static let textLabel = Color(light: "#171717", dark: "#e5e5e5")
    /// Unselected tab and table-header text: #737373 / #d4d4d4 as measured.
    static let textTab = Color(light: "#737373", dark: "#d4d4d4")
    static let tableHeaderText = Color(light: "#525252", dark: "#d4d4d4")

    static let border = Color(light: "#e5e5e5", dark: "#262626")
    static let borderStrong = Color(light: "#d4d4d4", dark: "#404040")
    static let borderHover = Color(light: "#d4d4d4", dark: "#404040")
    /// Table rows: #f5f5f5 in light, the default hairline in dark.
    static let rowDivider = Color(light: "#f5f5f5", dark: "#262626")

    /// Money and "on": cost figures, usage fill, heatmap, switches.
    static let accent = Color(light: "#059669", dark: "#059669")
    static let accentFill = Color(light: "#10b981", dark: "#10b981")

    static let primaryBackground = Color(light: "#171717", dark: "#fafafa")
    static let primaryForeground = Color(light: "#ffffff", dark: "#171717")
    static let secondaryBackground = Color(light: "#fafafa", dark: "#171717")
    static let code = Color(light: "#f5f5f5", dark: "#17171799")
    static let dialog = Color(light: "#ffffff", dark: "#0a0a0a")
    /// A footer link under the pointer.
    static let footerLinkHover = Color(light: "#404040", dark: "#d4d4d4")
    /// A period or table tab under the pointer, on a card.
    static let tabHover = Color(light: "#fafafa", dark: "#26262680")
    /// A row or tab under the pointer that sits straight on the panel.
    /// `rowHover` and `tabHover` were measured on white cards and vanish
    /// on the #fafafa panel, so light takes the next step, `bg.selected`.
    static let panelHover = Color(light: "#f5f5f5", dark: "#26262680")
    /// A whole card under the pointer (`status-row` hover): #fafafa in
    /// light, #262626 at 60% in dark.
    static let cardHover = Color(light: "#fafafa", dark: "#26262699")

    static let usageTrack = Color(light: "#f5f5f5", dark: "#40404080")
    static let success = Color(light: "#10b981", dark: "#34d399")
    static let warning = Color(light: "#f59e0b", dark: "#fbbf24")
    static let danger = Color(light: "#ef4444", dark: "#f87171")
    /// Danger and warning for text and glyphs. The roles above are for
    /// dots, bars and fills; as 12 to 14pt text they fall under 4.5:1 on
    /// the light surfaces (red about 3.8, amber about 2.1).
    static let dangerText = Color(light: "#dc2626", dark: "#f87171")
    static let warningText = Color(light: "#b45309", dark: "#fbbf24")

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
    /// `control.md`: secondary buttons.
    static let controlMedium: CGFloat = 32
    /// `control.field`: search fields, selects, text fields.
    static let fieldHeight: CGFloat = 36
    static let fieldRadius: CGFloat = 8
    static let selectRadius: CGFloat = 6
    static let iconButton: CGFloat = 36
    static let iconButtonRadius: CGFloat = 12
    static let usageTrackHeight: CGFloat = 6
    static let heatmapCell: CGFloat = 12
    /// Inset panels and code blocks inside a card, a step under `cardRadius`.
    static let blockRadius: CGFloat = 12
    /// Chips and the hover shape on small text buttons.
    static let smallRadius: CGFloat = 4
    /// The filled or stroked status dot.
    static let statusDot: CGFloat = 7
    /// A hairline row divider.
    static let hairline: CGFloat = 1
    /// Vertical space between the sections of a page.
    static let sectionGap: CGFloat = 32
    /// An editor sheet's padding and width.
    static let sheetPadding: CGFloat = 24
    static let sheetWidth: CGFloat = 520
    /// A small form sheet's padding and width.
    static let formSheetPadding: CGFloat = 20
    static let formSheetWidth: CGFloat = 420
    /// The quick fade behind every hover highlight and expand toggle.
    static let fadeDuration: TimeInterval = 0.15

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
    /// A path under the title reads better in mono.
    var monoSubtitle = false
    @ViewBuilder var accessory: Accessory
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if title != nil || subtitle != nil {
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        if let title { Text(title).font(Theme.cardTitle).foregroundStyle(Theme.textPrimary) }
                        if let subtitle {
                            Text(subtitle)
                                .font(monoSubtitle ? .system(size: 12, design: .monospaced) : Theme.caption)
                                .foregroundStyle(Theme.textSecondary)
                        }
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
    init(title: String? = nil, subtitle: String? = nil, monoSubtitle: Bool = false,
         @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, monoSubtitle: monoSubtitle,
                  accessory: { EmptyView() }, content: content)
    }
}

/// Page scaffold inside the content panel: 36/40 title, 16/24 subtitle,
/// actions top right, content up to 1152pt wide and centred.
///
/// `scrolls: false` is for a page whose content scrolls itself (an
/// editor): the content gets the panel's remaining height to fill, so
/// there is never a scroll inside a scroll.
struct Page<Content: View, Actions: View>: View {
    var title: String
    var subtitle: String
    var maxWidth: CGFloat = 1152
    var scrolls = true
    @ViewBuilder var actions: Actions
    @ViewBuilder var content: Content

    var body: some View {
        if scrolls {
            ScrollView { stack }
                .scrollContentBackground(.hidden)
        } else {
            stack
        }
    }

    private var stack: some View {
        VStack(alignment: .leading, spacing: 24) {
            PageHeader(title: title, subtitle: subtitle) { actions }
            content
                .textSelection(.enabled)
        }
        .frame(maxHeight: scrolls ? nil : .infinity, alignment: .top)
        .padding(.horizontal, 40)
        .padding(.vertical, 36)
        .frame(maxWidth: maxWidth, alignment: .leading)
        .frame(maxWidth: .infinity)
    }
}

extension Page where Actions == EmptyView {
    init(title: String, subtitle: String, maxWidth: CGFloat = 1152, scrolls: Bool = true,
         @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, maxWidth: maxWidth, scrolls: scrolls,
                  actions: { EmptyView() }, content: content)
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
            .textSelection(.enabled)
            Spacer(minLength: 16)
            HStack(spacing: 8) { actions }
        }
    }
}

