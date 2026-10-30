import AppKit
import QuiverCore
import SwiftUI

/// The look: TokenTracker's component shapes re-tokenised onto the Devin
/// desktop app's colour model (`systems/devin-desktop/tokens.json`; values
/// copied from there, not from memory). Every neutral is one ink at a fixed
/// alpha — near-black (13,13,13) in light, white in dark: text at 90, 56 and
/// 40%, fills at 8/6/4% (light) and 8/5/3% (dark), edges at 10/8% (light) and
/// 8/4% (dark), borders declaring (6,6,6) in light. Three surfaces: page
/// flat, sidebar on the page colour, only cards and popups raised. One
/// emerald kept for money and "on" states; one blue for links, focus rings
/// and selected controls; status hues from Devin's `text.*`/`hue.*` set.
enum Theme {
    // MARK: Colour roles (tokens.json `light` / `dark`)

    /// `bg.page` / `bg.wash` / `bg.elevated`.
    static let wash = Color(light: "#fcfcfc", dark: "#141414")
    static let panel = Color(light: "#f8f8f8", dark: "#191919")
    static let card = Color(light: "#fefefe", dark: "#252525")
    static let field = Color(light: "#fefefe", dark: "#252525")
    /// `tint.tertiary` (4% / 3%): chips, badges, code blocks, `subtle`.
    static let subtle = Color(light: "#0d0d0d0a", dark: "#ffffff08")
    /// `tint.secondary` (6% / 5%): every hover.
    static let hover = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    /// `tint.primary` (8% / 8%): selected tabs and the active nav row.
    static let selected = Color(light: "#0d0d0d14", dark: "#ffffff14")
    static let navActive = Color(light: "#0d0d0d14", dark: "#ffffff14")
    static let segmentTrack = Color(light: "#0d0d0d0a", dark: "#ffffff08")
    /// `bg.elevated`: the selected segment lifts off the tinted track.
    static let segmentSelected = Color(light: "#fefefe", dark: "#252525")
    static let rowHover = Color(light: "#0d0d0d0f", dark: "#ffffff0d")

    /// `text.primary` (90%), `text.secondary` (56% / 52%), one muted tier.
    static let textPrimary = Color(light: "#0d0d0de6", dark: "#ffffffe6")
    static let textSecondary = Color(light: "#0d0d0d8f", dark: "#ffffff85")
    static let textNav = Color(light: "#0d0d0d8f", dark: "#ffffff85")
    static let textMuted = Color(light: "#0d0d0d8f", dark: "#ffffff85")
    /// `text.primary-strong`: solid ink.
    static let textStrong = Color(light: "#0d0d0d", dark: "#ffffff")
    static let textLabel = Color(light: "#0d0d0d8f", dark: "#ffffff85")
    static let textTab = Color(light: "#0d0d0d8f", dark: "#ffffff85")
    static let tableHeaderText = Color(light: "#0d0d0d8f", dark: "#ffffff85")

    /// `border.primary` (10% / 8%) and `border.secondary` (8% / 4%);
    /// `borderStrong` adds one measured step for chips and field hover.
    static let border = Color(light: "#0606061a", dark: "#ffffff14")
    static let borderStrong = Color(light: "#0606062e", dark: "#ffffff29")
    static let borderHover = Color(light: "#0606062e", dark: "#ffffff29")
    static let rowDivider = Color(light: "#06060614", dark: "#ffffff0a")

    /// Money and "on": cost figures, usage fill, heatmap, switches. Quiver's
    /// own emerald, kept — Devin's green is a status hue, this is identity.
    static let accent = Color(light: "#059669", dark: "#059669")
    static let accentFill = Color(light: "#10b981", dark: "#10b981")

    /// `bg.accent-primary` — interactive blue, only for links, focus rings
    /// and selected controls (the switch, the unread dot, the checkmark).
    static let interactive = Color(light: "#317cff", dark: "#4489ff")
    /// `text.link-strong` — the solid accent for inline links (the 85%
    /// `text.link` reads 3.09:1 on the light card).
    static let link = Color(light: "#317cff", dark: "#49b0ff")

    /// `bg.accent-neutral` / `text.primary-inverse`: the ink button.
    static let primaryBackground = Color(light: "#0d0d0d", dark: "#ffffff")
    static let primaryForeground = Color(light: "#fcfcfc", dark: "#141414")
    static let secondaryBackground = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    static let code = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    static let dialog = Color(light: "#fefefe", dark: "#252525")
    /// `bg.scrim`: 12% black in light, 32% in dark.
    static let scrim = Color(light: "#0000001f", dark: "#00000052")
    /// A sheet's drop shadow, the soft part of `shadow.dialog`: 0 20 60
    /// at 25% in light, 65% in dark. The 1pt ring is the card's hairline.
    static let dialogShadow = Color(light: "#00000040", dark: "#000000a6")
    /// A footer link under the pointer.
    static let footerLinkHover = Color(light: "#0d0d0de6", dark: "#ffffffe6")
    /// A period or table tab under the pointer, on a card.
    static let tabHover = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    /// A row or tab under the pointer that sits straight on the panel.
    static let panelHover = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    /// A whole card under the pointer (`status-row` hover).
    static let cardHover = Color(light: "#0d0d0d0f", dark: "#ffffff0d")

