import QuiverCore
import SwiftUI

/// One row in a catalogue sheet's two-column grid, after the "Connect
/// apps" layout the status and provider catalogues share: the entry's
/// mark, its name with a detail line under it, and a trailing Add pill
/// that turns into a quiet done-label ("Added", "Set") once the entry is
/// in place. The whole row is the button, like the provider catalogue's
/// rows always were: Return and Space fire it from the keyboard, and
/// VoiceOver hears one element ("Add Groq", or "Groq, Added").
struct CatalogueRow: View {
    /// The `HarnessIcons` id, or nil when the bundle has no mark; the
    /// square stays so the rows still line up.
    var logo: String?
    var name: String
    /// The second line: the page's host for a status page, the variable
    /// or vendor for a key.
    var detail: String? = nil
    /// The quiet label shown once the entry is in place.
    var done: String? = nil
    /// A done row still fires its action. The provider catalogue's Set
    /// rows use it — the pick edits the existing key or adds a sibling
    /// variable; the status catalogue leaves it false so a tracked page
    /// can't be added twice.
    var reusable = false
    var actionTitle = "Add"
    /// Awaited, so the row knows when the add is over.
    var action: () async -> Void
    /// Set while the action runs so a slow add can't be double-fired.
    /// Cleared when it returns: `done` takes over if the entry landed,
    /// and a failed add shows its button again instead of "Added".
    @State private var added = false
    @State private var hovering = false
    @FocusState private var focused: Bool

    private var isDone: Bool { done != nil || added }
    private var isActionable: Bool { !isDone || reusable }

    var body: some View {
        HStack(spacing: 10) {
            Group {
                if let logo { HarnessIcon(harness: logo, size: 16) } else { Color.clear }
            }
            .frame(width: 16, height: 16)
            VStack(alignment: .leading, spacing: 1) {
                Text(name).font(Theme.rowTitle).foregroundStyle(Theme.textPrimary).lineLimit(1)
                if let detail {
                    Text(detail).font(Theme.caption).foregroundStyle(Theme.textMuted).lineLimit(1)
                }
            }
            Spacer(minLength: 8)
            if isDone {
                Label(done ?? "Added", systemImage: "checkmark")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .accessibilityHidden(true)
            } else {
                // The secondary-button look; the row itself is the button.
                Text(actionTitle)
                    .font(Theme.control).foregroundStyle(Theme.textPrimary)
                    .padding(.horizontal, 10)
                    .frame(height: 26)
                    .background(Theme.secondaryBackground, in: .rect(cornerRadius: Theme.controlRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
                        .strokeBorder(Theme.borderStrong, lineWidth: 1))
                    .accessibilityHidden(true)
            }
        }
        .padding(.horizontal, 10)
        .frame(height: 46)
        .background(hovering && isActionable ? Theme.cardHover : Theme.panel,
                    in: .rect(cornerRadius: Theme.controlRadius))
        .overlay(RoundedRectangle(cornerRadius: Theme.controlRadius)
            .strokeBorder(hovering && isActionable ? Theme.borderHover : Theme.border, lineWidth: 1))
        .contentShape(.rect)
        .onTapGesture { _ = activate() }
        // A done row is no Tab stop; the rest fire on Return and Space,
        // the same keys a card on the page answers.
        .focusable(isActionable)
        .focusEffectDisabled()
        .focused($focused)
        .focusRing(focused, cornerRadius: Theme.controlRadius)
        .onKeyPress(.return) { activate() }
        .onKeyPress(.space) { activate() }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(isDone ? "\(name), \(done ?? "Added")" : "\(actionTitle) \(name)")
        .accessibilityAddTraits(isActionable ? .isButton : [])
        .accessibilityAction(.default) { _ = activate() }
        // Both lines clamp to one line of width; the tip keeps the full
        // name and detail readable ("Atlas Cloud Coding Plan",
        // "HUGGINGFACE_HUB_TOKEN, HF_TOKEN").
        .help(detail.map { "\(name)\n\($0)" } ?? name)
        .animation(.easeOut(duration: Theme.fadeDuration), value: hovering)
        .onHover { hovering = $0 }
    }

    private func activate() -> KeyPress.Result {
        guard isActionable, !added else { return .ignored }
        added = true
        Task {
            await action()
            added = false
        }
        return .handled
    }
}
