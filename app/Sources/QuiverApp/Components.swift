import AppKit
import QuiverCore
import SwiftUI

// The shared controls, sized and coloured from the TokenTracker package's
// components.md (measured values) and tokens.json. Where TokenTracker and
// Quiver's own rules differ, Quiver's win: sentence case, green only for
// money and "on", and a visible neutral focus ring on every control.

// MARK: - Focus

extension View {
    /// The neutral 2pt focus ring, driven by this view's own focus.
    ///
    /// A button style never learns whether its button has focus, so the
    /// ring lives here, on the control itself: it binds a `@FocusState`
    /// and replaces macOS's blue ring.
    func focusRing(cornerRadius: CGFloat) -> some View {
        modifier(OwnFocusRing(cornerRadius: cornerRadius))
    }

    /// The ring for a control whose focus is tracked elsewhere (a tab in a
    /// group, a sidebar row), so it doesn't take a second focus binding.
    /// `afterPointer` keeps it on when the focus came from a click, as a
    /// text field's does.
    func focusRing(_ isFocused: Bool, cornerRadius: CGFloat, afterPointer: Bool = false) -> some View {
        modifier(RingOverlay(isFocused: isFocused, cornerRadius: cornerRadius, afterPointer: afterPointer))
    }
}

/// Whether the last input was a key press, like CSS `:focus-visible`. The
/// window hands its first control focus at launch and a sheet does the same
/// when it opens; neither came from the keyboard, so neither gets a ring
/// until a key is pressed. A click puts the rings away again. A shortcut
/// (⌘S, a menu key) says nothing about how focus moves, so it changes
/// nothing; the rule is `FocusModality.fromKeyboard(after:)` in QuiverCore.
@MainActor @Observable
final class FocusVisibility {
    static let shared = FocusVisibility()
    private(set) var fromKeyboard = false
    @ObservationIgnored private var monitor: Any?

    /// Starts watching input; call once at launch, before any key press,
    /// so the first Tab already counts as keyboard.
    static func start() { _ = shared }

    private init() {
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]) { event in
            let flags = event.modifierFlags
            let input: FocusModality.Input = event.type == .keyDown
                ? .key(command: flags.contains(.command), control: flags.contains(.control), option: flags.contains(.option))
                : .pointer
            guard let keyboard = FocusModality.fromKeyboard(after: input) else { return event }
            MainActor.assumeIsolated {
                if FocusVisibility.shared.fromKeyboard != keyboard { FocusVisibility.shared.fromKeyboard = keyboard }
            }
            return event
        }
    }
}

private struct OwnFocusRing: ViewModifier {
    var cornerRadius: CGFloat
    @FocusState private var focused: Bool

    func body(content: Content) -> some View {
        content
            .focused($focused)
            .modifier(RingOverlay(isFocused: focused, cornerRadius: cornerRadius))
    }
}

private struct RingOverlay: ViewModifier {
    var isFocused: Bool
    var cornerRadius: CGFloat
    var afterPointer = false

    func body(content: Content) -> some View {
        content
            .focusEffectDisabled()
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius + 3)
                    .strokeBorder(Theme.focus, lineWidth: Theme.focusRing)
                    .padding(-3)
                    .opacity(isFocused && (afterPointer || FocusVisibility.shared.fromKeyboard) ? 1 : 0)
                    .allowsHitTesting(false))
    }
}

// MARK: - Buttons

extension View {
    /// Black in light, near-white in dark, never green: 28pt, radius 8,
    /// 12/500, grows to 1.03 under the pointer.
    func primaryButton() -> some View {
        buttonStyle(PrimaryButtonStyle()).focusRing(cornerRadius: Theme.controlRadius)
    }

    /// 32pt on the field fill with a strong hairline. A button made with
    /// `role: .destructive` gets its label in `dangerText`.
    func secondaryButton() -> some View {
        buttonStyle(SecondaryButtonStyle()).focusRing(cornerRadius: Theme.controlRadius)
    }

