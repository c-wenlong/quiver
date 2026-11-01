import AppKit
import SWECore
import SwiftUI

/// The Rich half of the instruction editor: a live-styled markdown editor
/// over the same string the Raw view edits. Restyling only sets
/// attributes, never characters, so the file stays exactly as typed.
struct MarkdownTextView: NSViewRepresentable {
    @Binding var text: String
    @Binding var isFocused: Bool
    /// Increment to ask the editor to take focus (a view switch).
    var focusToken = 0
    var accessibilityLabel: String?
    /// Read in the parent's body, so a zoom change rebuilds this view and
    /// `updateNSView` restyles at the new size.
    var zoom = TextZoomModel.shared.zoom

    func makeCoordinator() -> Coordinator {
        let coordinator = Coordinator(text: $text, isFocused: $isFocused)
        coordinator.zoom = zoom
        return coordinator
    }

    func makeNSView(context: Context) -> NSScrollView {
        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = true
        scrollView.drawsBackground = false
        scrollView.borderType = .noBorder

        let textView = RichTextView()
        textView.minSize = NSSize(width: 0, height: 0)
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                                  height: CGFloat.greatestFiniteMagnitude)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.autoresizingMask = [.width]
        textView.textContainer?.containerSize = NSSize(width: scrollView.contentSize.width,
                                                       height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.widthTracksTextView = true
        textView.textContainerInset = NSSize(width: 0, height: 4)

        // Plain text: no ruler, no rich paste, and none of the writing
        // help that mangles config files.
        textView.isRichText = false
        textView.allowsUndo = true
        textView.usesFindBar = true
        textView.isIncrementalSearchingEnabled = true
        textView.isContinuousSpellCheckingEnabled = false
        textView.isGrammarCheckingEnabled = false
        textView.isAutomaticQuoteSubstitutionEnabled = false
        textView.isAutomaticDashSubstitutionEnabled = false
        textView.isAutomaticTextReplacementEnabled = false
        textView.isAutomaticTextCompletionEnabled = false
        textView.isAutomaticLinkDetectionEnabled = false
        textView.font = AppFonts.nsFont(size: 13)
        textView.textColor = NSColor(Theme.textPrimary)
        textView.insertionPointColor = NSColor(Theme.textPrimary)
        textView.delegate = context.coordinator
        textView.onFocusChange = { context.coordinator.isFocused.wrappedValue = $0 }
        if let accessibilityLabel { textView.setAccessibilityLabel(accessibilityLabel) }
        textView.string = text
        context.coordinator.textView = textView
        context.coordinator.restyle(textView)
        scrollView.documentView = textView
        return scrollView
    }

    func updateNSView(_ scrollView: NSScrollView, context: Context) {
        let coordinator = context.coordinator
        // SwiftUI may reuse this NSView for a different document (the
        // .id only covers file names), so rebind every update: edits and
        // focus reports must reach the document on screen, not the one
        // the view was created for.
        coordinator.text = $text
        coordinator.isFocused = $isFocused
        guard let textView = coordinator.textView else { return }
        if let accessibilityLabel { textView.setAccessibilityLabel(accessibilityLabel) }
        if textView.string != text {
            // Revert, Reload or a file switch: replace and restyle without
            // writing back into the binding, and keep the selection sane.
            coordinator.isProgrammatic = true
            let selected = textView.selectedRanges.compactMap(\.rangeValue)
            textView.string = text
            let length = (text as NSString).length
            textView.selectedRanges = selected.map { range in
                let location = min(range.location, length)
                return NSValue(range: NSRange(location: location,
                                              length: min(range.length, length - location)))
            }
            coordinator.restyle(textView)
            coordinator.isProgrammatic = false
        }
        if coordinator.zoom != zoom {
            coordinator.zoom = zoom
            coordinator.restyle(textView)
        }
        if coordinator.seenFocusToken != focusToken {
            coordinator.seenFocusToken = focusToken
            DispatchQueue.main.async { scrollView.window?.makeFirstResponder(textView) }
        }
    }

    @MainActor final class Coordinator: NSObject, NSTextViewDelegate {
        var text: Binding<String>
        var isFocused: Binding<Bool>
        var isProgrammatic = false
        var seenFocusToken = 0
        var zoom = TextZoom.standard
        weak var textView: NSTextView?

        init(text: Binding<String>, isFocused: Binding<Bool>) {
            self.text = text
            self.isFocused = isFocused
        }

        func textDidChange(_ notification: Notification) {
            guard let textView = notification.object as? NSTextView else { return }
            if !isProgrammatic { text.wrappedValue = textView.string }
            restyle(textView)
        }

        /// One full pass: reset to body attributes, then lay the spans
        /// on. The pass lives in SWECore so a test can render the same
        /// attributes without a view.
        func restyle(_ textView: NSTextView) {
            guard let storage = textView.textStorage else { return }
            MarkdownStyleSpans.apply(to: storage, palette: MarkdownTextView.palette(zoom: zoom))
        }
    }

    private static func palette(zoom: Double) -> MarkdownStylePalette {
        func size(_ points: CGFloat) -> CGFloat { TextZoom.pointSize(points, at: zoom) }
        return MarkdownStylePalette(
            bodyFont: AppFonts.nsFont(size: size(13)),
            codeFont: AppFonts.nsFont(size: size(12), monospaced: true),
            headingFonts: [22, 18, 16, 14, 13, 13].map { AppFonts.nsFont(size: size($0), weight: .semibold) },
            textPrimary: NSColor(Theme.textPrimary),
            textSecondary: NSColor(Theme.textSecondary),
            textMuted: NSColor(Theme.textMuted),
            accent: NSColor(Theme.link),
            codeBackground: NSColor(Theme.code),
            lineSpacing: 4,
            headingSpacingBefore: 10,
            subheadingSpacingBefore: 6)
    }
}

/// Reports first-responder changes, which NSTextViewDelegate has no
/// callback for; the box around the editor wants the focus look.
final class RichTextView: NSTextView {
    var onFocusChange: ((Bool) -> Void)?

    override func becomeFirstResponder() -> Bool {
        let became = super.becomeFirstResponder()
        if became { onFocusChange?(true) }
        return became
    }

    override func resignFirstResponder() -> Bool {
        let resigned = super.resignFirstResponder()
        if resigned { onFocusChange?(false) }
        return resigned
    }
}