    static let usageTrack = Color(light: "#0d0d0d0f", dark: "#ffffff0d")
    /// `text.green`, `text.orange`/`text.yellow`, `bg.destructive`.
    static let success = Color(light: "#00a558", dark: "#00ec7e")
    /// Text on `success` as a fill: always white on the light hue,
    /// `text.always-black` (#0d0f0d) on the dark one.
    static let successForeground = Color(light: "#ffffff", dark: "#0d0f0d")
    static let warning = Color(light: "#f58e3a", dark: "#f58e3a")
    static let danger = Color(light: "#f53b3a", dark: "#f53b3a")
    /// Calendar's today mark and now line, red as Calendar.app draws them,
    /// and the text on that mark.
    static let calendarNow = danger
    static let calendarNowText = Color(light: "#ffffff", dark: "#ffffff")
    /// Danger and warning as text. `text.destructive` is Devin's only red
    /// and reads 3.74:1 on the light card, so light keeps Quiver's deeper
    /// red (4.83:1); dark uses `text.destructive`. `text.yellow` is Devin's
    /// light-safe warning text (orange as 12-14pt text reads about 2.4:1).
    static let dangerText = Color(light: "#dc2626", dark: "#f53b3a")
    static let warningText = Color(light: "#a16207", dark: "#facc15")

    /// A diff row's tint: added green, removed red, at the web app's
    /// `.diff-add`/`.diff-del` alpha.
    static let diffAdded = Color(light: "#00a5581f", dark: "#00ec7e1f")
    static let diffRemoved = Color(light: "#dc26261f", dark: "#f53b3a1f")

    /// Syntax token colours, GitHub's light/dark pairs — the web app's
    /// `--tk-*` variables carry the same values.
    static let syntaxKeyword = Color(light: "#cf222e", dark: "#ff7b72")
    static let syntaxString = Color(light: "#0a3069", dark: "#a5d6ff")
    static let syntaxNumber = Color(light: "#0550ae", dark: "#79c0ff")
    static let syntaxBuiltin = Color(light: "#8250df", dark: "#d2a8ff")
    static let syntaxVariable = Color(light: "#953800", dark: "#ffa657")
    static let syntaxComment = Color(light: "#6e7781", dark: "#8b949e")
    static let syntaxMeta = Color(light: "#116329", dark: "#7ee787")

    /// The colour a `SyntaxToken.Kind` paints with.
    static func syntax(_ kind: SyntaxToken.Kind) -> Color {
        switch kind {
        case .keyword: return syntaxKeyword
        case .string: return syntaxString
        case .number: return syntaxNumber
        case .builtin: return syntaxBuiltin
        case .variable: return syntaxVariable
        case .comment: return syntaxComment
        case .meta: return syntaxMeta
        case .added: return success
        case .removed: return dangerText
        case .plain: return textPrimary
        }
    }

    /// Heatmap steps, empty to busiest.
    static let heatmap: [Color] = [
        Color(light: "#ebedf0", dark: "#121212"),
        Color(light: "#a7f3d0", dark: "#065f46"),
        Color(light: "#6ee7b7", dark: "#059669"),
        Color(light: "#34d399", dark: "#10b981"),
        Color(light: "#10b981", dark: "#34d399"),
    ]

    // MARK: Type roles (tokens.json `core.typography`)

    @MainActor static var pageTitle: Font { font(size: 28, weight: .semibold) }
    @MainActor static var pageSubtitle: Font { font(size: 14) }
    @MainActor static var sectionTitle: Font { font(size: 20, weight: .semibold) }
    @MainActor static var figure: Font { font(size: 20, weight: .bold) }
    @MainActor static var cardTitle: Font { font(size: 14, weight: .medium) }
    @MainActor static var rowTitle: Font { font(size: 14, weight: .medium) }
    @MainActor static var bodySmall: Font { font(size: 14) }
    @MainActor static var nav: Font { font(size: 13) }
    @MainActor static var control: Font { font(size: 12, weight: .medium) }
    @MainActor static var caption: Font { font(size: 12) }
    @MainActor static var tableHeader: Font { font(size: 12, weight: .medium) }