    /// 36pt square, radius 12, hairline, 16pt glyph in `text.nav`.
    func iconButton() -> some View {
        buttonStyle(IconButtonStyle()).focusRing(cornerRadius: Theme.iconButtonRadius)
    }

    /// No chrome; dims while disabled.
    func plainButton(cornerRadius: CGFloat = 6) -> some View {
        buttonStyle(RingedPlainStyle(cornerRadius: cornerRadius)).focusRing(cornerRadius: cornerRadius)
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        PrimaryButtonBody(configuration: configuration)
    }
}

private struct PrimaryButtonBody: View {
    let configuration: ButtonStyleConfiguration
    @Environment(\.isEnabled) private var isEnabled
    @State private var hovering = false

    var body: some View {
        configuration.label
            .font(Theme.control)
            .labelStyle(.titleAndIcon)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight)
            .foregroundStyle(Theme.primaryForeground)
            .background(Theme.primaryBackground, in: .rect(cornerRadius: Theme.controlRadius))
            .scaleEffect(hovering && isEnabled && !configuration.isPressed ? 1.03 : 1)
            .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.45)
            .animation(.easeOut(duration: 0.15), value: hovering)
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
            .onHover { hovering = $0 }
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        SecondaryButtonBody(configuration: configuration)
    }
}

private struct SecondaryButtonBody: View {
    let configuration: ButtonStyleConfiguration
    @Environment(\.isEnabled) private var isEnabled
    @State private var hovering = false

    var body: some View {
        configuration.label
            .font(Theme.control)
            .labelStyle(.titleAndIcon)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlMedium)
            .foregroundStyle(foreground)
            .background(hovering ? Theme.hover : Theme.secondaryBackground, in: .rect(cornerRadius: Theme.controlRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius).strokeBorder(Theme.borderStrong, lineWidth: 1))
            .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.45)
            .animation(.easeOut(duration: 0.2), value: hovering)
            .contentShape(.rect(cornerRadius: Theme.controlRadius))
            .onHover { hovering = $0 }
    }

    /// The style sets the label's colour, so a `.foregroundStyle` on the
    /// button can't reach it; a destructive role is how a caller asks for red.
    private var foreground: Color {
        if configuration.role == .destructive { return Theme.dangerText }
        return hovering ? Theme.textStrong : Theme.textPrimary
    }
}

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
            .font(.system(size: 16))
            .foregroundStyle(hovering && isEnabled ? Theme.textStrong : Theme.textNav)
            .frame(width: Theme.iconButton, height: Theme.iconButton)
            .background(hovering && isEnabled ? Theme.selected : .clear,
                        in: .rect(cornerRadius: Theme.iconButtonRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.iconButtonRadius).strokeBorder(Theme.border, lineWidth: 1))
            .opacity(isEnabled ? (configuration.isPressed ? 0.7 : 1) : 0.45)
            .animation(.easeOut(duration: 0.15), value: hovering)
            .contentShape(.rect(cornerRadius: Theme.iconButtonRadius))
            .onHover { hovering = $0 }
    }
}

/// No chrome; dims while disabled, so an unavailable action never looks
/// ready. Use through `plainButton()` to get the focus ring.
struct RingedPlainStyle: ButtonStyle {
    var cornerRadius: CGFloat
    func makeBody(configuration: Configuration) -> some View {
        PlainBody(configuration: configuration)
    }
}

private struct PlainBody: View {
    let configuration: ButtonStyleConfiguration
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .opacity(isEnabled ? (configuration.isPressed ? 0.75 : 1) : 0.4)
    }
}

// MARK: - Tabs

/// One control, four looks, each for its own job:
/// - `.period`: a time range (Overview, the menu bar). No track; the
///   current one on `bg.selected`.
/// - `.table`: switches what a table shows. Like period, radius 4.
/// - `.segmented`: picks a setting. Track at radius 12, 34pt, the current
///   one raised with `shadow.sm`.
/// - `.underline`: switches a page between views (Skills, AGENTS.md).
///   14/500 labels over a hairline, the current one underlined.
/// A tab that stands for a harness can carry its id (`icons`, or the
/// three-part options initialiser) to draw its logo before the title.
struct Tabs<Value: Hashable>: View {
    enum Style { case period, table, segmented, underline }

