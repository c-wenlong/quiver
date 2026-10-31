import AppKit
import QuiverCore
import SwiftUI

// The shared controls, sized and coloured from the TokenTracker package's
// components.md (measured values) and tokens.json, coloured per the Devin
// desktop package's colour roles. Quiver's own rules win: sentence case,
// emerald only for money and "on", and Devin's 1pt accent ring on every
// keyboard-focused control.

// MARK: - Focus

extension View {
    /// The 1pt accent focus ring, driven by this view's own focus.
    ///
    /// A button style never learns whether its button has focus, so the
    /// ring lives here, on the control itself: it binds a `@FocusState`
    /// and replaces macOS's default ring.
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
    /// Near-black in light, white in dark (Devin's `accent-neutral` ink
    /// button): 28pt, radius 8, 12/500, grows to 1.03 under the pointer.
    func primaryButton() -> some View {
        buttonStyle(PrimaryButtonStyle()).focusRing(cornerRadius: Theme.controlRadius)
    }

    /// The same primary shape in `success` green — the confirm action
    /// for a good outcome (GitHub's merge button).
    func successButton() -> some View {
        buttonStyle(PrimaryButtonStyle(background: Theme.success,
                                       foreground: Theme.successForeground))
            .focusRing(cornerRadius: Theme.controlRadius)
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

    /// A sheet's outer shape: `padding` inside a fixed `width` frame.
    func sheetScaffold(width: CGFloat = Theme.sheetWidth, padding: CGFloat = Theme.sheetPadding) -> some View {
        self.padding(padding).frame(width: width)
    }
}

/// A row's "more" menu: a quiet ellipsis with a hover shape, no indicator.
struct EllipsisMenu<Content: View>: View {
    @ViewBuilder var content: Content

    init(@ViewBuilder content: () -> Content) { self.content = content() }

    var body: some View {
        Menu { content } label: {
            Image(systemName: "ellipsis")
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 24, height: 20)
                .contentShape(.rect)
        }
        .menuStyle(.button)
        .plainButton()
        .menuIndicator(.hidden)
        .fixedSize()
        .help("More")
        .accessibilityLabel("More")
    }
}

/// A labelled form field: a control-strength caption over the content,
/// with an optional hint that reads as a problem in `dangerText`.
struct FormField<Content: View>: View {
    var label: String
    var hint: String?
    var problem = false
    @ViewBuilder var content: Content

    init(_ label: String, hint: String? = nil, problem: Bool = false,
         @ViewBuilder content: () -> Content) {
        self.label = label
        self.hint = hint
        self.problem = problem
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label).font(Theme.control).foregroundStyle(Theme.textSecondary)
            content
            if let hint {
                Text(hint).font(Theme.caption).foregroundStyle(problem ? Theme.dangerText : Theme.textMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    var background = Theme.primaryBackground
    var foreground = Theme.primaryForeground

    func makeBody(configuration: Configuration) -> some View {
        PrimaryButtonBody(configuration: configuration,
                          background: background, foreground: foreground)
    }
}

private struct PrimaryButtonBody: View {
    let configuration: ButtonStyleConfiguration
    var background: Color
    var foreground: Color
    @Environment(\.isEnabled) private var isEnabled
    @State private var hovering = false

    var body: some View {
        configuration.label
            .font(Theme.control)
            .labelStyle(.titleAndIcon)
            .padding(.horizontal, 12)
            .frame(height: Theme.controlHeight)
            .foregroundStyle(foreground)
            .background(background, in: .rect(cornerRadius: Theme.controlRadius))
            .scaleEffect(hovering && isEnabled && !configuration.isPressed ? 1.03 : 1)
            .opacity(isEnabled ? (configuration.isPressed ? 0.85 : 1) : 0.45)
            .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
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
            .font(Theme.font(size: 16))
            .foregroundStyle(hovering && isEnabled ? Theme.textStrong : Theme.textNav)
            .frame(width: Theme.iconButton, height: Theme.iconButton)
            .background(hovering && isEnabled ? Theme.selected : .clear,
                        in: .rect(cornerRadius: Theme.iconButtonRadius))
            .overlay(RoundedRectangle(cornerRadius: Theme.iconButtonRadius).strokeBorder(Theme.border, lineWidth: 1))
            .opacity(isEnabled ? (configuration.isPressed ? 0.7 : 1) : 0.45)
            .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
            .contentShape(.rect(cornerRadius: Theme.iconButtonRadius))
            .onHover { hovering = $0 }
    }
}

/// A full-width toggle row like a Notion calendar entry: no chrome, a
/// `rowHover` wash under the pointer, the whole row clickable.
struct LegendRowStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        LegendRowBody(configuration: configuration)
    }
}

