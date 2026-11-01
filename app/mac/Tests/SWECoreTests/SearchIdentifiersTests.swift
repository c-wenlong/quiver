import Foundation
import Testing
@testable import SWECore

/// `identifierShadowText` splits searchable identifiers into their pieces —
/// Orca's shadow-terms port.
@Suite struct SearchIdentifiersTests {
    @Test func camelPathScreamingAndDottedIdentifiers() {
        #expect(identifierShadowText("jsonlFiles") == "jsonlfiles jsonl files")
        #expect(identifierShadowText("~/.claude/projects/foo") == "claude projects foo")
        #expect(identifierShadowText("MAX_RETRY") == "max retry")
        #expect(identifierShadowText("Ledger.swift") == "ledger swift")
        // Whole + camel pieces — Orca's own example.
        #expect(identifierShadowText("resolveTerminalPath") == "resolveterminalpath resolve terminal path")
        // `JSONParser` has no separator and no lower→upper boundary, so it
        // is not "interesting" and shadows nothing.
        #expect(identifierShadowText("JSONParser") == "")
    }

    @Test func uninterestingTokensAreDropped() {
        #expect(identifierShadowText("plainword") == "")
        #expect(identifierShadowText("a.b.c") == "")  // pieces under 2 chars
        #expect(identifierShadowText("") == "")
        // Not interesting on its own, but "FOO" is SCREAMING — dedup folds it.
        #expect(identifierShadowText("Foo foo FOO") == "foo")
    }

    @Test func mixedTextShadowsOnlyIdentifiers() {
        let text = "fix jsonlFiles and MAX_RETRY today"
        #expect(identifierShadowText(text) == "jsonlfiles jsonl files max retry")
    }

    @Test func acronymBoundarySplitsLikeOrcas() {
        // `xABCdy` → pieces x + AB + Cdy (single chars under 2 are dropped).
        #expect(identifierShadowText("xABCdy") == "xabcdy ab cdy")
        // `a1B2` → pieces a1 + B + 2 (again only ≥2 chars survive).
        #expect(identifierShadowText("a1B2") == "a1b2 a1")
    }

    @Test func termsAreCapped() {
        let text = (0..<5000).map { "ident_\($0)" }.joined(separator: " ")
        let terms = identifierShadowText(text, limit: 4000).split(separator: " ")
        #expect(terms.count == 4000)
        // Each `ident_N` yields ident + N, so the cap lands mid-token too.
    }
}