    var options: [(value: Value, title: String)]
    @Binding var selection: Value
    var style: Style = .segmented
    /// Sits straight on the panel rather than on a card, so a tab under
    /// the pointer takes `panelHover`; `tabHover` vanishes on the panel.
    var onPanel = false
    /// The harness id whose logo goes before a tab's title, by tab value.
    var icons: [Value: String] = [:]
    @FocusState private var focused: Int?

    var body: some View {
        Group {
            switch style {
            case .underline:
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 20) { buttons }
                    Rectangle().fill(Theme.border).frame(height: 1)
                }
            case .segmented:
                HStack(spacing: 2) { buttons }
                    .padding(2)
                    .background(Theme.segmentTrack, in: .rect(cornerRadius: 12))
                    .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.border, lineWidth: 1))
                    .fixedSize()
            case .period, .table:
                HStack(spacing: 2) { buttons }.fixedSize()
            }
        }
        // The arrow keys move the selection, like a segmented control.
        .onKeyPress(.leftArrow) { step(-1) }
        .onKeyPress(.rightArrow) { step(1) }
    }

    private var buttons: some View {
        ForEach(Array(options.enumerated()), id: \.element.value) { index, option in
            TabButton(title: option.title, icon: icons[option.value], isSelected: option.value == selection,
                      isFocused: focused == index, style: style, onPanel: onPanel) { selection = option.value }
                .focused($focused, equals: index)
        }
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

extension Tabs {
    /// Tabs as (value, title, harness id or nil).
    init(options: [(value: Value, title: String, icon: String?)], selection: Binding<Value>,
         style: Style = .segmented, onPanel: Bool = false) {
        var icons: [Value: String] = [:]
        for option in options { if let icon = option.icon { icons[option.value] = icon } }
        self.init(options: options.map { ($0.value, $0.title) }, selection: selection, style: style,
                  onPanel: onPanel, icons: icons)
    }
}

private struct TabButton<Value: Hashable>: View {
    var title: String
    var icon: String?
    var isSelected: Bool
    var isFocused: Bool
    var style: Tabs<Value>.Style
    var onPanel: Bool
    var action: () -> Void
    @State private var hovering = false

    private var radius: CGFloat { style == .table ? 4 : Theme.controlRadius }

    var body: some View {
        Button(action: action) {
            label.contentShape(.rect(cornerRadius: radius))
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: radius))
        .focusRing(isFocused, cornerRadius: radius)
        .onHover { hovering = $0 }
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    /// The logo before the title, only when there is one to draw.
    @ViewBuilder private var titleLabel: some View {
        if let icon, HarnessIcon.exists(for: icon) {
            HStack(spacing: 6) {
                HarnessIcon(harness: icon, size: 14)
                Text(title)
            }
        } else {
            Text(title)
        }
    }

    @ViewBuilder private var label: some View {
        if style == .underline {
            titleLabel
                .font(Theme.rowTitle)
                .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textSecondary)
                .padding(.vertical, 8)
                .overlay(alignment: .bottom) {
                    Rectangle().fill(isSelected ? Theme.textPrimary : .clear).frame(height: 2)
                }
        } else {
            titleLabel
                .font(Theme.control)
                .foregroundStyle(isSelected || hovering ? Theme.textPrimary : Theme.textTab)
                .padding(.horizontal, 12)
                .frame(height: Theme.controlHeight)
                .background(fill, in: .rect(cornerRadius: radius))
                .shadow(color: style == .segmented && isSelected ? .black.opacity(0.08) : .clear, radius: 1, y: 1)
                .animation(.easeOut(duration: 0.15), value: hovering)
        }
    }

    private var fill: Color {
        if isSelected { return style == .segmented ? Theme.segmentSelected : Theme.selected }
        return hovering ? (onPanel ? Theme.panelHover : Theme.tabHover) : .clear
    }
}

