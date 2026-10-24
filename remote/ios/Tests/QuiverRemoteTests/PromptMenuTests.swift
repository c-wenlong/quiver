import XCTest
@testable import QuiverRemote

final class MentionTokenTests: XCTestCase {
    func testAtStartOfText() {
        XCTAssertEqual(Mention.token(in: "@", cursor: nil), MentionToken(start: 0, end: 1, query: ""))
        XCTAssertEqual(Mention.token(in: "@src/ma", cursor: nil), MentionToken(start: 0, end: 7, query: "src/ma"))
    }

    func testAfterWhitespaceOrNewline() {
        XCTAssertEqual(Mention.token(in: "look at @rea", cursor: nil)?.query, "rea")
        XCTAssertEqual(Mention.token(in: "look at @rea", cursor: nil)?.start, 8)
        XCTAssertEqual(Mention.token(in: "first line\n@notes", cursor: nil)?.query, "notes")
    }

    func testMidTextCursorReadsUpToTheCaretAndReplacesTheWholeWord() {
        // "see @foo and bar", caret after "@fo".
        let token = Mention.token(in: "see @foo and bar", cursor: 7)
        XCTAssertEqual(token, MentionToken(start: 4, end: 8, query: "fo"))
    }

    func testTheTokenAtTheCaretWinsAmongSeveral() {
        XCTAssertEqual(Mention.token(in: "@one @two", cursor: nil)?.query, "two")
        XCTAssertEqual(Mention.token(in: "@one @two", cursor: 3)?.query, "on")
        XCTAssertEqual(Mention.token(in: "@one @two", cursor: 3)?.end, 4)
    }

    func testEmailsAndMidWordAtDoNotTrigger() {
        XCTAssertNil(Mention.token(in: "mail a@b.com", cursor: nil))
        XCTAssertNil(Mention.token(in: "mail a@", cursor: nil))
        XCTAssertNil(Mention.token(in: "a@b.com", cursor: 2))
    }

    func testSpaceAfterTheTokenOrNoAtCloses() {
        XCTAssertNil(Mention.token(in: "@src ", cursor: nil))
        XCTAssertNil(Mention.token(in: "plain text", cursor: nil))
        XCTAssertNil(Mention.token(in: "", cursor: nil))
        // Caret before the @ itself.
        XCTAssertNil(Mention.token(in: "@src", cursor: 0))
    }

    func testOutOfRangeCursorIsClamped() {
        XCTAssertEqual(Mention.token(in: "@ab", cursor: 99)?.query, "ab")
        XCTAssertNil(Mention.token(in: "@ab", cursor: -4))
    }
}

final class MentionInsertTests: XCTestCase {
    private func insert(_ text: String, cursor: Int?, _ path: String, dir: Bool = false) -> (String, Int)? {
        guard let token = Mention.token(in: text, cursor: cursor) else { return nil }
        let r = Mention.insert(path, isDir: dir, replacing: token, in: text)
        return (r.text, r.cursor)
    }

    func testFileAtEndGetsATrailingSpace() {
        let r = insert("@sr", cursor: nil, "src/main.py")
        XCTAssertEqual(r?.0, "@src/main.py ")
        XCTAssertEqual(r?.1, 13)
    }

    func testFileMidTextReusesTheSpaceAfterIt() {
        // Caret after "@fo" in "see @foo and bar": the whole word goes.
        let r = insert("see @foo and bar", cursor: 7, "lib/foo.py")
        XCTAssertEqual(r?.0, "see @lib/foo.py and bar")
        XCTAssertEqual(r?.1, 16) // after the existing space
    }

    func testFolderKeepsTheMenuOpen() {
        let r = insert("@sr", cursor: nil, "src", dir: true)
        XCTAssertEqual(r?.0, "@src/")
        XCTAssertEqual(r?.1, 5)
        XCTAssertEqual(Mention.token(in: r!.0, cursor: r!.1)?.query, "src/")
        // A trailing slash from the hub isn't doubled.
        XCTAssertEqual(insert("@sr", cursor: nil, "src/", dir: true)?.0, "@src/")
    }

    func testFolderMidText() {
        let r = insert("open @sr please", cursor: 8, "src", dir: true)
        XCTAssertEqual(r?.0, "open @src/ please")
        XCTAssertEqual(r?.1, 10)
    }

    func testSplitNameAndParent() {
        XCTAssertEqual(Mention.split("src/app/main.py").name, "main.py")
        XCTAssertEqual(Mention.split("src/app/main.py").parent, "src/app")
        XCTAssertEqual(Mention.split("README.md").parent, "")
        XCTAssertEqual(Mention.split("docs/guides/").name, "guides")
    }
}

/// Lets a test hold async work until it says go.
@MainActor
private final class Gate {
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private(set) var open = false

