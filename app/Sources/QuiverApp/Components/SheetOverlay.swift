import AppKit
import SwiftUI

// Every popup in the main window is a card over the window rather than a
// window-modal `.sheet`: a dimmed scrim covers the whole window (sidebar
// included), and a click on the scrim or Esc closes the card. A system
// sheet ignores clicks outside it, which made the app's popups feel stuck.
//
// A page presents with `.quiverSheet(isPresented:)` or `.quiverSheet(item:)`,
// the same shape as `.sheet`. The card travels up the view tree as a
// preference to the window's `.sheetHost()`, which draws it on top of
// everything. While a card is up, the window underneath is disabled, so its
// buttons, shortcuts and Tab stops can't fire behind the card. A sheet
// closes itself with `@Environment(\.dismissSheet)`; `\.dismiss` would
// close the window, since the card is not a presentation of its own.

extension View {
    /// Presents `content` as a card over the window while `isPresented` is
    /// true. `modal` keeps it up until its own buttons close it: no scrim
    /// click, no Esc. In a view with no sheet host above it (a window
    /// other than the main one), this falls back to a system sheet.
    func quiverSheet<Sheet: View>(isPresented: Binding<Bool>, modal: Bool = false,
                                  onDismiss: (() -> Void)? = nil,
                                  @ViewBuilder content: @escaping () -> Sheet) -> some View {
        modifier(FlagSheetPresenter(isPresented: isPresented, modal: modal,
                                    onDismiss: onDismiss, sheet: content))
    }

    /// Presents `content` for `item` while it is non-nil, like
    /// `.sheet(item:)`. A different item is a different card.
    func quiverSheet<Item: Identifiable, Sheet: View>(item: Binding<Item?>, modal: Bool = false,
                                                      onDismiss: (() -> Void)? = nil,
                                                      @ViewBuilder content: @escaping (Item) -> Sheet) -> some View {
        modifier(ItemSheetPresenter(item: item, modal: modal, onDismiss: onDismiss, sheet: content))
    }

    /// Draws the cards presented anywhere below this view over the whole of
    /// it. The main window applies it once, at its root.
    func sheetHost() -> some View {
        modifier(SheetHost(isRoot: true))
    }

    /// Answers a click on the scrim or Esc instead of closing the card at
    /// once: an editor with unsaved changes asks first. A sheet's own
    /// buttons still close it with `dismissSheet`.
    func onSheetDismissRequest(_ action: @escaping () -> Void) -> some View {
        background(DismissRequestRegistrar(action: action))
    }
}

/// Closes the sheet this view is in. A no-op outside a sheet.
struct DismissSheetAction {
    var action: @MainActor () -> Void

    @MainActor func callAsFunction() { action() }
}

extension EnvironmentValues {
    @Entry var dismissSheet = DismissSheetAction {}
    /// True under a `sheetHost()`; a presenter without one uses a system sheet.
    @Entry var hasSheetHost = false
    @Entry var sheetDismissRequest: DismissRequestSlot? = nil
}

// MARK: - Presenters

/// One presented card, as it travels up to the host.
struct SheetEntry: Identifiable, Equatable {
    var id: AnyHashable
    var modal: Bool
    var content: AnyView
    var dismiss: @MainActor () -> Void

    /// Same card, same place: a new body for it is not a new card.
    static func == (lhs: SheetEntry, rhs: SheetEntry) -> Bool {
        lhs.id == rhs.id && lhs.modal == rhs.modal
    }
}

private struct SheetStackKey: PreferenceKey {
    static var defaultValue: [SheetEntry] { [] }

    static func reduce(value: inout [SheetEntry], nextValue: () -> [SheetEntry]) {
        value.append(contentsOf: nextValue())
    }
}

private struct FlagSheetPresenter<Sheet: View>: ViewModifier {
    @Binding var isPresented: Bool
    var modal: Bool
    var onDismiss: (() -> Void)?
    var sheet: () -> Sheet
    @Environment(\.hasSheetHost) private var hosted
    @State private var id = UUID()