// MARK: - Tags and switches

/// A small sentence-case pill: `border.strong` hairline on `bg.subtle`,
/// 12/500 in `text.label`.
struct Badge: View {
    var text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text)
            .font(.system(size: 12, weight: .medium))
            .foregroundStyle(Theme.textLabel)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
            .background(Theme.subtle, in: .capsule)
            .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
    }
}

/// TokenTracker's switch: green when on (an "on" state), grey when off, the
/// same whether or not the window is focused, unlike the system switch.
struct GreenSwitchStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        GreenSwitch(configuration: configuration)
    }
}

private struct GreenSwitch: View {
    let configuration: ToggleStyleConfiguration

    var body: some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            ZStack(alignment: configuration.isOn ? .trailing : .leading) {
                Capsule().fill(configuration.isOn ? Theme.accentFill : Theme.usageTrack)
                    .overlay(Capsule().strokeBorder(Theme.border, lineWidth: configuration.isOn ? 0 : 1))
                // A fixed knob, so the alignment moves it end to end.
                Circle().fill(.white).frame(width: 18, height: 18).padding(3)
                    .shadow(color: .black.opacity(0.15), radius: 1, y: 1)
            }
            .frame(width: 40, height: 24)
            .animation(.easeOut(duration: 0.15), value: configuration.isOn)
            .contentShape(Capsule())
        }
        // plainButton already dims a disabled switch to 0.4; no second dim.
        .plainButton(cornerRadius: 12)
        .accessibilityElement()
        .accessibilityLabel { _ in configuration.label }
        .accessibilityAddTraits(.isToggle)
        .accessibilityValue(configuration.isOn ? "On" : "Off")
        .accessibilityAction { configuration.isOn.toggle() }
    }
}

// MARK: - Fields

extension View {
    /// A themed text field for Settings and sheets: 36pt, radius 8,
    /// `bg.field`, a hairline that strengthens on focus, 14/20 text.
    /// Apply to a `TextField` or `SecureField`.
    /// `multiline` lets a vertical-axis TextField grow from 36pt as it wraps.
    func quiverField(monospaced: Bool = false, multiline: Bool = false) -> some View {
        modifier(QuiverField(monospaced: monospaced, multiline: multiline))
    }
}

private struct QuiverField: ViewModifier {
    var monospaced: Bool
    var multiline = false
    @FocusState private var focused: Bool

    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(monospaced ? .system(size: 13, design: .monospaced) : Theme.bodySmall)
            .foregroundStyle(Theme.textPrimary)
            .focused($focused)
            .padding(.horizontal, 12)
            .padding(.vertical, multiline ? 9 : 0)
            .frame(minHeight: Theme.fieldHeight, maxHeight: multiline ? nil : Theme.fieldHeight)
            .background(Theme.field, in: .rect(cornerRadius: Theme.fieldRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.fieldRadius)
                .strokeBorder(focused ? Theme.borderHover : Theme.border, lineWidth: 1))
            .focusRing(focused, cornerRadius: Theme.fieldRadius, afterPointer: true)
    }
}

// MARK: - States

/// Something went wrong: the message in danger red, selectable.
struct ErrorCard: View {
    var message: String

    var body: some View {
        Card {
            Label(message, systemImage: "exclamationmark.triangle")
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.dangerText)
                .textSelection(.enabled)
        }
    }
}

/// Nothing to show, or a quiet all-clear.
struct EmptyCard: View {
    var text: String
    var icon: String

    var body: some View {
        Card {
            Label(text, systemImage: icon)
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.textSecondary)
        }
    }
}

/// Work in progress: a small spinner with what it's doing.
struct LoadingCard: View {
    var text: String

    var body: some View {
        Card {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(text).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
            }
        }
    }
}
