import AppKit
import Foundation
import Testing
@testable import SWECore

@Suite struct MarkdownStyleSpansTests {
    private typealias Span = MarkdownStyleSpans.Span
    private typealias Kind = MarkdownStyleSpans.Kind

    private func spans(_ text: String) -> [Span] {
        MarkdownStyleSpans.spans(in: text)
    }

    private func ranges(_ text: String, kind: Kind) -> [NSRange] {
        spans(text).filter { $0.kind == kind }.map(\.range)
    }

    @Test func aHeadingSplitsIntoMarkerAndText() {
        let spans = spans("# Title")
        #expect(spans.contains(Span(range: NSRange(location: 0, length: 2), kind: .headingMarker)))
        #expect(spans.contains(Span(range: NSRange(location: 2, length: 5), kind: .heading(1))))
    }

    @Test func deeperHeadingsKeepTheirLevel() {
        #expect(ranges("### T", kind: .headingMarker) == [NSRange(location: 0, length: 4)])
        #expect(ranges("### T", kind: .heading(3)) == [NSRange(location: 4, length: 1)])
        #expect(ranges("###### T", kind: .heading(6)).count == 1)
        #expect(ranges("####### T", kind: .heading(6)).isEmpty)
    }

    @Test func boldItalicAndStrikeMarkTheirMarkers() {
        let spans = spans("a **b** and *i* and ~~s~~")
        #expect(spans.contains(Span(range: NSRange(location: 2, length: 2), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 4, length: 1), kind: .bold)))
        #expect(spans.contains(Span(range: NSRange(location: 5, length: 2), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 12, length: 1), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 13, length: 1), kind: .italic)))
        #expect(spans.contains(Span(range: NSRange(location: 14, length: 1), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 20, length: 2), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 22, length: 1), kind: .strike)))
        #expect(spans.contains(Span(range: NSRange(location: 23, length: 2), kind: .marker)))
    }

    @Test func inlineCodeIsOneSpan() {
        #expect(ranges("a `x` b", kind: .inlineCode) == [NSRange(location: 2, length: 3)])
        #expect(ranges("a `x` b", kind: .marker).isEmpty)
    }

    @Test func aFenceSwallowsMarkdownInside() {
        let spans = spans("```\n**not bold**\n# not a heading\n```")
        #expect(spans.contains(Span(range: NSRange(location: 0, length: 3), kind: .fence)))
        #expect(spans.contains(Span(range: NSRange(location: 4, length: 12), kind: .codeBlock)))
        #expect(spans.contains(Span(range: NSRange(location: 17, length: 15), kind: .codeBlock)))
        #expect(spans.contains(Span(range: NSRange(location: 33, length: 3), kind: .fence)))
        for span in spans {
            #expect(![Kind.bold, .italic, .strike, .headingMarker, .link].contains(span.kind))
            #expect(![Kind.heading(1), .heading(2)].contains(span.kind))
        }
    }

    @Test func listAndQuoteMarkersAreMarked() {
        #expect(ranges("- item", kind: .marker) == [NSRange(location: 0, length: 1)])
        #expect(ranges("1. item", kind: .marker) == [NSRange(location: 0, length: 2)])
        #expect(ranges("> quoted", kind: .marker) == [NSRange(location: 0, length: 2)])
        #expect(ranges("> quoted", kind: .quote) == [NSRange(location: 2, length: 6)])
    }

    @Test func aLinkMarksTextAndURL() {
        #expect(ranges("see [docs](https://x)", kind: .link) == [NSRange(location: 5, length: 4)])
        #expect(ranges("see [docs](https://x)", kind: .linkURL) == [NSRange(location: 10, length: 11)])
    }

    @Test func aCommentIsOneMutedSpan() {
        #expect(ranges("a <!-- b --> c", kind: .comment) == [NSRange(location: 2, length: 10)])
    }

    @Test func rangesAreUTF16SoEmojiCountTwice() {
        #expect(ranges("🙂\n# T", kind: .headingMarker) == [NSRange(location: 3, length: 2)])
        #expect(ranges("🙂\n# T", kind: .heading(1)) == [NSRange(location: 5, length: 1)])
        #expect(ranges("🙂**b**", kind: .bold) == [NSRange(location: 4, length: 1)])
    }

    @Test func aHeadingStillStylesEmphasisInsideIt() {
        let spans = spans("# **Title**")
        #expect(spans.contains(Span(range: NSRange(location: 0, length: 2), kind: .headingMarker)))
        #expect(spans.contains(Span(range: NSRange(location: 2, length: 9), kind: .heading(1))))
        #expect(spans.contains(Span(range: NSRange(location: 2, length: 2), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 4, length: 5), kind: .bold)))
        #expect(spans.contains(Span(range: NSRange(location: 9, length: 2), kind: .marker)))
    }

    @Test func aQuoteStillStylesInlineCodeInsideIt() {
        let spans = spans("> `x`")
        #expect(spans.contains(Span(range: NSRange(location: 0, length: 2), kind: .marker)))
        #expect(spans.contains(Span(range: NSRange(location: 2, length: 3), kind: .quote)))
        #expect(spans.contains(Span(range: NSRange(location: 2, length: 3), kind: .inlineCode)))
    }