private struct LegendRowBody: View {
    let configuration: ButtonStyleConfiguration
    @State private var hovering = false

    var body: some View {
        configuration.label
            .background(hovering ? Theme.rowHover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .opacity(configuration.isPressed ? 0.7 : 1)
            .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
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
    /// A real image before a tab's title (an app's own icon), by tab value.
    /// Wins over `icons` when both name a tab.
    var iconImages: [Value: NSImage] = [:]
    /// Runs on every tap, including on the tab already selected, so a
    /// detail page under a tab can go back on a reselect.
    var onSelect: ((Value) -> Void)? = nil
    /// Segmented only: the track takes the offered width and the tabs
    /// split it evenly, instead of hugging their titles.
    var fillWidth = false
    /// Segmented only: wraps the tabs into this many equal columns on as
    /// many rows as they need, so a long row of titled tabs still fits a
    /// narrow card. One (the default) keeps them on a single row.
    var columns = 1
    /// Tabs shown dimmed that can't be picked, like an app that isn't
    /// installed. One already selected still reads as selected.
    var disabled: Set<Value> = []
    @FocusState private var focused: Int?

    var body: some View {
        Group {
            switch style {
            case .underline:
                VStack(alignment: .leading, spacing: 0) {
                    HStack(spacing: 20) { buttons }
                    Rectangle().fill(Theme.border).frame(height: Theme.hairline)
                }
            case .segmented:
                Group {
                    if columns > 1 {
                        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 2),
                                                 count: columns),
                                  spacing: 2) { buttons }
                    } else {
                        HStack(spacing: 2) { buttons }
                            .fixedSize(horizontal: !fillWidth, vertical: true)
                    }
                }
                .padding(2)
                .background(Theme.segmentTrack, in: .rect(cornerRadius: Theme.blockRadius))
                .overlay(RoundedRectangle(cornerRadius: Theme.blockRadius).strokeBorder(Theme.border, lineWidth: 1))
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
            TabButton(title: option.title, icon: icons[option.value], iconImage: iconImages[option.value],
                      isSelected: option.value == selection,
                      isFocused: focused == index, style: style, onPanel: onPanel, fillWidth: fillWidth) {
                selection = option.value
                onSelect?(option.value)
            }
                .focused($focused, equals: index)
                .disabled(disabled.contains(option.value))
        }
    }

    private func step(_ offset: Int) -> KeyPress.Result {
        guard let index = options.firstIndex(where: { $0.value == selection }) else { return .ignored }
        var next = index + offset
        while options.indices.contains(next), disabled.contains(options[next].value) { next += offset }
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
    var iconImage: NSImage?
    var isSelected: Bool
    var isFocused: Bool
    var style: Tabs<Value>.Style
    var onPanel: Bool
    var fillWidth = false
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
        if let iconImage {
            HStack(spacing: 6) {
                Image(nsImage: iconImage)
                    .resizable().interpolation(.high).scaledToFit()
                    .frame(width: 14, height: 14)
                Text(title)
            }
        } else if let icon, HarnessIcon.exists(for: icon) {
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
                .frame(maxWidth: fillWidth ? .infinity : nil)
                .frame(height: Theme.controlHeight)
                .background(fill, in: .rect(cornerRadius: radius))
                .shadow(color: style == .segmented && isSelected ? .black.opacity(0.08) : .clear, radius: 1, y: 1)
                .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
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
            .font(Theme.font(size: 12, weight: .medium))
            .foregroundStyle(Theme.textLabel)
            .lineLimit(1)
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
            .background(Theme.subtle, in: .capsule)
            .overlay(Capsule().strokeBorder(Theme.borderStrong, lineWidth: 1))
    }
}

/// Devin's switch: accent blue when on (a selected control), tinted track
/// when off, the same whether or not the window is focused, unlike the
/// system switch. 36x20 as `core.size.control.switch`.
struct AccentSwitchStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        AccentSwitch(configuration: configuration)
    }
}

private struct AccentSwitch: View {
    let configuration: ToggleStyleConfiguration