    /// Geist (Geist Mono for `.monospaced`) at `size` points as designed,
    /// scaled by View › Zoom (`TextZoom`). Every font in the app comes
    /// through here or the roles above, so ⌘+ and ⌘− reach every page.
    /// Reading the zoom inside a view's body is what redraws that view when
    /// it changes. Without the bundled fonts it is the system font.
    @MainActor static func font(size: CGFloat, weight: Font.Weight? = nil, design: Font.Design? = nil) -> Font {
        guard let family = AppFonts.family(monospaced: design == .monospaced) else {
            return .system(size: scaled(size), weight: weight, design: design)
        }
        return .custom(family, fixedSize: scaled(size)).weight(weight ?? .regular)
    }

    /// The AppKit font that matches `font(size:weight:)` at the current
    /// zoom, for text AppKit draws or measures beside it. `monospacedDigits`
    /// matches a SwiftUI `.monospacedDigit()`.
    @MainActor static func nsFont(size: CGFloat, weight: NSFont.Weight = .regular,
                                  monospaced: Bool = false, monospacedDigits: Bool = false) -> NSFont {
        AppFonts.nsFont(size: scaled(size), weight: weight, monospaced: monospaced, monospacedDigits: monospacedDigits)
    }

    /// `size` points at the current zoom, for AppKit fonts and measurements
    /// that have to match a `font(size:)`.
    @MainActor static func scaled(_ size: CGFloat) -> CGFloat {
        TextZoom.pointSize(size, at: TextZoomModel.shared.zoom)
    }

    // MARK: Shape and size (tokens.json `core.radius`, `size`, `space`)

    static let cardRadius: CGFloat = 10
    static let controlRadius: CGFloat = 6
    static let cardPadding: CGFloat = 20
    static let gutter: CGFloat = 16
    /// The icon rail: wide enough to clear the traffic lights (~x 68).
    static let sidebarCollapsedWidth: CGFloat = 72
    /// The sidebar header's least height (it grows to the window's real
    /// title bar) and the first x clear of the traffic lights in it.
    static let titleBarHeight: CGFloat = 30
    static let titleBarLeading: CGFloat = 80
    /// Leading keeps the icons put; trailing hugs the panel.
    static let sidebarLeadingPadding: CGFloat = 8
    static let sidebarTrailingPadding: CGFloat = 4
    /// The collapsed nav pill that hugs an icon: 8 + 18 + 8.
    static let navIconPillWidth: CGFloat = 34
    /// The strip that arms the hover expansion: just the icon column
    /// (leading padding + pill + trailing), so a pointer heading for
    /// the page's left edge never floats the sidebar over its target.
    static let sidebarHoverWidth: CGFloat =
        sidebarLeadingPadding + navIconPillWidth + sidebarTrailingPadding
    /// The invisible drag strip centred on the sidebar's trailing edge.
    static let sidebarResizeHandleWidth: CGFloat = 8
    /// Sidebar collapse, expand and hover-float all use this.
    static let sidebarAnimation: Animation = .smooth(duration: 0.25)
    static let navRowHeight: CGFloat = 32
    static let controlHeight: CGFloat = 28
    /// `control.md`: secondary buttons.
    static let controlMedium: CGFloat = 32
    /// `control.field`: search fields, selects, text fields.
    static let fieldHeight: CGFloat = 36
    static let fieldRadius: CGFloat = 6
    static let selectRadius: CGFloat = 5
    static let iconButton: CGFloat = 36
    static let iconButtonRadius: CGFloat = 8
    static let usageTrackHeight: CGFloat = 6
    static let heatmapCell: CGFloat = 12
    /// Inset panels and code blocks inside a card, a step under `cardRadius`.
    static let blockRadius: CGFloat = 7
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
    /// A sheet card's corners
    /// and the room it keeps from the window's edges.
    static let dialogRadius: CGFloat = 12
    static let sheetMargin: CGFloat = 24
    /// A sheet fades in over `motion.duration.fade` and settles from 98%
    /// over `motion.duration.dialog`, both on `motion.easing.emphasized`.
    static let sheetFade: Animation = .timingCurve(0.16, 1, 0.3, 1, duration: 0.22)
    static let sheetSettle: Animation = .timingCurve(0.16, 1, 0.3, 1, duration: 0.26)
    /// The quick fade behind every hover highlight and expand toggle.
    static let fadeDuration: TimeInterval = 0.15

    /// `core.focusRing`: a 1pt accent ring (Devin draws it inside
    /// `border.accent-primary`, offset 2px).
    static let focusRing: CGFloat = 1
    static let focus = interactive

    // MARK: Data colours

    /// The harness's colour from `HarnessColor`, the one palette every
    /// page reads.
    static func harness(_ id: String) -> Color {
        Color(rgb: HarnessColor.of(id))
    }

