import AppKit
import Foundation

/// The fonts, colours and spacing a markdown span kind is painted with.
/// SWEApp fills one from its theme; a test uses plain system fonts so
/// traits are all it has to check. Shared read-only, hence Sendable by
/// convention rather than by the compiler's proof.
public struct MarkdownStylePalette: @unchecked Sendable {
    public var bodyFont: NSFont
    public var codeFont: NSFont
    /// Heading fonts, level 1 first; deeper levels reuse the last.
    public var headingFonts: [NSFont]
    public var textPrimary: NSColor
    public var textSecondary: NSColor
    public var textMuted: NSColor
    public var accent: NSColor
    public var codeBackground: NSColor
    public var lineSpacing: CGFloat
    /// Space above a level 1-2 heading.
    public var headingSpacingBefore: CGFloat
    /// Space above a deeper heading.
    public var subheadingSpacingBefore: CGFloat

    public init(bodyFont: NSFont, codeFont: NSFont, headingFonts: [NSFont],
                textPrimary: NSColor, textSecondary: NSColor, textMuted: NSColor,
                accent: NSColor, codeBackground: NSColor, lineSpacing: CGFloat,
                headingSpacingBefore: CGFloat, subheadingSpacingBefore: CGFloat) {
        self.bodyFont = bodyFont
        self.codeFont = codeFont
        self.headingFonts = headingFonts
        self.textPrimary = textPrimary
        self.textSecondary = textSecondary
        self.textMuted = textMuted
        self.accent = accent
        self.codeBackground = codeBackground
        self.lineSpacing = lineSpacing
        self.headingSpacingBefore = headingSpacingBefore
        self.subheadingSpacingBefore = subheadingSpacingBefore
    }

    public func headingFont(_ level: Int) -> NSFont {
        headingFonts.isEmpty ? bodyFont : headingFonts[min(level, headingFonts.count) - 1]
    }
}

extension MarkdownStyleSpans {
    /// `text` as the rich editor shows it. Attributes only: the string
    /// stays exactly as passed.
    public static func styled(_ text: String, palette: MarkdownStylePalette) -> NSAttributedString {
        let storage = NSTextStorage(string: text)
        apply(to: storage, palette: palette)
        return storage
    }

    /// One full pass over the storage: reset to body attributes, then lay
    /// the spans on in the order they were found, so structural looks
    /// land first and muted marks land last. Only attributes change, so
    /// this never marks the text edited. Ligatures stay off: Geist draws
    /// `->` and `-->` as one arrow glyph, so a comment's closing marker
    /// read as an arrow, and an editor should show the characters typed.
    public static func apply(to storage: NSTextStorage, palette: MarkdownStylePalette) {
        let full = NSRange(location: 0, length: storage.length)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = palette.lineSpacing
        storage.beginEditing()
        storage.setAttributes([
            .ligature: 0,
            .font: palette.bodyFont,
            .foregroundColor: palette.textPrimary,
            .paragraphStyle: paragraph,
            .backgroundColor: NSColor.clear,
            .strikethroughStyle: 0,
        ], range: full)
        for span in spans(in: storage.string)
        where NSMaxRange(span.range) <= storage.length {
            apply(span, to: storage, palette: palette)
        }
        storage.endEditing()
    }

    private static func apply(_ span: Span, to storage: NSTextStorage,
                              palette: MarkdownStylePalette) {
        switch span.kind {
        case .heading(let level):
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = palette.lineSpacing
            paragraph.paragraphSpacingBefore =
                level <= 2 ? palette.headingSpacingBefore : palette.subheadingSpacingBefore
            storage.addAttributes([
                .font: palette.headingFont(level),
                .paragraphStyle: paragraph,
            ], range: span.range)
        case .headingMarker, .marker, .fence, .linkURL, .comment:
            storage.addAttribute(.foregroundColor, value: palette.textMuted, range: span.range)
        case .bold:
            addTrait(.boldFontMask, at: span.range, in: storage)
        case .italic:
            addTrait(.italicFontMask, at: span.range, in: storage)
        case .strike:
            storage.addAttribute(.strikethroughStyle, value: NSUnderlineStyle.single.rawValue,
                                 range: span.range)
        case .inlineCode, .codeBlock:
            storage.addAttributes([
                .font: palette.codeFont,
                .backgroundColor: palette.codeBackground,
            ], range: span.range)
        case .link:
            storage.addAttribute(.foregroundColor, value: palette.accent, range: span.range)
        case .quote:
            storage.addAttribute(.foregroundColor, value: palette.textSecondary, range: span.range)
        }
    }

    /// Bold and italic ride on whatever each run already has: a bold
    /// heading stays heading-sized, and a bold inside an italic keeps
    /// its bold. Converting the font at the range's start and stamping
    /// it across would flatten a nested style, so convert every font
    /// run the range holds.
    private static func addTrait(_ trait: NSFontTraitMask, at range: NSRange,
                                 in storage: NSTextStorage) {
        var runs: [(range: NSRange, font: NSFont)] = []
        storage.enumerateAttribute(.font, in: range) { value, run, _ in
            if let font = value as? NSFont { runs.append((run, font)) }
        }
        for run in runs {
            storage.addAttribute(.font,
                                 value: NSFontManager.shared.convert(run.font, toHaveTrait: trait),
                                 range: run.range)
        }
    }
}
