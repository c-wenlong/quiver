import Testing
@testable import QuiverCore

/// GitHub bodies are markdown with inline/block HTML — dependabot and
/// release bodies are mostly `<details>`, `<blockquote>` and `<table>`.
/// `GitHubMarkdown` turns them into rendered segments, not literal tags.
@Suite struct GitHubMarkdownTests {

    @Test func plainMarkdownPassesThrough() {
        let segs = GitHubMarkdown.segments("hello **world**\n\n- a\n- b")
        #expect(segs == [.markdown("hello **world**\n\n- a\n- b")])
    }

    @Test(.timeLimit(.minutes(1))) func aLessThanThatOpensNoTagIsText() {
        // A `<` with no tag after it used to stop the converter in place:
        // opening a PR whose body said "a < b" hung the app.
        #expect(GitHubMarkdown.segments("a < b") == [.markdown("a < b")])
        #expect(GitHubMarkdown.segments("x <= 3 and <3") == [.markdown("x <= 3 and <3")])
        #expect(GitHubMarkdown.segments("ends with <") == [.markdown("ends with <")])
        #expect(GitHubMarkdown.segments("<b>a</b> < <i>c</i>") == [.markdown("**a** < *c*")])
    }

    /// Entities decode once, left to right: `&amp;lt;` is the text
    /// `&lt;`, and one bad entity doesn't stop the rest decoding.
    @Test func entitiesDecodeOnceAndABadOneStaysLiteral() {
        let c = GitHubMarkdown.Converter("")
        #expect(c.decodeEntities("&amp;lt;b&amp;gt;") == "&lt;b&gt;")
        #expect(c.decodeEntities("&amp;#65;") == "&#65;")
        #expect(c.decodeEntities("&#38;lt;") == "&lt;")
        #expect(c.decodeEntities("&#99999999; then &#65;&#x42;&#X43;") == "&#99999999; then ABC")
        #expect(c.decodeEntities("&bogus; &lt; a & b &#x200b;x") == "&bogus; < a & b x")
        #expect(c.decodeEntities("&hellip;&mdash;&nbsp;&#39;") == "…— '")
        #expect(c.decodeEntities("&#00000000065;&#x0000000042;") == "AB")
        #expect(c.decodeEntities("&#; &#x; &amp &#65 x") == "&#; &#x; &amp &#65 x")
    }

    /// `href` matches only the attribute named `href`, not `data-href` or
    /// text inside another attribute's value.
    @Test func attrMatchesTheWholeName() {
        let c = GitHubMarkdown.Converter("")
        #expect(c.attr(#" data-href="/wrong" href="/right""#, "href") == "/right")
        #expect(c.attr(#" title="see href=/wrong" href='/right'"#, "href") == "/right")
        #expect(c.attr(#" HREF=/right target=_blank"#, "href") == "/right")
        #expect(c.attr(#" data-href="/wrong""#, "href") == nil)
        #expect(c.attr(#" disabled src="a.png"/"#, "src") == "a.png")
        #expect(c.attr(#" href="a&amp;lt;b""#, "href") == "a&lt;b")
        let segs = GitHubMarkdown.segments(#"<a data-href="/x" href="https://y.dev">go</a>"#)
        #expect(segs == [.markdown("[go](https://y.dev)")])
    }

    @Test func inlineHtmlBecomesMarkdown() {
        let segs = GitHubMarkdown.segments(
            "Fixes <a href=\"https://x.dev\">the thing</a> in <code>foo.swift</code>.")
        #expect(segs == [.markdown("Fixes [the thing](https://x.dev) in `foo.swift`.")])
    }

    @Test func emphasisAndLineBreaks() {
        let segs = GitHubMarkdown.segments(
            "<strong>bold</strong> <em>it</em><br/>next")
        #expect(segs == [.markdown("**bold** *it*\nnext")])
    }

    @Test func listsFlatten() {
        let segs = GitHubMarkdown.segments(
            "<ul><li>one</li><li>two <a href=\"https://x\">x</a></li></ul>")
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md == "- one\n- two [x](https://x)")
    }

