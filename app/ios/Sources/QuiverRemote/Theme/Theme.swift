import SwiftUI
import UIKit

/// Quiver's look, ported from the Mac app's `app/mac/Sources/QuiverApp/Theme/Theme.swift`
/// (which takes its values from the TokenTracker package's tokens.json).
/// Same hex values and roles; type is one point larger than the Mac's
/// so 12pt captions stay readable at arm's length.
enum Theme {
    // MARK: Surfaces
    static let wash = Color(light: "#f5f5f5", dark: "#0a0a0a")
    static let card = Color(light: "#ffffff", dark: "#171717")
    static let field = Color(light: "#ffffff", dark: "#171717")
    static let subtle = Color(light: "#fafafa", dark: "#262626")
    static let selected = Color(light: "#f5f5f5", dark: "#262626")
    static let pressed = Color(light: "#f5f5f5", dark: "#26262680")
    static let segmentTrack = Color(light: "#fafafa", dark: "#171717")
    static let segmentSelected = Color(light: "#ffffff", dark: "#262626")
    static let code = Color(light: "#f5f5f5", dark: "#0f0f0f")

    // MARK: Text
    static let textPrimary = Color(light: "#0a0a0a", dark: "#fafafa")
    static let textSecondary = Color(light: "#737373", dark: "#a3a3a3")
    static let textMuted = Color(light: "#737373", dark: "#737373")
    static let textLabel = Color(light: "#171717", dark: "#e5e5e5")

    // MARK: Borders
    static let border = Color(light: "#e5e5e5", dark: "#262626")
    static let borderStrong = Color(light: "#d4d4d4", dark: "#404040")

    // MARK: Accent and status (green only for "on" and "active")
    static let accent = Color(light: "#059669", dark: "#059669")
    static let accentFill = Color(light: "#10b981", dark: "#10b981")
    /// The activity heatmap's five levels, none to busiest: the Mac's.
    static let heatmap: [Color] = [
        Color(light: "#ebedf0", dark: "#121212"),
        Color(light: "#a7f3d0", dark: "#065f46"),
        Color(light: "#6ee7b7", dark: "#059669"),
        Color(light: "#34d399", dark: "#10b981"),
        Color(light: "#10b981", dark: "#34d399"),
    ]
    static let success = Color(light: "#10b981", dark: "#34d399")
    static let warning = Color(light: "#f59e0b", dark: "#fbbf24")
    static let warningText = Color(light: "#b45309", dark: "#fbbf24")
    static let danger = Color(light: "#ef4444", dark: "#f87171")
    /// Solid fill behind white, such as the stop button; `danger` is for red text and marks.
    static let dangerFill = Color(light: "#ef4444", dark: "#dc2626")
    static let dangerText = Color(light: "#dc2626", dark: "#f87171")
    /// The quiet track under a limit bar, as the Mac app's `usageTrack`.
    static let usageTrack = Color(light: "#f5f5f5", dark: "#40404080")
    static let diffAdded = Color(light: "#10b98126", dark: "#10b98126")
    static let diffRemoved = Color(light: "#ef444426", dark: "#ef444426")

    // MARK: Row swipe actions (solid fills behind a white glyph and label)
    static let swipeAttach = accentFill
    static let swipeDetach = Color(light: "#64748b", dark: "#475569")
    static let swipeOpen = Color(light: "#0891b2", dark: "#0e7490")
    static let swipePin = Color(light: "#d97706", dark: "#b45309")
    static let swipeRename = Color(light: "#2563eb", dark: "#1d4ed8")
    static let swipeFork = Color(light: "#4f46e5", dark: "#4338ca")
    static let swipeClear = Color(light: "#64748b", dark: "#475569")

    static func swipe(_ action: SessionAction) -> Color {
        switch action {
        case .attach, .resolve, .newSession: swipeAttach
        case .detach: swipeDetach
        case .open: swipeOpen
        case .pin, .unpin: swipePin
        case .rename: swipeRename
        case .fork: swipeFork
        case .clear: swipeClear
        }
    }