    func body(content: Content) -> some View {
        if hosted {
            content
                .transformPreference(SheetStackKey.self) { stack in
                    guard isPresented else { return }
                    let binding = $isPresented
                    stack.append(SheetEntry(id: id, modal: modal, content: AnyView(sheet()),
                                            dismiss: { binding.wrappedValue = false }))
                }
                .onChange(of: isPresented) { was, now in
                    if was && !now { onDismiss?() }
                }
        } else {
            content.sheet(isPresented: $isPresented, onDismiss: onDismiss) {
                let binding = $isPresented
                sheet()
                    .environment(\.dismissSheet, DismissSheetAction { binding.wrappedValue = false })
                    .interactiveDismissDisabled(modal)
            }
        }
    }
}

private struct ItemSheetPresenter<Item: Identifiable, Sheet: View>: ViewModifier {
    @Binding var item: Item?
    var modal: Bool
    var onDismiss: (() -> Void)?
    var sheet: (Item) -> Sheet
    @Environment(\.hasSheetHost) private var hosted
    @State private var id = UUID()

    private struct Key: Hashable {
        var presenter: UUID
        var item: AnyHashable
    }

    func body(content: Content) -> some View {
        if hosted {
            content
                .transformPreference(SheetStackKey.self) { stack in
                    guard let item else { return }
                    let binding = $item
                    stack.append(SheetEntry(id: Key(presenter: id, item: AnyHashable(item.id)), modal: modal,
                                            content: AnyView(sheet(item)),
                                            dismiss: { binding.wrappedValue = nil }))
                }
                // Like `.sheet(item:)`: the old card is dismissed whether
                // the item clears or another one replaces it.
                .onChange(of: item?.id) { was, now in
                    if was != nil && was != now { onDismiss?() }
                }
        } else {
            content.sheet(item: $item, onDismiss: onDismiss) { item in
                let binding = $item
                sheet(item)
                    .environment(\.dismissSheet, DismissSheetAction { binding.wrappedValue = nil })
                    .interactiveDismissDisabled(modal)
            }
        }
    }
}

// MARK: - Host

/// Reads the cards presented below it, disables what they cover, and draws
/// them on top. The root host always keeps its layer, so a card's arrival
/// animates; a card can present its own, which the layer's nested host
/// draws over it.
private struct SheetHost: ViewModifier {
    var isRoot: Bool
    @State private var covered = false

    func body(content: Content) -> some View {
        content
            .disabled(covered)
            .accessibilityHidden(covered)
            .onPreferenceChange(SheetStackKey.self) { stack in
                let up = !stack.isEmpty
                MainActor.assumeIsolated { if covered != up { covered = up } }
            }
            .overlayPreferenceValue(SheetStackKey.self) { stack in
                if isRoot || !stack.isEmpty {
                    // AnyView breaks the type recursion through the
                    // layer's own nested host.
                    AnyView(SheetLayer(stack: stack))
                }
            }
            // The cards are drawn here; nothing above should draw them again.
            .transformPreference(SheetStackKey.self) { $0 = [] }
            .environment(\.hasSheetHost, true)
    }
}

/// The cards over the window, oldest at the bottom. Only the top one is
/// enabled.
private struct SheetLayer: View {
    var stack: [SheetEntry]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            ForEach(stack) { entry in
                SheetCard(entry: entry)
                    .disabled(entry.id != stack.last?.id)
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(reduceMotion ? nil : Theme.sheetFade, value: stack.map(\.id))
        .modifier(SheetHost(isRoot: false))
    }
}

/// The scrim and one card, sized like a system sheet: its ideal size, never
/// past the window less `sheetMargin`, centred.
private struct SheetCard: View {
    var entry: SheetEntry
    @State private var request = DismissRequestSlot()
    @State private var settled = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            Theme.scrim
                .ignoresSafeArea()
                .contentShape(.rect)
                .onTapGesture { if !entry.modal { requestDismiss() } }
                .accessibilityElement()
                .accessibilityLabel("Close")
                .accessibilityAddTraits(entry.modal ? [] : .isButton)
                .accessibilityHidden(entry.modal)
            SheetCardLayout(margin: Theme.sheetMargin) {
                entry.content
                    .environment(\.dismissSheet, DismissSheetAction(action: entry.dismiss))
                    .environment(\.sheetDismissRequest, request)
                    .background(Theme.dialog)
                    .clipShape(.rect(cornerRadius: Theme.dialogRadius))
                    .overlay(RoundedRectangle(cornerRadius: Theme.dialogRadius)
                        .strokeBorder(Theme.border, lineWidth: 1))
                    .contentShape(.rect(cornerRadius: Theme.dialogRadius))
                    .background(SheetKeyBridge(modal: entry.modal, onEscape: requestDismiss))
                    .shadow(color: Theme.dialogShadow, radius: 30, y: 20)
                    .scaleEffect(settled || reduceMotion ? 1 : 0.98)
                    .accessibilityAddTraits(.isModal)
            }
        }
        .onAppear {
            withAnimation(reduceMotion ? nil : Theme.sheetSettle) { settled = true }
        }
    }

    private func requestDismiss() {
        if let intercept = request.intercept { intercept() } else { entry.dismiss() }
    }
}

