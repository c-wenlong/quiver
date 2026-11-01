import Foundation
import Testing
@testable import SWEApp

/// Markdown from PRs, issues and transcripts renders clickable links only
/// for web URLs; anything else stays as its text.
@Suite struct MarkdownLinksTests {
    private func links(_ md: String) -> [URL] {
        MarkdownBody.inline(md).runs.compactMap(\.link)
    }

    @Test func webLinksStayClickable() {
        #expect(links("see [docs](https://example.com/a) and [b](http://x.dev)")
                == [URL(string: "https://example.com/a")!, URL(string: "http://x.dev")!])
    }

    @Test func otherSchemesLoseTheLinkButKeepTheText() {
        let md = "[run](file:///Applications/Calculator.app) [x](vscode://open?f=/etc) [y](javascript:alert(1))"
        let text = MarkdownBody.inline(md)
        #expect(text.runs.allSatisfy { $0.link == nil })
        #expect(String(text.characters) == "run x y")
    }
}