    var body: some View {
        Button {
            configuration.isOn.toggle()
        } label: {
            ZStack(alignment: configuration.isOn ? .trailing : .leading) {
                Capsule().fill(configuration.isOn ? Theme.interactive : Theme.usageTrack)
                    .overlay(Capsule().strokeBorder(Theme.border, lineWidth: configuration.isOn ? 0 : 1))
                // A fixed knob, so the alignment moves it end to end.
                Circle().fill(.white).frame(width: 14, height: 14).padding(3)
                    .shadow(color: .black.opacity(0.15), radius: 1, y: 1)
            }
            .frame(width: 36, height: 20)
            .animation(.easeOut(duration: Theme.fadeDuration), value: configuration.isOn)
            .contentShape(Capsule())
        }
        // plainButton already dims a disabled switch to 0.4; no second dim.
        .plainButton(cornerRadius: Theme.blockRadius)
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
            .font(monospaced ? Theme.font(size: 13, design: .monospaced) : Theme.bodySmall)
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

// MARK: - Expand row

/// The last row of a list: unfolds the rest of it, or folds it away again.
/// `.cardBottom` is the card's bottom edge, so its hover fill and focus ring
/// follow the card's bottom corners; `.inset` sits inside a padded card and
/// takes the control radius instead.
struct ExpandRow: View {
    enum Edge { case cardBottom, inset }

    var isExpanded: Bool
    var hidden: Int
    /// What the folded row says instead of "Show N more", when the count
    /// wording does not fit (a details row opens one section, not items).
    var collapsedTitle: String? = nil
    var help: (expand: String, collapse: String)
    var edge: Edge = .cardBottom
    var note: String?
    var action: () -> Void
    @State private var hovering = false
    @FocusState private var focused: Bool

    private var shape: AnyShape {
        switch edge {
        case .cardBottom:
            AnyShape(UnevenRoundedRectangle(bottomLeadingRadius: Theme.cardRadius,
                                            bottomTrailingRadius: Theme.cardRadius))
        case .inset:
            AnyShape(RoundedRectangle(cornerRadius: Theme.controlRadius))
        }
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: isExpanded ? "chevron.up" : "chevron.down")
                    .font(Theme.font(size: 12, weight: .semibold))
                Text(isExpanded ? "Show less" : collapsedTitle ?? (hidden == 1 ? "Show 1 more" : "Show \(hidden) more"))
                    .font(Theme.control)
                if let note {
                    Text("· \(note)")
                        .font(Theme.control).foregroundStyle(Theme.textMuted)
                }
            }
            .foregroundStyle(hovering ? Theme.textPrimary : Theme.textSecondary)
            .frame(maxWidth: .infinity)
            .frame(height: 36)
            .background(hovering ? Theme.rowHover : .clear, in: shape)
            .contentShape(shape)
        }
        .buttonStyle(RingedPlainStyle(cornerRadius: 0))
        .focused($focused)
        .focusEffectDisabled()
        .overlay(ring.opacity(focused && FocusVisibility.shared.fromKeyboard ? 1 : 0)
            .allowsHitTesting(false))
        .onHover { hovering = $0 }
        .help(isExpanded ? help.collapse : help.expand)
    }

    @ViewBuilder private var ring: some View {
        switch edge {
        case .cardBottom:
            UnevenRoundedRectangle(bottomLeadingRadius: Theme.cardRadius,
                                   bottomTrailingRadius: Theme.cardRadius)
                .inset(by: 2)
                .stroke(Theme.focus, lineWidth: Theme.focusRing)
        case .inset:
            RoundedRectangle(cornerRadius: Theme.controlRadius)
                .inset(by: 2)
                .stroke(Theme.focus, lineWidth: Theme.focusRing)
        }
    }
}

// MARK: - States

/// Something went wrong: the message in danger red, selectable, with an
/// optional retry on the right like EmptyCard's.
struct ErrorCard: View {
    var message: String
    var actionTitle: String? = nil
    var actionShortcut: KeyboardShortcut? = nil
    var action: (() -> Void)? = nil
    /// False inside a larger callout — renders bare, no second frame.
    var framed = true

    var body: some View {
        Group {
            if framed { Card { content } } else { content }
        }
    }

    private var content: some View {
        HStack {
            Label(message, systemImage: "exclamationmark.triangle")
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.dangerText)
                .textSelection(.enabled)
            Spacer()
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .secondaryButton()
                    .keyboardShortcut(actionShortcut)
            }
        }
    }
}

/// gh is missing or signed out: the one command that fixes it, a copy
/// affordance, and a Refresh that re-probes. Covers both `envSetup`
/// states on the GitHub pages.
struct GhSetupCard: View {
    var status: GhEnv.Status
    var refresh: () -> Void