    func wait() async {
        if open { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        open = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}

@MainActor
final class MentionSearchTests: XCTestCase {
    private func token(_ q: String) -> MentionToken { MentionToken(start: 0, end: q.count + 1, query: q) }
    private func entries(_ paths: String...) -> FilesResponse { FilesResponse(entries: paths.map { FileEntry(path: $0) }) }

    func testDebounceSendsOnlyTheLastQuery() async {
        let search = MentionSearch()
        let gate = Gate()
        var asked: [String] = []
        search.pause = { _ in await gate.wait() }
        search.fetch = { q in asked.append(q); return FilesResponse(entries: [FileEntry(path: q + ".txt")]) }
        let first = search.track(token("a"))
        let second = search.track(token("ab"))
        gate.release()
        await first?.value
        await second?.value
        XCTAssertEqual(asked, ["ab"])
        XCTAssertEqual(search.entries.map(\.path), ["ab.txt"])
        XCTAssertEqual(search.answered, "ab")
        XCTAssertFalse(search.loading)
    }

    func testAStaleAnswerIsDropped() async {
        let search = MentionSearch()
        search.pause = { _ in }
        let slow = Gate()
        search.fetch = { q in
            if q == "a" { await slow.wait(); return FilesResponse(entries: [FileEntry(path: "old.txt")]) }
            return FilesResponse(entries: [FileEntry(path: "new.txt")])
        }
        let first = search.track(token("a"))
        // Let the first request reach the hub before the next keystroke.
        await Task.yield(); await Task.yield()
        let second = search.track(token("ab"))
        await second?.value
        slow.release()
        await first?.value
        XCTAssertEqual(search.entries.map(\.path), ["new.txt"])
        XCTAssertEqual(search.answered, "ab")
    }

    func testLastGoodResultsStayWhileTheNextQueryLoads() async {
        let search = MentionSearch()
        let gate = Gate()
        search.fetch = { q in FilesResponse(entries: [FileEntry(path: q + ".md")]) }
        search.pause = { _ in }
        await search.track(token("re"))?.value
        XCTAssertEqual(search.entries.map(\.path), ["re.md"])
        search.pause = { _ in await gate.wait() }
        let next = search.track(token("rea"))
        XCTAssertTrue(search.loading)
        XCTAssertEqual(search.entries.map(\.path), ["re.md"])
        gate.release()
        await next?.value
        XCTAssertEqual(search.entries.map(\.path), ["rea.md"])
    }

    func testSameQueryDoesNotSearchAgain() async {
        let search = MentionSearch()
        var count = 0
        search.pause = { _ in }
        search.fetch = { _ in count += 1; return FilesResponse(entries: []) }
        await search.track(token("x"))?.value
        // The caret moved but the token reads the same.
        XCTAssertNil(search.track(MentionToken(start: 0, end: 4, query: "x")))
        XCTAssertEqual(count, 1)
    }

    func testTokenGoneClearsAndCancels() async {
        let search = MentionSearch()
        let gate = Gate()
        var asked: [String] = []
        search.pause = { _ in await gate.wait() }
        search.fetch = { q in asked.append(q); return FilesResponse(entries: []) }
        let t = search.track(token("a"))
        search.track(nil)
        gate.release()
        await t?.value
        XCTAssertEqual(asked, [])
        XCTAssertFalse(search.isOpen)
        XCTAssertFalse(search.loading)
    }

    func testDismissHoldsUntilTheTokenChanges() async {
        let search = MentionSearch()
        search.pause = { _ in }
        search.fetch = { _ in FilesResponse(entries: [FileEntry(path: "a.txt")]) }
        await search.track(token("a"))?.value
        search.dismiss()
        XCTAssertFalse(search.isOpen)
        XCTAssertNil(search.track(token("a")))
        XCTAssertFalse(search.isOpen)
        await search.track(token("ab"))?.value
        XCTAssertTrue(search.isOpen)
        XCTAssertEqual(search.entries.map(\.path), ["a.txt"])
    }

    func testErrorsBecomeOneLine() async {
        let search = MentionSearch()
        search.pause = { _ in }
        search.machine = { "studio" }
        search.fetch = { _ in throw HubError(status: 422, code: "files_unavailable", message: "x") }
        await search.track(token("a"))?.value
        XCTAssertEqual(search.error, "File search isn't available for sessions on studio")
        XCTAssertEqual(search.entries, [])
        XCTAssertEqual(MentionSearch.message(for: HubError(status: 422, code: "cwd_missing", message: ""), machine: "m"),
                       "This session's folder is gone")
        XCTAssertEqual(MentionSearch.message(for: HubError(status: 404, code: "http_404", message: ""), machine: "m"),
                       "Update the hub to search files from the phone")
        XCTAssertEqual(MentionSearch.message(for: HubError(status: 403, code: "not_readable", message: ""), machine: "m"),
                       "The hub can't read this session's folder")
    }

    func testDecodesTheFilesRoute() throws {
        let json = #"{"cwd":"/w/demo","query":"ma","source":"git","truncated":false,"entries":[{"path":"src/main.py","kind":"file","mtime":1.5},{"path":"src","kind":"dir","mtime":2}]}"#
        let body = try Hub.decoder.decode(FilesResponse.self, from: Data(json.utf8))
        XCTAssertEqual(body.entries.map(\.path), ["src/main.py", "src"])
        XCTAssertEqual(body.entries.map(\.isDir), [false, true])
        XCTAssertEqual(body.source, "git")
    }
}