/// A sheet's ideal size, capped to the room inside the margin, centred.
/// A system sheet sizes to its content the same way, so the sheets keep
/// their sizes; one that wants more room than the window has scrolls or
/// squeezes as it would in a short window.
private struct SheetCardLayout: Layout {
    var margin: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        proposal.replacingUnspecifiedDimensions()
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard let card = subviews.first else { return }
        let ideal = card.sizeThatFits(.unspecified)
        let offer = ProposedViewSize(width: min(ideal.width, max(0, bounds.width - 2 * margin)),
                                     height: min(ideal.height, max(0, bounds.height - 2 * margin)))
        card.place(at: CGPoint(x: bounds.midX, y: bounds.midY), anchor: .center, proposal: offer)
    }
}

// MARK: - Dismiss requests

/// Where a sheet's `onSheetDismissRequest` handler waits for the card's
/// scrim and Esc. A plain reference, not state: the handler is replaced on
/// every render so it never acts on a stale copy of the sheet.
@MainActor final class DismissRequestSlot {
    var intercept: (() -> Void)?
}

private struct DismissRequestRegistrar: View {
    var action: () -> Void
    @Environment(\.sheetDismissRequest) private var slot

    var body: some View {
        let _ = slot?.intercept = action
        Color.clear
            .accessibilityHidden(true)
            .onDisappear { slot?.intercept = nil }
    }
}

// MARK: - Keyboard and focus

/// Esc closes the top card (a sheet's Cancel button with `.cancelAction`
/// does the same thing, so nothing is lost by answering first). Pure, so
/// the rule is testable without a window.
enum SheetKeys {
    static let escapeKeyCode: UInt16 = 53

    static func closesOnEscape(keyCode: UInt16, flags: NSEvent.ModifierFlags, isTop: Bool,
                               modal: Bool, composing: Bool) -> Bool {
        let modifiers = flags.intersection(.deviceIndependentFlagsMask).subtracting([.function, .capsLock])
        // Esc mid-composition cancels the input method's text, not the card.
        return keyCode == escapeKeyCode && modifiers.isEmpty && isTop && !modal && !composing
    }
}

/// Windows with a card up, so ⌘W closes the card (PopupKeyRouting) rather
/// than the window, as it does for a system sheet.
@MainActor enum SheetOverlayRegistry {
    private static var counts: [ObjectIdentifier: Int] = [:]

    static func cover(_ window: NSWindow) {
        counts[ObjectIdentifier(window), default: 0] += 1
    }

    static func uncover(_ window: NSWindow) {
        let key = ObjectIdentifier(window)
        guard let count = counts[key] else { return }
        counts[key] = count > 1 ? count - 1 : nil
    }

    static func isCovered(_ window: NSWindow) -> Bool {
        counts[ObjectIdentifier(window)] != nil
    }
}

/// An AppKit view the size of the card, behind it: it answers Esc for the
/// top card, puts the cursor in the card's first field when it opens (a
/// system sheet's first responder), and hands focus back when it closes.
private struct SheetKeyBridge: NSViewRepresentable {
    var modal: Bool
    var onEscape: () -> Void

    func makeNSView(context: Context) -> SheetKeyBridgeView { SheetKeyBridgeView() }

    func updateNSView(_ view: SheetKeyBridgeView, context: Context) {
        // A card under another card is disabled, so `isEnabled` says
        // which bridge is on top.
        view.isTop = context.environment.isEnabled
        view.modal = modal
        view.onEscape = onEscape
    }