    private var title: String {
        status == .unavailable
            ? "Download the gh CLI to enable GitHub features."
            : "Sign in to GitHub to enable GitHub features."
    }

    private var command: String {
        status == .unavailable ? "brew install gh" : "gh auth login"
    }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                Label(title, systemImage: "terminal")
                    .font(Theme.bodySmall)
                    .foregroundStyle(Theme.textSecondary)
                HStack(spacing: 8) {
                    Text(command)
                        .font(Theme.bodySmall.monospaced())
                        .foregroundStyle(Theme.textPrimary)
                        .textSelection(.enabled)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(Theme.field,
                                    in: .rect(cornerRadius: Theme.controlRadius))
                    CopyButton(title: "Copy", help: "Copy the command",
                               iconSize: 12) {
                        copyToPasteboard(command)
                    }
                    .iconButton()
                    Spacer()
                    Button("Refresh", systemImage: "arrow.clockwise",
                           action: refresh)
                        .secondaryButton()
                }
            }
        }
    }
}

/// Nothing to show, or a quiet all-clear.
struct EmptyCard: View {
    var text: String
    var icon: String
    var actionTitle: String? = nil
    var actionShortcut: KeyboardShortcut? = nil
    var action: (() -> Void)? = nil
    /// False when the card lives inside a larger callout already —
    /// the content renders bare instead of nesting a second frame.
    var framed = true

    var body: some View {
        Group {
            if framed { Card { content } } else { content }
        }
    }

    private var content: some View {
        HStack {
            Label(text, systemImage: icon)
                .font(Theme.bodySmall)
                .foregroundStyle(Theme.textSecondary)
            Spacer()
            if let actionTitle, let action {
                Button(actionTitle, action: action)
                    .secondaryButton()
                    .keyboardShortcut(actionShortcut)
            }
        }
    }
}

/// Work in progress: a small spinner with what it's doing.
struct LoadingCard: View {
    var text: String
    /// False inside a larger callout — renders bare, no second frame.
    var framed = true

    var body: some View {
        Group {
            if framed { Card { content } } else { content }
        }
    }

    private var content: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text(text).font(Theme.bodySmall).foregroundStyle(Theme.textSecondary)
        }
    }
}

/// A placeholder bar with a light band sweeping across it, for skeleton
/// loading states laid out like what they stand in for. The band is an
/// offset, so the sweep never lays the page out again.
struct SkeletonBlock: View {
    var width: CGFloat?
    var height: CGFloat
    var radius: CGFloat = 6

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var swept = false

    var body: some View {
        RoundedRectangle(cornerRadius: radius)
            .fill(Theme.border)
            .overlay {
                if !reduceMotion {
                    GeometryReader { geometry in
                        LinearGradient(colors: [.clear, Theme.borderStrong.opacity(0.7), .clear],
                                       startPoint: .leading, endPoint: .trailing)
                            .frame(width: 160)
                            .offset(x: swept ? geometry.size.width : -160)
                    }
                }
            }
            .clipShape(.rect(cornerRadius: radius))
            .frame(width: width, height: height)
            .frame(maxWidth: width == nil ? .infinity : nil, alignment: .leading)
            .onAppear {
                withAnimation(.linear(duration: 1.4).repeatForever(autoreverses: false)) { swept = true }
            }
            .accessibilityHidden(true)
    }
}

/// The component a `SkeletonRows` stands in for. Each case mirrors one
/// real row layout, so the shimmer matches what lands rather than one
/// generic list everywhere.
enum SkeletonShape: Equatable {
    /// Icon, title over detail, trailing chip: the generic list row.
    case list
    /// Detail lines only: a facts panel like the Pages settings.
    case plain
    /// Sessions: mark, title over details, Tokens/Spend, a button slot.
    case session
    /// Tasks: a glyph, title with badges, subtitle, badges, a chevron.
    case task
    /// Repositories' rows: a state mark, title with badges, metadata,
    /// trailing marks.
    case issue
    /// Inbox notes: a dot, a mark, title over caption, a side button.
    case note
    /// Doctor and plugin drift: a severity dot, message, check line and
    /// the fix command strip.
    case check
    /// Harnesses: a mark, title over command and captions, a side badge.
    case harness
    /// Skills: name, description and path; source badge, count, chevron.
    case skill
    /// Marketplace plugins: title and version, ref, description, skill
    /// badges; harness chips on the right.
    case plugin
    /// Hooks: an event column and script rows with pills.
    case hook
    /// AGENTS.md links: a status dot, the mark, a state caption; laid
    /// out in a flow strip like the real links.
    case link
    /// Folder trees and scans: indented mono path lines.
    case file
    /// First-run agent tiles: a three-wide grid of icon and name tiles.
    case tiles
    /// First-run links preview: an icon, a title, a two-line detail.
    case feature
    /// A bar chart: columns of varied heights over a logo and a slanted
    /// label each, as the Models chart draws them.
    case chart
}

