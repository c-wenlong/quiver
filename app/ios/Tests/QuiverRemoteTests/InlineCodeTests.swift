import XCTest
@testable import QuiverRemote

final class InlineCodeTests: XCTestCase {
    /// The text of each run that renders as code.
    private func codeSpans(_ s: AttributedString) -> [String] {
        s.runs.filter { $0.inlinePresentationIntent == .code }.map { String(s[$0.range].characters) }
    }

    func testBacktickSpanBecomesCodeWithoutItsBackticks() {
        let s = Markdown.inlineCode("gh is not authenticated: run `gh auth login`")
        XCTAssertEqual(String(s.characters), "gh is not authenticated: run gh auth login")
        XCTAssertEqual(codeSpans(s), ["gh auth login"])
    }

    func testSeveralSpansAndPlainTextAround() {
        let s = Markdown.inlineCode("Pin repositories with `swe gh repo add`, then `swe tasks sync`.")
        XCTAssertEqual(String(s.characters), "Pin repositories with swe gh repo add, then swe tasks sync.")
        XCTAssertEqual(codeSpans(s), ["swe gh repo add", "swe tasks sync"])
    }

    func testOtherMarkdownStaysAsWritten() {
        let text = "Can't read ~/my_repo_name/*.json: see [docs](https://x.y) **now**"
        let s = Markdown.inlineCode(text)
        XCTAssertEqual(String(s.characters), text)
        XCTAssertTrue(codeSpans(s).isEmpty)
        XCTAssertTrue(s.runs.allSatisfy { $0.link == nil && $0.inlinePresentationIntent == nil })
    }

    func testUnmatchedBacktickShowsAsTyped() {
        let s = Markdown.inlineCode("it's a `half span")
        XCTAssertEqual(String(s.characters), "it's a `half span")
        XCTAssertTrue(codeSpans(s).isEmpty)
    }

    func testDoubleBackticksWrapASingleOne() {
        let s = Markdown.inlineCode("type `` ` `` to quote")
        XCTAssertEqual(String(s.characters), "type ` to quote")
        XCTAssertEqual(codeSpans(s), ["`"])
    }
}
