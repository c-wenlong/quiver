import Foundation
import Testing
@testable import QuiverCore

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
}
