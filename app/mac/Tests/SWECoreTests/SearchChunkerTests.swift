import Foundation
import Testing
@testable import SWECore

/// `SearchChunker.rows` turns decoded messages into FTS rows: tool messages
/// head-capped to one 3,072-unit row, user/assistant split at ≤8,000 on a
/// token boundary in the window's second half.
@Suite struct SearchChunkerTests {
    func user(_ text: String) -> SearchMessage { SearchMessage(role: .user, text: text) }
    func assistant(_ text: String) -> SearchMessage { SearchMessage(role: .assistant, text: text) }
    func tool(_ text: String) -> SearchMessage { SearchMessage(role: .tool, text: text) }

    @Test func shortAndEmptyMessages() {
        #expect(SearchChunker.rows(from: [user("hi")]) == [user("hi")])
        #expect(SearchChunker.rows(from: [user(""), user("   \n\t  ")]).isEmpty)
        // Exactly the limit is one row.
        let exact = String(repeating: "a", count: SearchChunker.chunkLimit)
        let rows = SearchChunker.rows(from: [user(exact)])
        #expect(rows == [user(exact)])
    }

    @Test func toolMessagesBecomeOneHeadCappedRow() {
        let big = String(repeating: "x", count: 10_000)
        let rows = SearchChunker.rows(from: [tool(big)])
        #expect(rows.count == 1)
        #expect(rows[0].text.utf16.count == SearchChunker.toolRowLimit)
        #expect(rows[0].text == String(repeating: "x", count: SearchChunker.toolRowLimit))
        // Even a multi-thousand-line output is one row.
        let lines = (0..<500).map { "line \($0)" }.joined(separator: "\n")
        #expect(SearchChunker.rows(from: [tool(lines)]).count == 1)
    }

    @Test func oversizedTextCutsAtTheLastBoundaryInTheSecondHalf() {
        // 'a'*7900 + ' ' + 'b'*100 = 8,001 units; the space at 7,900 is the
        // only boundary, so the first row ends after it.
        let text = String(repeating: "a", count: 7900) + " " + String(repeating: "b", count: 100)
        let rows = SearchChunker.rows(from: [user(text)])
        #expect(rows.count == 2)
        #expect(rows[0].text.utf16.count == 7901)
        #expect(rows[0].text.hasSuffix(" "))
        #expect(rows[1].text == String(repeating: "b", count: 100))
        #expect(rows[0].text + rows[1].text == text)
    }

    @Test func noBoundaryInTheSecondHalfCutsAtTheLimit() {
        // A boundary in the first half does not count.
        let text = String(repeating: "a", count: 3000) + " " + String(repeating: "b", count: 5001)
        let rows = SearchChunker.rows(from: [assistant(text)])
        #expect(rows.count == 2)
        #expect(rows[0].text.utf16.count == SearchChunker.chunkLimit)
        #expect(rows[1].text.utf16.count == 2)
    }

    @Test func everyChunkStaysInsideTheLimitAcrossManyRows() {
        let words = (0..<3000).map { "word\($0)" }.joined(separator: " ")
        let rows = SearchChunker.rows(from: [user(words)])
        #expect(rows.count >= 4)
        #expect(rows.allSatisfy { $0.text.utf16.count <= SearchChunker.chunkLimit })
        #expect(rows.allSatisfy { $0.role == .user })
        #expect(rows.map(\.text).joined() == words)
    }

    @Test func cutsNeverTearASurrogatePairOrCluster() {
        // '😀' straddles the window edge at units 7,998–8,000 and is itself a
        // boundary, so the row ends with it whole.
        let text = String(repeating: "a", count: 7998) + "😀" + "xxx"
        let rows = SearchChunker.rows(from: [user(text)])
        #expect(rows.count == 2)
        #expect(rows[0].text.hasSuffix("😀"))
        #expect(rows[0].text.utf16.count == SearchChunker.chunkLimit)
        #expect(rows[1].text == "xxx")

        // A flag cluster (two regional indicators, four UTF-16 units) that
        // starts mid-window is never split in half either.
        let flag = String(repeating: "b", count: 7997) + "🇫🇷" + "tail"
        let flagRows = SearchChunker.rows(from: [user(flag)])
        #expect(flagRows.map(\.text).joined() == flag)
        #expect(flagRows.allSatisfy { !$0.text.isEmpty })
        for row in flagRows {
            // No row may end inside the flag's regional indicators.
            #expect(!row.text.hasSuffix("\u{1F1EB}"))
        }
    }
}