    /// Order the harnesses always appear in, so a colour never moves.
    static let harnessOrder = ["claude", "codex", "opencode", "devin"]

    /// Bar colour for a used percentage: calm, then amber at 70, red at 90.
    static func usage(_ percent: Int) -> Color {
        percent >= 90 ? danger : percent >= 70 ? warning : accentFill
    }
}

extension Color {
    /// A QuiverCore colour (harness or model brand) as a SwiftUI colour.
    init(rgb: HarnessRGB) { self.init(red: rgb.red, green: rgb.green, blue: rgb.blue) }

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

/// A card: one hairline on `bg.card`, 10pt radius, 20pt padding, and an
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
                                .font(monoSubtitle ? Theme.font(size: 12, design: .monospaced) : Theme.caption)
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
/// actions top right, content up to 1280pt wide and centred.
///
/// `scrolls: false` is for a page whose content scrolls itself (an
/// editor): the content gets the panel's remaining height to fill, so
/// there is never a scroll inside a scroll.
struct Page<Content: View, Actions: View>: View {
    var title: String
    var subtitle: String
    /// A link that ends the subtitle, such as "Data from [Artificial
    /// Analysis]" on the Models page. Rendered inline after the text.
    var subtitleLink: (label: String, url: URL)? = nil
    /// A chevron before the title, for a page reached from a list (the
    /// chat, from Sessions). Carries the ⌘[ shortcut.
    var back: (help: String, action: () -> Void)? = nil
    var maxWidth: CGFloat = 1280
    var scrolls = true
    @ViewBuilder var actions: Actions
    @ViewBuilder var content: Content
    /// False for the page's first frame. Laying out a long page's rows
    /// can take a few hundred ms on the main thread, and until it ends
    /// the old page stays up, so a click or ⌘-number felt slow. The
    /// header goes up at once; the content joins on the next run-loop
    /// turn, after that frame is committed.
    @State private var showsContent = false

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
            PageHeader(title: title, subtitle: subtitle, link: subtitleLink, back: back) { actions }
            if showsContent {
                content
                    .textSelection(.enabled)
            }
        }
        .onAppear {
            // The next run-loop turn: the header's frame commits first.
            DispatchQueue.main.async { showsContent = true }
        }
        .frame(maxHeight: scrolls ? nil : .infinity, alignment: .top)
        .padding(.horizontal, 40)
        .padding(.vertical, 36)
        .frame(maxWidth: maxWidth, alignment: .leading)
        .frame(maxWidth: .infinity)
    }
}

extension Page where Actions == EmptyView {
    init(title: String, subtitle: String,
         subtitleLink: (label: String, url: URL)? = nil,
         back: (help: String, action: () -> Void)? = nil,
         maxWidth: CGFloat = 1280, scrolls: Bool = true,
         @ViewBuilder content: () -> Content) {
        self.init(title: title, subtitle: subtitle, subtitleLink: subtitleLink, back: back,
                  maxWidth: maxWidth, scrolls: scrolls,
                  actions: { EmptyView() }, content: content)
    }
}

struct PageHeader<Actions: View>: View {
    var title: String
    var subtitle: String
    var link: (label: String, url: URL)? = nil
    /// A chevron before the title, for a page reached from a list (the
    /// chat, from Sessions). Carries the ⌘[ shortcut.
    var back: (help: String, action: () -> Void)? = nil
    @ViewBuilder var actions: Actions

    var body: some View {
        HStack(alignment: .top) {
            HStack(spacing: 12) {
                if let back {
                    Button(action: back.action) {
                        Image(systemName: "chevron.left")
                            .font(Theme.font(size: 15, weight: .medium))
                            .foregroundStyle(Theme.textSecondary)
                            .frame(width: 24, height: 28)
                            .contentShape(.rect)
                    }
                    .plainButton(cornerRadius: Theme.smallRadius)
                    .keyboardShortcut("[", modifiers: .command)
                    .help("\(back.help) (⌘[)")
                    .accessibilityLabel(back.help)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text(title).font(Theme.pageTitle).tracking(-0.6).foregroundStyle(Theme.textPrimary)
                    if !subtitle.isEmpty || link != nil {
                        // Inline link: the subtitle ends mid-sentence and the
                        // link's label finishes it.
                        HStack(spacing: 4) {
                            Text(subtitle).font(Theme.pageSubtitle).foregroundStyle(Theme.textSecondary)
                            if let link {
                                Link(link.label, destination: link.url)
                                    .font(Theme.pageSubtitle).foregroundStyle(Theme.link)
                            }
                        }
                    }
                }
                .textSelection(.enabled)
            }
            Spacer(minLength: 16)
            HStack(spacing: 8) { actions }
        }
    }
}