/// A stand-in for a list a fetch is still reading: `rows` fake entries in
/// the `shape` of the real rows, so the page doesn't jump when they land.
struct SkeletonRows: View {
    var rows = 6
    /// False inside a larger callout — renders bare, no second frame.
    var framed = true
    /// Which real row these stand in for.
    var shape: SkeletonShape = .list
    /// What VoiceOver says while the real rows load — the text the
    /// spinner card used to say.
    var label: String = "Loading"

    var body: some View {
        Group {
            if framed { Card { content } } else { content }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(label))
    }

    @ViewBuilder private var content: some View {
        switch shape {
        case .session: sessionBody
        case .link: linkBody
        case .tiles: tilesBody
        case .chart: chartBody
        default: listBody
        }
    }

    /// Grouped boxes (tasks, repositories) split their rows under
    /// section strips; the strip drops in before the first and the
    /// middle row.
    private var grouped: Bool { shape == .task || shape == .issue }

    private var listBody: some View {
        VStack(spacing: 0) {
            ForEach(0..<rows, id: \.self) { row in
                if grouped && (row == 0 || row == rows / 2) { groupHead }
                self.row(row)
            }
        }
    }

    /// The grouped box's section strip: source / name, then its rows.
    private var groupHead: some View {
        HStack(spacing: 6) {
            SkeletonBlock(width: 110, height: 12)
            Spacer()
            SkeletonBlock(width: 40, height: 14)
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
    }

    @ViewBuilder private func row(_ row: Int) -> some View {
        switch shape {
        case .session: sessionRow(row)
        case .task: ghRow(row, chevron: true)
        case .issue: ghRow(row, chevron: false)
        case .note: noteRow(row)
        case .check: checkRow(row)
        case .harness: harnessRow(row)
        case .skill: skillRow(row)
        case .plugin: pluginRow(row)
        case .hook: hookRow(row)
        case .link: linkChip(row)
        case .file: fileRow(row)
        case .feature: featureRow(row)
        case .plain: plainRow(row)
        case .list, .tiles, .chart: listRow(row)
        }
    }

    private func listRow(_ row: Int) -> some View {
        HStack(spacing: 10) {
            SkeletonBlock(width: 16, height: 16, radius: 8)
            VStack(alignment: .leading, spacing: 6) {
                SkeletonBlock(height: 12)
                    .padding(.trailing, CGFloat(row % 3) * 64 + 40)
                SkeletonBlock(height: 10)
                    .padding(.trailing, CGFloat(row % 2) * 96 + 120)
            }
            Spacer(minLength: 8)
            SkeletonBlock(width: 48, height: 16, radius: 8)
        }
        .padding(.vertical, 10)
    }

    private func plainRow(_ row: Int) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            SkeletonBlock(height: 12)
                .padding(.trailing, CGFloat(row % 3) * 64 + 40)
            SkeletonBlock(height: 10)
                .padding(.trailing, CGFloat(row % 2) * 96 + 120)
        }
        .padding(.vertical, 10)
    }

    // MARK: sessions

    /// The list's own header and day groups: the three column headings,
    /// then rows of mark + title/sub + Tokens/Spend + a button slot.
    private var sessionBody: some View {
        VStack(spacing: 0) {
            HStack(spacing: 16) {
                SkeletonBlock(width: 110, height: 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                SkeletonBlock(width: 40, height: 10)
                    .frame(width: tokensColumnWidth, alignment: .trailing)
                SkeletonBlock(width: 34, height: 10)
                    .frame(width: spendColumnWidth, alignment: .trailing)
                Color.clear.frame(width: Theme.iconButton)
            }
            .padding(.horizontal, 8).padding(.bottom, 10)
            Divider().overlay(Theme.border)
            ForEach(0..<rows, id: \.self) { row in
                if row == 0 || row == rows / 2 {
                    SkeletonBlock(width: 92, height: 12)
                        .padding(.horizontal, 8)
                        .padding(.top, 18).padding(.bottom, 10)
                    Divider().overlay(Theme.border)
                }
                sessionRow(row)
                Divider().overlay(Theme.rowDivider)
            }
        }
    }

    private func sessionRow(_ row: Int) -> some View {
        HStack(spacing: 16) {
            HStack(alignment: .center, spacing: 12) {
                SkeletonBlock(width: 16, height: 16, radius: 8)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        SkeletonBlock(width: 150 - CGFloat(row % 3) * 30, height: 12)
                        if row % 3 == 0 { SkeletonBlock(width: 46, height: 15, radius: 7) }
                    }
                    SkeletonBlock(width: 190 - CGFloat(row % 2) * 50, height: 10)
                }
            }
            Spacer(minLength: 8)
            SkeletonBlock(width: 40, height: 12)
                .frame(width: tokensColumnWidth, alignment: .trailing)
            SkeletonBlock(width: 32, height: 12)
                .frame(width: spendColumnWidth, alignment: .trailing)
            SkeletonBlock(width: 24, height: 24, radius: 7)
                .frame(width: Theme.iconButton)
        }
        .padding(.horizontal, 8).padding(.vertical, 10)
    }

    // MARK: GitHub-style rows (tasks, repositories)

    private func ghRow(_ row: Int, chevron: Bool) -> some View {
        HStack(alignment: .top, spacing: 12) {
            SkeletonBlock(width: 15, height: 15, radius: 7).padding(.top, 1)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    SkeletonBlock(width: 200 - CGFloat(row % 3) * 36, height: 12)
                    if row % 2 == 0 { SkeletonBlock(width: 54, height: 15, radius: 7) }
                }
                SkeletonBlock(width: 230 - CGFloat(row % 2) * 40, height: 10)
            }
            Spacer(minLength: 12)
            if row % 2 == 1 { SkeletonBlock(width: 42, height: 15, radius: 7) }
            if chevron { SkeletonBlock(width: 8, height: 14).padding(.top, 2) }
        }
        .padding(.vertical, 10).padding(.horizontal, 14)
    }

    // MARK: inbox

    private func noteRow(_ row: Int) -> some View {
        HStack(spacing: 8) {
            SkeletonBlock(width: 8, height: 8, radius: 4)
            SkeletonBlock(width: 15, height: 15, radius: 4)
            VStack(alignment: .leading, spacing: 4) {
                SkeletonBlock(width: 210 - CGFloat(row % 3) * 34, height: 12)
                SkeletonBlock(width: 150 - CGFloat(row % 2) * 30, height: 10)
            }
            Spacer(minLength: 8)
            if row % 3 == 0 { SkeletonBlock(width: 30, height: 30, radius: 8) }
        }
        .padding(.vertical, 10).padding(.horizontal, 6)
    }

    // MARK: doctor and plugin drift

    private func checkRow(_ row: Int) -> some View {
        HStack(alignment: .top, spacing: 10) {
            SkeletonBlock(width: 8, height: 8, radius: 4).padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    if row % 2 == 0 { SkeletonBlock(width: 16, height: 16, radius: 4) }
                    SkeletonBlock(width: 240 - CGFloat(row % 3) * 40, height: 12)
                }
                SkeletonBlock(width: 170 - CGFloat(row % 2) * 40, height: 10)
                if row % 3 != 2 {
                    HStack(spacing: 8) {
                        SkeletonBlock(height: 18, radius: 5)
                        SkeletonBlock(width: 30, height: 30, radius: 8)
                    }
                    .padding(EdgeInsets(top: 6, leading: 12, bottom: 6, trailing: 6))
                    .background(Theme.subtle, in: .rect(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Theme.border, lineWidth: 1))
                }
            }
            Spacer(minLength: 10)
            if row % 3 == 1 { SkeletonBlock(width: 40, height: 24, radius: 6) }
        }
        .padding(.vertical, 12)
    }

    // MARK: harnesses

    private func harnessRow(_ row: Int) -> some View {
        HStack(alignment: .top, spacing: 10) {
            SkeletonBlock(width: 16, height: 16, radius: 8)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    SkeletonBlock(width: 96, height: 13)
                    SkeletonBlock(width: 70, height: 10, radius: 4)
                    if row % 2 == 0 { SkeletonBlock(width: 50, height: 15, radius: 7) }
                }
                SkeletonBlock(width: 230 - CGFloat(row % 3) * 30, height: 10)
                if row % 2 == 1 { SkeletonBlock(width: 140, height: 9) }
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 4) {
                SkeletonBlock(width: 62, height: 15, radius: 7)
                if row % 2 == 0 { SkeletonBlock(width: 90, height: 9) }
            }
        }
        .padding(.vertical, 12)
    }

    // MARK: skills

    private func skillRow(_ row: Int) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                SkeletonBlock(width: 120 - CGFloat(row % 3) * 20, height: 13)
                if row % 3 != 2 { SkeletonBlock(width: 260 - CGFloat(row % 2) * 40, height: 10) }
                SkeletonBlock(width: 180 - CGFloat(row % 2) * 30, height: 9)
            }
            Spacer(minLength: 12)
            HStack(spacing: 10) {
                SkeletonBlock(width: 56, height: 15, radius: 7)
                SkeletonBlock(width: 20, height: 10)
                SkeletonBlock(width: 8, height: 14)
            }
        }
        .padding(.vertical, 10)
    }

    // MARK: plugins

    private func pluginRow(_ row: Int) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    SkeletonBlock(width: 130 - CGFloat(row % 3) * 24, height: 13)
                    SkeletonBlock(width: 28, height: 10)
                }
                if row % 2 == 0 { SkeletonBlock(width: 150, height: 9) }
                SkeletonBlock(width: 250 - CGFloat(row % 3) * 40, height: 10)
                if row % 2 == 1 {
                    HStack(spacing: 4) {
                        SkeletonBlock(width: 60, height: 14, radius: 7)
                        SkeletonBlock(width: 72, height: 14, radius: 7)
                    }
                    .padding(.top, 2)
                }
            }
            Spacer(minLength: 12)
            HStack(spacing: 6) {
                SkeletonBlock(width: 84, height: 24, radius: 8)
                if row % 2 == 1 { SkeletonBlock(width: 84, height: 24, radius: 8) }
            }
        }
        .padding(.vertical, 12)
    }

    // MARK: hooks

    private func hookRow(_ row: Int) -> some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                SkeletonBlock(width: 110 - CGFloat(row % 2) * 20, height: 12)
                SkeletonBlock(width: 70, height: 9)
            }
            .frame(width: 168, alignment: .leading)
            .padding(.top, 5)
            VStack(alignment: .leading, spacing: 6) {
                ForEach(0..<(row % 2 + 1), id: \.self) { _ in
                    HStack(spacing: 8) {
                        SkeletonBlock(width: 130 - CGFloat(row % 3) * 20, height: 12)
                        SkeletonBlock(width: 56, height: 14, radius: 7)
                        SkeletonBlock(width: 100, height: 10)
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .padding(.vertical, 8)
    }

    // MARK: AGENTS.md links

    /// The links strip's own layout: the heading and the link chips
    /// flowing beside it, the Link button on the right.
    private var linkBody: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            SkeletonBlock(width: 80, height: 15)
            FlowLayout(spacing: 14, lineSpacing: 6) {
                ForEach(0..<max(rows, 4), id: \.self) { row in linkChip(row) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            SkeletonBlock(width: 112, height: 28, radius: 8)
        }
    }

    private func linkChip(_ row: Int) -> some View {
        HStack(spacing: 6) {
            SkeletonBlock(width: Theme.statusDot, height: Theme.statusDot, radius: Theme.statusDot / 2)
            SkeletonBlock(width: 64 + CGFloat(row % 3) * 12, height: 13)
            if row % 3 == 1 { SkeletonBlock(width: 44, height: 10) }
        }
    }

    // MARK: files, tiles, features, chart

    private func fileRow(_ row: Int) -> some View {
        HStack(spacing: 6) {
            if row % 2 == 0 { SkeletonBlock(width: 10, height: 10) }
            SkeletonBlock(width: 150 - CGFloat(row % 3) * 24, height: 12)
        }
        .padding(.leading, CGFloat(row % 3) * 16)
        .padding(.vertical, 6)
    }

    /// First-run's agent picker: the same three-column tile grid.
    private var tilesBody: some View {
        LazyVGrid(columns: [GridItem](repeating: GridItem(.flexible(), spacing: 12), count: 3), spacing: 12) {
            ForEach(0..<max(rows, 3), id: \.self) { _ in
                HStack(spacing: 10) {
                    SkeletonBlock(width: 30, height: 30, radius: 8)
                    SkeletonBlock(width: 60, height: 12)
                    Spacer(minLength: 4)
                }
                .padding(.horizontal, 12)
                .frame(height: 52)
                .background(Theme.card, in: .rect(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.border, lineWidth: 1))
            }
        }
    }

    private func featureRow(_ row: Int) -> some View {
        HStack(alignment: .top, spacing: 12) {
            SkeletonBlock(width: 16, height: 16, radius: 4).padding(.top, 2)
            VStack(alignment: .leading, spacing: 4) {
                SkeletonBlock(width: 90 + CGFloat(row) * 30, height: 13)
                SkeletonBlock(height: 10).padding(.trailing, 60)
                SkeletonBlock(width: 200 - CGFloat(row % 2) * 40, height: 10)
            }
        }
        .padding(.vertical, 10)
    }

    /// The Models chart: its card title, then a descending run of bars,
    /// each over a logo and the deep zone a slanted label takes up.
    private var chartBody: some View {
        let heights: [CGFloat] = [1, 0.86, 0.8, 0.72, 0.68, 0.6, 0.55, 0.5,
                                  0.44, 0.4, 0.36, 0.33, 0.3, 0.27, 0.24, 0.2, 0.18, 0.15]
        return VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                SkeletonBlock(width: 150, height: 16)
                SkeletonBlock(width: 240, height: 10)
            }
            HStack(alignment: .bottom, spacing: 8) {
                ForEach(0..<heights.count, id: \.self) { index in
                    VStack(spacing: 4) {
                        Spacer(minLength: 0)
                        SkeletonBlock(height: heights[index] * 240, radius: 4)
                        SkeletonBlock(width: 18, height: 18, radius: 4)
                        SkeletonBlock(width: 8, height: 150, radius: 4)
                    }
                    .frame(maxWidth: .infinity)
                }
            }
            .frame(height: 456)
            SkeletonBlock(width: 190, height: 10)
        }
    }
}