    // MARK: Buttons (primary is ink, never green)
    static let primaryBackground = Color(light: "#171717", dark: "#fafafa")
    static let primaryForeground = Color(light: "#ffffff", dark: "#171717")

    // MARK: Type
    static let pageTitle = Font.system(size: 30, weight: .semibold)
    static let sectionTitle = Font.system(size: 20, weight: .semibold)
    static let cardTitle = Font.system(size: 15, weight: .medium)
    static let rowTitle = Font.system(size: 15, weight: .medium)
    static let body = Font.system(size: 15)
    static let control = Font.system(size: 13, weight: .medium)
    static let caption = Font.system(size: 13)
    static let groupLabel = Font.system(size: 13, weight: .medium)
    static let mono = Font.system(size: 12, design: .monospaced)
    static let monoBody = Font.system(size: 13, design: .monospaced)
    static let code6 = Font.system(size: 34, weight: .semibold, design: .monospaced)

    // MARK: Shape and spacing
    static let cardRadius: CGFloat = 16
    static let blockRadius: CGFloat = 12
    static let controlRadius: CGFloat = 8
    static let gutter: CGFloat = 16
    static let rowVertical: CGFloat = 12
    static let rowHorizontal: CGFloat = 14
    static let statusDot: CGFloat = 8
    static let controlHeight: CGFloat = 32
    static let buttonHeight: CGFloat = 44
    static let fieldHeight: CGFloat = 44
    static let usageTrackHeight: CGFloat = 6

    /// Each harness's colour, the same hand-picked palette as the Mac app
    /// (`QuiverCore/Swe/HarnessColor.swift`); others hash into `palette`.
    static func harness(_ id: String?) -> Color {
        let fixed: [String: String] = [
            "claude": "#d97757", "codex": "#3b7ff0", "opencode": "#139f8b", "devin": "#c4860f",
            "cursor": "#c08ce0", "grok-bot": "#8a73e8", "pi": "#5fb3d9", "droid": "#e06c8f",
            "freebuff": "#8ac74f", "cline": "#9aa0aa", "gemini": "#e3b51e",
        ]
        let palette = ["#5aa2ff", "#f0a04b", "#4ec9b0", "#e06c8f", "#9b7ff0",
                       "#8ac74f", "#5ac8e0", "#f0755f", "#d9b44a", "#c08ce0"]
        let key = (id ?? "").lowercased()
        if let hex = fixed[key] { return Color(light: hex, dark: hex) }
        let hash = key.unicodeScalars.reduce(5381) { ($0 &* 33) &+ Int($1.value) }
        let hex = palette[abs(hash) % palette.count]
        return Color(light: hex, dark: hex)
    }

    static func status(_ s: SessionStatus) -> Color {
        switch s {
        case .active: accentFill
        case .followup, .approval: warning
        case .error, .interrupted: danger
        default: borderStrong
        }
    }
}

extension Color {
    /// A colour that follows the system appearance. Hex is `#rrggbb` or
    /// `#rrggbbaa`.
    init(light: String, dark: String) {
        self.init(uiColor: UIColor { $0.userInterfaceStyle == .dark ? UIColor(hex: dark) : UIColor(hex: light) })
    }
}

extension UIColor {
    convenience init(hex: String) {
        let text = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        let value = UInt64(text, radix: 16) ?? 0
        let r, g, b, a: Double
        if text.count == 8 {
            r = Double((value >> 24) & 0xff) / 255
            g = Double((value >> 16) & 0xff) / 255
            b = Double((value >> 8) & 0xff) / 255
            a = Double(value & 0xff) / 255
        } else {
            r = Double((value >> 16) & 0xff) / 255
            g = Double((value >> 8) & 0xff) / 255
            b = Double(value & 0xff) / 255
            a = 1
        }
        self.init(red: r, green: g, blue: b, alpha: a)
    }
}