    @Test func blockquoteCarriesPrefixAcrossLines() {
        let segs = GitHubMarkdown.segments(
            "before<blockquote><p>line1</p><p>line2</p></blockquote>after")
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md.contains("> line1"))
        #expect(md.contains("> line2"))
        #expect(md.hasPrefix("before"))
        #expect(md.hasSuffix("after"))
    }

    @Test func detailsBecomesSection() {
        let segs = GitHubMarkdown.segments(
            "intro\n<details>\n<summary>Release notes</summary>\n<p>body here</p>\n</details>\noutro")
        #expect(segs.count == 3)
        guard case .details(let title, let inner) = segs[1] else {
            Issue.record("expected details"); return
        }
        #expect(title == "Release notes")
        #expect(inner == [.markdown("body here")])
        guard case .markdown(let intro) = segs.first,
              case .markdown(let outro) = segs.last else {
            Issue.record("expected prose segments"); return
        }
        #expect(intro == "intro"); #expect(outro == "outro")
    }

    @Test func nestedDetailsRecurse() {
        let segs = GitHubMarkdown.segments(
            "<details><summary>outer</summary>o<details><summary>inner</summary>i</details></details>")
        guard case .details(_, let inner) = segs.first else {
            Issue.record("expected details"); return
        }
        #expect(inner.contains(.markdown("o")))
        #expect(inner.contains(.details(title: "inner",
                                        body: [.markdown("i")])))
    }

    @Test func unclosedDetailsStaysText() {
        let segs = GitHubMarkdown.segments("a <details> never ends")
        #expect(segs.count == 1)
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md.contains("never ends"))
    }

    @Test func dependabotTableBecomesBullets() {
        let segs = GitHubMarkdown.segments("""
            <table><thead><tr><th>Package</th><th>From</th><th>To</th></tr></thead>
            <tbody><tr><td>actions/checkout</td><td>4.4.0</td><td>7.0.1</td></tr>
            <tr><td>actions/setup-python</td><td>5.6.0</td><td>7.0.0</td></tr></tbody></table>
            """)
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md.contains("- **actions/checkout** → 4.4.0 → 7.0.1"))
        #expect(md.contains("- **actions/setup-python** → 5.6.0 → 7.0.0"))
        #expect(!md.contains("Package"))
    }

    @Test func commentsScriptsAndEntities() {
        let segs = GitHubMarkdown.segments(
            "a<!-- hidden -->b<script>x()</script>c&hellip;d&#8203;e")
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md == "abc…de")
    }

    @Test func headingsKeepLevel() {
        let segs = GitHubMarkdown.segments("<h2>What's Changed</h2><p>t</p>")
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md.contains("## What's Changed"))
    }

    @Test func pipeTableBecomesBullets() {
        // GFM tables aren't HTML — dependabot emits these straight.
        let segs = GitHubMarkdown.segments("""
            intro:

            | Package | From | To |
            | --- | --- | --- |
            | [actions/checkout](https://x) | `4.4.0` | `7.0.1` |
            | a/b | 1 | 2 |
            """)
        guard case .markdown(let md) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(md.contains("- **[actions/checkout](https://x)** → `4.4.0` → `7.0.1`"))
        #expect(md.contains("- **a/b** → 1 → 2"))
        #expect(!md.contains("Package"))
    }

    @Test func dependabotShape() {
        // The real dependabot skeleton: intro, table, "Updates…",
        // then three folded sections per package.
        let body = """
            Bumps the group with 1 update:
            <table><tbody><tr><td>a/b</td><td>1</td><td>2</td></tr></tbody></table>
            Updates <code>a/b</code> from 1 to 2
            <details><summary>Release notes</summary><blockquote><p>r</p></blockquote></details>
            <details><summary>Commits</summary><ul><li><code>abc1234</code> msg</li></ul></details>
            """
        let segs = GitHubMarkdown.segments(body)
        #expect(segs.count == 3)
        guard case .details(let t1, _) = segs[1],
              case .details(let t2, _) = segs[2] else {
            Issue.record("expected two details"); return
        }
        #expect(t1 == "Release notes"); #expect(t2 == "Commits")
        guard case .markdown(let prose) = segs.first else {
            Issue.record("expected markdown"); return
        }
        #expect(prose.contains("- **a/b** → 1 → 2"))
        #expect(prose.contains("Updates `a/b` from 1 to 2"))
    }

    // MARK: - blocks

    @Test func blocksSplitHeadingsAndParagraphs() {
        let blocks = GitHubMarkdown.blocks(
            "## Summary\n\ntext **bold**\n\n### Next\n\ntail")
        #expect(blocks == [
            .heading(2, "Summary"),
            .paragraph("text **bold**"),
            .heading(3, "Next"),
            .paragraph("tail"),
        ])
    }

    @Test func blocksKeepSoftBreaks() {
        // GitHub wraps bodies hard — one source line is a rendered line.
        let blocks = GitHubMarkdown.blocks("line one\nline two")
        #expect(blocks == [.paragraph("line one\nline two")])
    }

    @Test func blocksFenceKeepsCodeAndLang() {
        let blocks = GitHubMarkdown.blocks("```swift\nlet x = 1\n```\ndone")
        #expect(blocks == [
            .code("let x = 1", "swift"),
            .paragraph("done"),
        ])
    }

    @Test func blocksUnclosedFenceKeepsText() {
        let blocks = GitHubMarkdown.blocks("```\nnever closed\nstill in it")
        #expect(blocks == [.code("never closed\nstill in it", nil)])
    }

    @Test func blocksQuoteReparsesInner() {
        let blocks = GitHubMarkdown.blocks("> first\n> - a\n> - b\nafter")
        #expect(blocks == [
            .quote([.paragraph("first"), .list([
                .init(depth: 0, kind: .bullet, text: "a"),
                .init(depth: 0, kind: .bullet, text: "b"),
            ])]),
            .paragraph("after"),
        ])
    }

    @Test func blocksNestedQuoteNests() {
        let blocks = GitHubMarkdown.blocks("> outer\n>> inner")
        #expect(blocks == [
            .quote([.paragraph("outer"), .quote([.paragraph("inner")])]),
        ])
    }

    @Test func blocksTasksBulletsAndOrdered() {
        let blocks = GitHubMarkdown.blocks(
            "- [x] done\n- [ ] todo\n- plain\n1. one\n  - nested")
        guard case .list(let items) = blocks.first, blocks.count == 1 else {
            Issue.record("expected one list"); return
        }
        #expect(items == [
            .init(depth: 0, kind: .task(true), text: "done"),
            .init(depth: 0, kind: .task(false), text: "todo"),
            .init(depth: 0, kind: .bullet, text: "plain"),
            .init(depth: 0, kind: .ordered(1), text: "one"),
            .init(depth: 1, kind: .bullet, text: "nested"),
        ])
    }

    @Test func blocksRuleAndSetext() {
        #expect(GitHubMarkdown.blocks("a\n\n---") == [.paragraph("a"), .rule])
        // Under paragraph text a dash line is a setext h2, not a rule.
        #expect(GitHubMarkdown.blocks("Title\n---") == [.heading(2, "Title")])
    }

    @Test func blocksBlankLineEndsList() {
        let blocks = GitHubMarkdown.blocks("- a\n\n- b")
        #expect(blocks == [
            .list([.init(depth: 0, kind: .bullet, text: "a")]),
            .list([.init(depth: 0, kind: .bullet, text: "b")]),
        ])
    }

    /// Greptile's footer writes a badge as a link inside a link —
    /// `[[View in Greptile](badge.svg)](app-url)` — which Apple's
    /// inline parser mangles into literal brackets. `flatLinks` keeps
    /// the label and the outer href.
    @Test func flatLinksCollapsesNestedBadgeLinks() {
        #expect(GitHubMarkdown.flatLinks(
            "[Retrigger](https://app.greptile.com/r)[[View in Greptile](https://cdn/x.svg?v=2)](https://app.greptile.com/p)Confidence Score: 4/5")
            == "[Retrigger](https://app.greptile.com/r)[View in Greptile](https://app.greptile.com/p)Confidence Score: 4/5")
        // The `!`-form badge and a bare image flatten the same way.
        #expect(GitHubMarkdown.flatLinks("[![ci](img)](job) ![pic](shot.png)")
            == "[ci](job) [pic](shot.png)")
        // Ordinary links are left alone.
        #expect(GitHubMarkdown.flatLinks("see [the fix](https://x) now")
            == "see [the fix](https://x) now")
        // Inside backticks the brackets are an example, not a badge.
        #expect(GitHubMarkdown.flatLinks(
            "use `[[label](badge.svg)](url)` for it [![ci](i)](j)")
            == "use `[[label](badge.svg)](url)` for it [ci](j)")
    }
}