/// A stand-in for a text body still being read — a file's lines, an
/// editor, a document preview.
struct SkeletonLines: View {
    var rows = 8
    /// False inside a larger callout — renders bare, no second frame.
    var framed = true
    /// What VoiceOver says while the body loads.
    var label: String = "Loading"

    var body: some View {
        Group {
            if framed { Card { content } } else { content }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(label))
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(0..<rows, id: \.self) { row in
                SkeletonBlock(height: 12)
                    .padding(.trailing, CGFloat(row % 4) * 56 + (row == rows - 1 ? 200 : 0))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A stand-in for a fetched detail — an issue, a pull request, a script —
/// while its body is still on the wire: a heading, some paragraph lines,
/// a row of chips.
struct SkeletonDetail: View {
    /// False inside a larger callout — renders bare, no second frame.
    var framed = true
    /// What VoiceOver says while the detail loads.
    var label: String = "Loading"

    var body: some View {
        Group {
            if framed { Card { content } } else { content }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(label))
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 14) {
            SkeletonBlock(width: 220, height: 20, radius: Theme.controlRadius)
            SkeletonLines(rows: 4, framed: false)
            HStack(spacing: 8) {
                ForEach(0..<2, id: \.self) { _ in SkeletonBlock(width: 64, height: 20, radius: 10) }
            }
        }
    }
}

// MARK: - Rail

/// A small chromeless icon button for header rows: a rail's header, or
/// a page header's action cluster.
struct RailIconButton: View {
    var symbol: String
    var help: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(Theme.font(size: 13))
                .foregroundStyle(Theme.textSecondary)
                .frame(width: 28, height: 28)
                .contentShape(.rect)
        }
        .plainButton(cornerRadius: Theme.controlRadius)
        .help(help)
        .accessibilityLabel(help)
    }
}

/// A rail row: a tap target with the selected and hover treatments a
/// rail list uses (the repos rail leaves its drag to ReorderableRows —
/// a Button would swallow the mouse-down the drag starts from).
struct RailRow<Content: View>: View {
    var selected: Bool
    var select: () -> Void
    @ViewBuilder var content: Content
    @State private var hovering = false

    var body: some View {
        HStack(spacing: 8) { content }
            .foregroundStyle(selected ? Theme.textPrimary : Theme.textNav)
            .padding(.horizontal, 8)
            .frame(height: Theme.navRowHeight)
            .background(selected ? Theme.navActive : hovering ? Theme.hover : .clear,
                        in: .rect(cornerRadius: Theme.controlRadius))
            .contentShape(.rect)
            .onHover { hovering = $0 }
            .onTapGesture(perform: select)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            .accessibilityAction(.default, select)
    }
}