    @Test func boldWrapsInlineCodeWithoutStylingIt() {
        let spans = spans("**bold `code` bold**")
        #expect(ranges("**bold `code` bold**", kind: .inlineCode) == [NSRange(location: 7, length: 6)])
        #expect(ranges("**bold `code` bold**", kind: .bold) == [NSRange(location: 2, length: 5),
                                                              NSRange(location: 13, length: 5)])
        for span in spans where span.kind == .bold {
            #expect(NSIntersectionRange(span.range, NSRange(location: 7, length: 6)).length == 0)
        }
    }

    @Test func aClosingFenceMustMatchTheOpener() {
        let text = "````\n```\n# x\n````"
        #expect(ranges(text, kind: .fence) == [NSRange(location: 0, length: 4),
                                              NSRange(location: 13, length: 4)])
        #expect(ranges(text, kind: .codeBlock) == [NSRange(location: 5, length: 3),
                                                  NSRange(location: 9, length: 3)])
        #expect(ranges(text, kind: .headingMarker).isEmpty)
        #expect(ranges(text, kind: .heading(1)).isEmpty)
    }

    @Test func aTildeOrAnnotatedLineDoesNotCloseABacktickFence() {
        let text = "```\n~~~\n```tail\n```"
        #expect(ranges(text, kind: .fence) == [NSRange(location: 0, length: 3),
                                              NSRange(location: 16, length: 3)])
        #expect(ranges(text, kind: .codeBlock) == [NSRange(location: 4, length: 3),
                                                  NSRange(location: 8, length: 7)])
    }

    @Test func inlineCodeIsNotAComment() {
        let text = "a `<!-- x -->` b"
        #expect(ranges(text, kind: .comment).isEmpty)
        #expect(ranges(text, kind: .inlineCode) == [NSRange(location: 2, length: 12)])
    }

    @Test func codeInsideACommentStaysAComment() {
        let spans = spans("<!-- use `foo` -->")
        #expect(spans == [Span(range: NSRange(location: 0, length: 18), kind: .comment)])
    }

    @Test func aMultiLineCommentWithBackticksStaysAComment() {
        let text = "before\n<!-- start\n`code`\nend -->\nafter"
        let comment = NSRange(location: 7, length: 25)
        #expect(ranges(text, kind: .comment) == [comment])
        #expect(ranges(text, kind: .inlineCode).isEmpty)
    }

    @Test func nestedEmphasisKeepsBothTraitsWhenRendered() {
        // *a __b__ c*: a is italic only, b is bold AND italic.
        let styled = MarkdownStyleSpans.styled("*a __b__ c*", palette: .test)
        func traits(_ index: Int) -> NSFontDescriptor.SymbolicTraits {
            let font = styled.attribute(.font, at: index, effectiveRange: nil) as? NSFont
            return font?.fontDescriptor.symbolicTraits ?? []
        }
        #expect(traits(1).contains(.italic))
        #expect(!traits(1).contains(.bold))
        #expect(traits(5).isSuperset(of: [.bold, .italic]))
        #expect(traits(9).contains(.italic))
        #expect(!traits(9).contains(.bold))
    }

    /// The same with the app's own fonts: Geist has to ship an italic face
    /// for `NSFontManager` to find, or emphasis renders upright.
    @Test(arguments: [false, true])
    func nestedEmphasisKeepsBothTraitsInGeist(monospaced: Bool) {
        let body = AppFonts.nsFont(size: 13, monospaced: monospaced)
        let palette = MarkdownStylePalette.test.with(bodyFont: body)
        let styled = MarkdownStyleSpans.styled("*a __b__ c*", palette: palette)
        func font(_ index: Int) -> NSFont? { styled.attribute(.font, at: index, effectiveRange: nil) as? NSFont }
        #expect(font(1)?.familyName == body.familyName)
        #expect(font(1)?.fontDescriptor.symbolicTraits.contains(.italic) == true)
        #expect(font(5)?.fontDescriptor.symbolicTraits.isSuperset(of: [.bold, .italic]) == true)
    }

    /// Geist ligates `->` and `-->` into one arrow glyph, which drew a
    /// comment's closing marker as an arrow. Every character gets its
    /// own glyph in the editor.
    @Test func geistDrawsEveryCharacterOfACommentAndAnArrow() {
        let text = "<!-- note --> a -> b"
        let palette = MarkdownStylePalette.test.with(bodyFont: AppFonts.nsFont(size: 13))
        let styled = MarkdownStyleSpans.styled(text, palette: palette)
        let line = CTLineCreateWithAttributedString(styled)
        #expect(CTLineGetGlyphCount(line) == (text as NSString).length)
    }

    @Test func aLongDocumentStylesQuickly() {
        let text = String(repeating: "a **b** and *i* and ~~s~~\n", count: 5_000)
        let start = Date()
        _ = spans(text)
        // The bound catches a pathological slowdown, not latency: 5,000
        // lines take ~40ms, so three seconds still fails an accidental
        // quadratic while a starved CI runner cannot trip it.
        #expect(Date().timeIntervalSince(start) < 3)
    }
}

/// Plain fonts and colours for attribute tests: the traits are what
/// matters, not the looks.
private extension MarkdownStylePalette {
    static let test = MarkdownStylePalette(
        bodyFont: .systemFont(ofSize: 13),
        codeFont: .monospacedSystemFont(ofSize: 12, weight: .regular),
        headingFonts: [22, 18, 16, 14, 13, 13].map { .systemFont(ofSize: $0, weight: .semibold) },
        textPrimary: .black, textSecondary: .gray, textMuted: .gray,
        accent: .blue, codeBackground: .lightGray,
        lineSpacing: 4, headingSpacingBefore: 10, subheadingSpacingBefore: 6)

    func with(bodyFont: NSFont) -> MarkdownStylePalette {
        MarkdownStylePalette(
            bodyFont: bodyFont, codeFont: codeFont, headingFonts: headingFonts,
            textPrimary: textPrimary, textSecondary: textSecondary, textMuted: textMuted,
            accent: accent, codeBackground: codeBackground,
            lineSpacing: lineSpacing, headingSpacingBefore: headingSpacingBefore,
            subheadingSpacingBefore: subheadingSpacingBefore)
    }
}
