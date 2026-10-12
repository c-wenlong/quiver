import AppKit
import SwiftUI

/// The app's small "what is this" affordance: a tip that opens after the
/// pointer rests a beat, hides when the pointer leaves, and pins open on
/// click (a second click, or one outside, dismisses it). Used where
/// `.help` would hide the answer behind a delay nobody waits out.
struct HoverTip: ViewModifier {
    var text: String

    @State private var open = false
    /// Opened by a click, so leaving the icon doesn't close it.
    @State private var pinned = false
    @State private var rest: Task<Void, Never>?
    @State private var hovering = false

    func body(content: Content) -> some View {
        content
            .contentShape(.rect)
            // The tap pins the tip; a text selection would take it first.
            .textSelection(.disabled)
            .onTapGesture {
                rest?.cancel()
                if open, !pinned {
                    pinned = true
                } else if pinned {
                    open = false
                } else {
                    open = true
                    pinned = true
                }
            }
            .onHover { inside in
                if inside == hovering { return }
                hovering = inside
                if inside {
                    NSCursor.pointingHand.push()
                    rest = Task {
                        try? await Task.sleep(for: .milliseconds(150))
                        guard !Task.isCancelled else { return }
                        open = true
                    }
                } else {
                    NSCursor.pop()
                    rest?.cancel()
                    if !pinned { open = false }
                }
            }
            // A click outside the popover also dismisses it; the pin goes
            // with it so the next hover-only open closes on leaving.
            .onChange(of: open) { _, open in if !open { pinned = false } }
            .accessibilityLabel(text)
            .accessibilityAddTraits(.isButton)
            .popover(isPresented: $open, arrowEdge: .bottom) {
                Text(text)
                    .font(Theme.caption)
                    .foregroundStyle(Theme.textSecondary)
                    .frame(maxWidth: 260, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(10)
            }
            .onDisappear {
                rest?.cancel()
                if hovering { NSCursor.pop() }
            }
    }
}

extension View {
    /// A resting-hover and click tip, for small info marks and controls
    /// that need an explanation a `.help` tooltip would hide.
    func hoverTip(_ text: String) -> some View {
        modifier(HoverTip(text: text))
    }
}
