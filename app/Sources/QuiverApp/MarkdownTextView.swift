import AppKit
import QuiverCore
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

    func makeCoordinator() -> Coordinator {
        Coordinator(text: $text, isFocused: $isFocused)
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
        textView.font = .systemFont(ofSize: 13)
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

        /// One full pass: reset to body attributes, then lay the spans on
        /// in the order MarkdownStyleSpans found them, so structural looks
        /// land first and muted marks land last.
        func restyle(_ textView: NSTextView) {
            guard let storage = textView.textStorage else { return }
            let full = NSRange(location: 0, length: storage.length)
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = 4
            storage.beginEditing()
            storage.setAttributes([
                .font: NSFont.systemFont(ofSize: 13),
                .foregroundColor: NSColor(Theme.textPrimary),
                .paragraphStyle: paragraph,
                .backgroundColor: NSColor.clear,
                .strikethroughStyle: 0,
            ], range: full)
            for span in MarkdownStyleSpans.spans(in: storage.string)
            where NSMaxRange(span.range) <= storage.length {
                apply(span, to: storage)
            }
            storage.endEditing()
        }

        private func apply(_ span: MarkdownStyleSpans.Span, to storage: NSTextStorage) {
            switch span.kind {
            case .heading(let level):
                let paragraph = NSMutableParagraphStyle()
                paragraph.lineSpacing = 4
                paragraph.paragraphSpacingBefore = level <= 2 ? 10 : 6
                storage.addAttributes([
                    .font: NSFont.systemFont(ofSize: MarkdownTextView.headingSizes[min(level, 6) - 1],
                                             weight: .semibold),
                    .paragraphStyle: paragraph,
                ], range: span.range)
            case .headingMarker, .marker, .fence, .linkURL, .comment:
                storage.addAttribute(.foregroundColor, value: NSColor(Theme.textMuted),
                                     range: span.range)
            case .bold:
                addTrait(.boldFontMask, at: span.range, in: storage)
            case .italic:
                addTrait(.italicFontMask, at: span.range, in: storage)
            case .strike:
                storage.addAttribute(.strikethroughStyle, value: NSUnderlineStyle.single.rawValue,
                                     range: span.range)
            case .inlineCode, .codeBlock:
                storage.addAttributes([
                    .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
                    .backgroundColor: NSColor(Theme.code),
                ], range: span.range)
            case .link:
                storage.addAttribute(.foregroundColor, value: NSColor(Theme.accent),
                                     range: span.range)
            case .quote:
                storage.addAttribute(.foregroundColor, value: NSColor(Theme.textSecondary),
                                     range: span.range)
            }
        }

        /// Bold and italic ride on whatever the range already has (a bold
        /// heading stays heading-sized), so convert the font in place.
        private func addTrait(_ trait: NSFontTraitMask, at range: NSRange, in storage: NSTextStorage) {
            guard let font = storage.attribute(.font, at: range.location, effectiveRange: nil)
                    as? NSFont else { return }
            storage.addAttribute(.font, value: NSFontManager.shared.convert(font, toHaveTrait: trait),
                                 range: range)
        }
    }

    private static let headingSizes: [CGFloat] = [22, 18, 16, 14, 13, 13]
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
