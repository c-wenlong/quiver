import SwiftUI
import XCTest
@testable import SWERemote

/// The rows and controls that rearrange above the default text size:
/// unchanged at the default, wrapping or stacking above it.
@MainActor
final class LargeTextLayoutTests: XCTestCase {
    func testFlowLayoutWrapsWhatDoesNotFit() {
        // Five 50pt boxes in 120pt: two a line, three lines of 10pt
        // with 4pt between.
        let flow = FlowLayout(spacing: 6, lineSpacing: 4) {
            ForEach(0..<5, id: \.self) { _ in Color.black.frame(width: 50, height: 10) }
        }
        XCTAssertEqual(size(flow.frame(width: 120, alignment: .leading)).height, 38)
        // All five fit one line of 274pt.
        XCTAssertEqual(size(flow.fixedSize()), CGSize(width: 274, height: 10))
    }

    func testFlowLayoutGivesAnOverlongViewTheLine() {
        let sentence = Text("A sentence far too long to fit on one line of this width").font(Theme.caption)
        let flow = FlowLayout(spacing: 6, lineSpacing: 6) {
            Color.black.frame(width: 30, height: 10)
            sentence
        }
        // The sentence gets a line of its own, 100pt wide, and wraps in it.
        let wrapped = size(sentence.frame(width: 100, alignment: .leading).fixedSize(horizontal: false, vertical: true))
        XCTAssertGreaterThan(wrapped.height, size(sentence.fixedSize()).height)
        XCTAssertEqual(size(flow.frame(width: 100, alignment: .leading).fixedSize(horizontal: false, vertical: true)),
                       CGSize(width: 100, height: 10 + 6 + wrapped.height))
    }

    func testRowsAreUnchangedAtTheDefaultSizeAndWrapAbove() {
        let wrapping = WrappingHStack(spacing: 6) { details }.lineLimit(1).frame(width: 140, alignment: .leading)
        let plain = HStack(spacing: 6) { details }.lineLimit(1).frame(width: 140, alignment: .leading)
        XCTAssertEqual(png(wrapping, .large), png(plain, .large))
        XCTAssertGreaterThan(size(wrapping.dynamicTypeSize(.xxxLarge)).height,
                             size(plain.dynamicTypeSize(.xxxLarge)).height)
    }

    func testTitleLinesGrowOnlyAboveTheDefaultSize() {
        let title = "Fix the flaky checkout test that times out on CI when the mock is slow"
        let growing = Text(title).font(Theme.rowTitle).lineLimit(1, larger: 3).frame(width: 200)
        let one = Text(title).font(Theme.rowTitle).lineLimit(1).frame(width: 200)
        XCTAssertEqual(png(growing, .large), png(one, .large))
        XCTAssertGreaterThan(size(growing.dynamicTypeSize(.xxLarge)).height,
                             size(one.dynamicTypeSize(.xxLarge)).height)
    }

    func testStackingRowStacksOnlyAtAccessibilitySizes() {
        let row = StackingRow { Color.black.frame(width: 50, height: 10); Color.black.frame(width: 50, height: 10) }
        XCTAssertEqual(size(row.fixedSize().dynamicTypeSize(.xxxLarge)), CGSize(width: 108, height: 10))
        XCTAssertEqual(size(row.fixedSize().dynamicTypeSize(.accessibility1)), CGSize(width: 50, height: 28))
    }

    func testAStaleNoticeGrowsPastTwoLinesAtLargerSizes() {
        let error = "Can't reach the hub. Check that Tailscale is on, then pull to refresh the list again."
        let notice = StaleNotice(error: error).frame(width: 200)
        let twoLines = Text("A\nB").font(Theme.caption).lineLimit(2).frame(width: 200)
        // Capped at two lines, the advice at the end was what got cut.
        XCTAssertGreaterThan(size(notice.dynamicTypeSize(.accessibility1)).height,
                             size(twoLines.dynamicTypeSize(.accessibility1)).height)
    }

    @ViewBuilder private var details: some View {
        Text("#1214")
        Text("· c-wenlong")
        Text("· 2 comments")
        Text("· 6h")
    }

    private func size<V: View>(_ view: V) -> CGSize {
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        return renderer.uiImage?.size ?? .zero
    }

    private func png<V: View>(_ view: V, _ type: DynamicTypeSize) -> Data? {
        let renderer = ImageRenderer(content: view.font(Theme.caption).background(.white).dynamicTypeSize(type))
        renderer.scale = 2
        return renderer.uiImage?.pngData()
    }
}
