import SwiftUI

/// Every copy button in the app. A click runs `copy`, turns the icon into
/// a green check, and shows "Copied" above it for a second; the icon goes
/// back after five seconds. Another click starts both timers again.
/// Callers style it like any button (`.iconButton()`, `.plainButton()`).
struct CopyButton: View {
    var title = "Copy"
    var help = "Copy"
    /// The icon's point size; nil keeps the button style's own.
    var iconSize: CGFloat?
    var copy: () -> Void

    static let popupSeconds = 1.0
    static let resetSeconds = 5.0

    @State private var copied = false
    @State private var showsPopup = false
    /// Bumped on every click so an older click's timers do nothing.
    @State private var generation = 0

    var body: some View {
        Button(action: press) {
            Label {
                Text(copied ? "Copied" : title)
            } icon: {
                Image(systemName: copied ? "checkmark" : "doc.on.doc")
                    .font(iconSize.map { Theme.font(size: $0, weight: copied ? .bold : .regular) })
                    .foregroundStyle(copied ? AnyShapeStyle(Theme.success) : AnyShapeStyle(.foreground))
                    .contentTransition(.symbolEffect(.replace))
            }
            .labelStyle(.iconOnly)
        }
        .hoverTip(copied ? "Copied" : help)
        .accessibilityLabel(copied ? "Copied" : title)
        .overlay(alignment: .top) {
            if showsPopup {
                Text("Copied")
                    .font(Theme.caption).foregroundStyle(Theme.textPrimary)
                    .fixedSize()
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Theme.panel, in: .capsule)
                    .overlay(Capsule().strokeBorder(Theme.border, lineWidth: 1))
                    .shadow(color: .black.opacity(0.18), radius: 4, y: 1)
                    .offset(y: -26)
                    .transition(.opacity.combined(with: .offset(y: 4)))
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .zIndex(showsPopup ? 1 : 0)
    }

    private func press() {
        copy()
        generation += 1
        let run = generation
        withAnimation(.easeOut(duration: Theme.fadeDuration)) {
            copied = true
            showsPopup = true
        }
        Task {
            try? await Task.sleep(for: .seconds(Self.popupSeconds))
            guard run == generation else { return }
            withAnimation(.easeOut(duration: Theme.fadeDuration)) { showsPopup = false }
            try? await Task.sleep(for: .seconds(Self.resetSeconds - Self.popupSeconds))
            guard run == generation else { return }
            withAnimation(.easeOut(duration: Theme.fadeDuration)) { copied = false }
        }
    }
}