    static func dismantleNSView(_ view: SheetKeyBridgeView, coordinator: ()) {
        view.detach()
    }
}

final class SheetKeyBridgeView: NSView {
    var isTop = true
    var modal = false
    var onEscape: () -> Void = {}
    private var monitor: Any?
    private weak var attached: NSWindow?
    private weak var previousResponder: NSResponder?

    // Clicks belong to the card above it.
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let window { attach(to: window) } else { detach() }
    }

    private func attach(to window: NSWindow) {
        guard attached == nil else { return }
        attached = window
        SheetOverlayRegistry.cover(window)
        previousResponder = window.firstResponder
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            // Only Sendable values cross into assumeIsolated (see
            // PopupKeyRouting); the event itself is returned out here.
            let keyCode = event.keyCode
            let flags = event.modifierFlags
            let windowNumber = event.windowNumber
            let handled = MainActor.assumeIsolated {
                self?.handleKey(keyCode: keyCode, flags: flags, windowNumber: windowNumber) ?? false
            }
            return handled ? nil : event
        }
        focusFirstField(attempt: 0)
    }

    func detach() {
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        guard let window = attached else { return }
        attached = nil
        SheetOverlayRegistry.uncover(window)
        // Back to where the cursor was before the card, as a closing
        // system sheet does.
        if let previous = previousResponder as? NSView, previous.window === window,
           !previous.isHiddenOrHasHiddenAncestor {
            window.makeFirstResponder(previous)
        }
    }

    private func handleKey(keyCode: UInt16, flags: NSEvent.ModifierFlags, windowNumber: Int) -> Bool {
        guard let window = attached, window.windowNumber == windowNumber else { return false }
        let composing = (window.firstResponder as? NSTextView)?.hasMarkedText() ?? false
        guard SheetKeys.closesOnEscape(keyCode: keyCode, flags: flags, isTop: isTop,
                                       modal: modal, composing: composing) else { return false }
        onEscape()
        return true
    }

    /// The topmost, then leftmost, editable field inside the card. The
    /// window under the card is disabled, so its fields never qualify.
    /// Retried briefly: the card may not be laid out on its first turn.
    private func focusFirstField(attempt: Int) {
        DispatchQueue.main.asyncAfter(deadline: .now() + (attempt == 0 ? 0 : 0.05)) { [weak self] in
            guard let self, let window = self.attached, let root = window.contentView else { return }
            let card = self.convert(self.bounds, to: nil)
            guard card.width > 1, card.height > 1 else {
                if attempt < 4 { self.focusFirstField(attempt: attempt + 1) }
                return
            }
            if Self.isInside(window.firstResponder, card) { return }
            var best: (view: NSView, frame: NSRect)?
            Self.visit(root) { view in
                let frame = view.convert(view.bounds, to: nil)
                guard card.contains(CGPoint(x: frame.midX, y: frame.midY)) else { return }
                // Window coordinates grow upwards: higher maxY is nearer the top.
                if let current = best, current.frame.maxY > frame.maxY + 1
                    || (abs(current.frame.maxY - frame.maxY) <= 1 && current.frame.minX <= frame.minX) {
                    return
                }
                best = (view, frame)
            }
            if let best {
                window.makeFirstResponder(best.view)
            } else if attempt < 2 {
                self.focusFirstField(attempt: attempt + 1)
            }
        }
    }

    private static func visit(_ view: NSView, _ found: (NSView) -> Void) {
        guard !view.isHidden else { return }
        if let field = view as? NSTextField, field.isEditable, field.isEnabled {
            found(field)
        } else if let text = view as? NSTextView, text.isEditable, !text.isFieldEditor {
            found(text)
        }
        for child in view.subviews { visit(child, found) }
    }

    /// Whether the cursor is already in the card (a sheet that set its own
    /// focus): a field editor counts as the field it edits.
    private static func isInside(_ responder: NSResponder?, _ card: NSRect) -> Bool {
        let view: NSView?
        if let editor = responder as? NSTextView {
            view = editor.isFieldEditor ? editor.delegate as? NSView : editor
        } else {
            view = responder as? NSTextField
        }
        guard let view, view.window != nil else { return false }
        let frame = view.convert(view.bounds, to: nil)
        return card.contains(CGPoint(x: frame.midX, y: frame.midY))
    }
}
