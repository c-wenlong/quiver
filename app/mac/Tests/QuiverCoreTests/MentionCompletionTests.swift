import Foundation
import Testing
@testable import QuiverCore

/// The `@` menu's token, pick and search logic — the iPhone app's
/// `PromptMenuTests` mention cases.
@Suite struct MentionTokenTests {
    @Test func atTheStartOfTheText() {
        #expect(Mention.token(in: "@", cursor: nil) == MentionToken(start: 0, end: 1, query: ""))
        #expect(Mention.token(in: "@src/ma", cursor: nil) == MentionToken(start: 0, end: 7, query: "src/ma"))
    }

    @Test func afterWhitespaceOrANewline() {
        #expect(Mention.token(in: "look at @rea", cursor: nil) == MentionToken(start: 8, end: 12, query: "rea"))
        #expect(Mention.token(in: "first line\n@notes", cursor: nil)?.query == "notes")
    }

    @Test func midTextCaretReadsUpToItAndTheWholeWordIsReplaced() {
        // "see @foo and bar", caret after "@fo".
        #expect(Mention.token(in: "see @foo and bar", cursor: 7) == MentionToken(start: 4, end: 8, query: "fo"))
    }

    @Test func theTokenAtTheCaretWinsAmongSeveral() {
        #expect(Mention.token(in: "@one @two", cursor: nil)?.query == "two")
        #expect(Mention.token(in: "@one @two", cursor: 3) == MentionToken(start: 0, end: 4, query: "on"))
    }

    @Test func emailsAndAMidWordAtNeverOpenIt() {
        #expect(Mention.token(in: "mail a@b.com", cursor: nil) == nil)
        #expect(Mention.token(in: "mail a@", cursor: nil) == nil)
        #expect(Mention.token(in: "a@b.com", cursor: 2) == nil)
    }

    @Test func aSpaceAfterTheTokenOrNoAtClosesIt() {
        #expect(Mention.token(in: "@src ", cursor: nil) == nil)
        #expect(Mention.token(in: "plain text", cursor: nil) == nil)
        #expect(Mention.token(in: "", cursor: nil) == nil)
        // Before the @: not in the token.
        #expect(Mention.token(in: "@src", cursor: 0) == nil)
    }

    @Test func anOutOfRangeCaretIsClamped() {
        #expect(Mention.token(in: "@ab", cursor: 99)?.query == "ab")
        #expect(Mention.token(in: "@ab", cursor: -4) == nil)
    }

    @Test func aQuotedPathRunsPastItsSpaces() {
        // Open quote while a folder with a space is browsed.
        #expect(Mention.token(in: "@\"My Documents/no", cursor: nil)
            == MentionToken(start: 0, end: 17, query: "My Documents/no"))
        // A closed quote: the token runs to and over it.
        #expect(Mention.token(in: "see @\"a b.txt\" now", cursor: 9)
            == MentionToken(start: 4, end: 14, query: "a b"))
        // A quote mid-word doesn't start one.
        #expect(Mention.token(in: "x@\"a b", cursor: nil) == nil)
    }
}

@Suite struct MentionInsertTests {
    private func insert(_ text: String, cursor: Int?, _ path: String, dir: Bool = false) -> (text: String, cursor: Int)? {
        guard let token = Mention.token(in: text, cursor: cursor) else { return nil }
        return Mention.insert(path, isDir: dir, replacing: token, in: text)
    }

    @Test func aFileAtTheEndGetsATrailingSpace() {
        let r = insert("@sr", cursor: nil, "src/main.py")
        #expect(r?.text == "@src/main.py ")
        #expect(r?.cursor == 13)
        // The pick closes the menu.
        #expect(Mention.token(in: r!.text, cursor: r!.cursor) == nil)
    }

    @Test func aFileMidTextReusesTheSpaceAfterIt() {
        let r = insert("see @foo and bar", cursor: 7, "lib/foo.py")
        #expect(r?.text == "see @lib/foo.py and bar")
        #expect(r?.cursor == 16)
    }

    @Test func aFolderKeepsTheMenuOpen() {
        let r = insert("@sr", cursor: nil, "src", dir: true)
        #expect(r?.text == "@src/")
        #expect(r?.cursor == 5)
        #expect(Mention.token(in: r!.text, cursor: r!.cursor)?.query == "src/")
        // A trailing slash from the hub isn't doubled.
        #expect(insert("@sr", cursor: nil, "src/", dir: true)?.text == "@src/")
        let mid = insert("open @sr please", cursor: 8, "src", dir: true)
        #expect(mid?.text == "open @src/ please")
        #expect(mid?.cursor == 10)
    }

    @Test func aPathWithSpacesIsQuoted() {
        let file = insert("@my", cursor: nil, "My Documents/a b.txt")
        #expect(file?.text == "@\"My Documents/a b.txt\" ")
        #expect(file?.cursor == file?.text.count)
        // A folder's quote stays open, and the menu reads inside it.
        let dir = insert("@my", cursor: nil, "My Documents", dir: true)
        #expect(dir?.text == "@\"My Documents/")
        #expect(Mention.token(in: dir!.text, cursor: dir!.cursor)?.query == "My Documents/")
        // Then a file inside it closes the quote.
        let inside = insert(dir!.text, cursor: dir!.cursor, "My Documents/notes.txt")
        #expect(inside?.text == "@\"My Documents/notes.txt\" ")
    }

    @Test func aFolderPickBeforeAQuotedWordKeepsTheWordsAfterIt() {
        // Caret after "@my", then a folder with a space, then a file in it:
        // the quote before "hello" opens a word of its own.
        let text = "open @my and say \"hello\""
        let dir = insert(text, cursor: 8, "My Documents", dir: true)
        #expect(dir?.text == "open @\"My Documents/ and say \"hello\"")
        #expect(Mention.token(in: dir!.text, cursor: dir!.cursor)
            == MentionToken(start: 5, end: dir!.cursor, query: "My Documents/"))
        let file = insert(dir!.text, cursor: dir!.cursor, "My Documents/note.txt")
        #expect(file?.text == "open @\"My Documents/note.txt\" and say \"hello\"")
        #expect(file?.cursor == 30)
    }

    @Test func splitsTheNameFromItsFolder() {
        #expect(Mention.split("src/app/main.py") == (name: "main.py", parent: "src/app"))
        #expect(Mention.split("README.md").parent == "")
        #expect(Mention.split("docs/guides/").name == "guides")
    }
}

@Suite @MainActor struct MentionSearchTests {
    private func token(_ q: String) -> MentionToken { MentionToken(start: 0, end: q.count + 1, query: q) }

    @Test func theDebounceSendsOnlyTheLastQuery() async {
        let search = MentionSearch()
        let gate = HoldGate()
        var asked: [String] = []
        search.pause = { _ in try? await gate.pass() }
        search.fetch = { q in asked.append(q); return FilesResponse(entries: [FileEntry(path: q + ".txt")]) }
        let first = search.track(token("a"))
        let second = search.track(token("ab"))
        #expect(search.firstLoad)
        gate.open()
        await first?.value
        await second?.value
        #expect(asked == ["ab"])
        #expect(search.entries.map(\.path) == ["ab.txt"])
        #expect(search.answered == "ab")
        #expect(!search.loading)
        #expect(!search.firstLoad)
    }

    @Test func aStaleAnswerIsDropped() async {
        let search = MentionSearch()
        search.pause = { _ in }
        let slow = HoldGate()
        search.fetch = { q in
            if q == "a" {
                try? await slow.pass()
                return FilesResponse(entries: [FileEntry(path: "old.txt")])
            }
            return FilesResponse(entries: [FileEntry(path: "new.txt")])
        }
        let first = search.track(token("a"))
        // The first request reaches the hub before the next keystroke.
        #expect(await slow.arrived())
        await search.track(token("ab"))?.value
        slow.open()
        await first?.value
        #expect(search.entries.map(\.path) == ["new.txt"])
        #expect(search.answered == "ab")
    }

    @Test func theLastResultsStayWhileTheNextQueryLoads() async {
        let search = MentionSearch()
        search.pause = { _ in }
        search.fetch = { q in FilesResponse(entries: [FileEntry(path: q + ".md")]) }
        await search.track(token("re"))?.value
        let gate = HoldGate()
        search.pause = { _ in try? await gate.pass() }
        let next = search.track(token("rea"))
        #expect(search.loading)
        // An answer is already up, so Return picks rather than waits.
        #expect(!search.firstLoad)
        #expect(search.entries.map(\.path) == ["re.md"])
        gate.open()
        await next?.value
        #expect(search.entries.map(\.path) == ["rea.md"])
    }

    @Test func theSameQueryDoesNotSearchAgain() async {
        let search = MentionSearch()
        var count = 0
        search.pause = { _ in }
        search.fetch = { _ in count += 1; return FilesResponse(entries: []) }
        await search.track(token("x"))?.value
        // The caret moved but the token reads the same.
        #expect(search.track(MentionToken(start: 0, end: 4, query: "x")) == nil)
        #expect(count == 1)
    }

    @Test func theTokenGoingClearsAndCancels() async {
        let search = MentionSearch()
        let gate = HoldGate()
        var asked: [String] = []
        search.pause = { _ in try? await gate.pass() }
        search.fetch = { q in asked.append(q); return FilesResponse(entries: []) }
        let t = search.track(token("a"))
        search.track(nil)
        gate.open()
        await t?.value
        #expect(asked.isEmpty)
        #expect(!search.isOpen)
        #expect(!search.loading)
    }

    @Test func dismissHoldsUntilTheTokenChanges() async {
        let search = MentionSearch()
        search.pause = { _ in }
        search.fetch = { _ in FilesResponse(entries: [FileEntry(path: "a.txt")]) }
        await search.track(token("a"))?.value
        search.dismiss()
        #expect(!search.isOpen)
        #expect(search.track(token("a")) == nil)
        #expect(!search.isOpen)
        await search.track(token("ab"))?.value
        #expect(search.isOpen)
        #expect(search.entries.map(\.path) == ["a.txt"])
    }

    @Test func resumeSearchesAgainForAnOpenMenu() async {
        let search = MentionSearch()
        var count = 0
        search.pause = { _ in }
        search.fetch = { _ in count += 1; return FilesResponse(entries: [FileEntry(path: "a.txt")]) }
        await search.track(token("a"))?.value
        // stop(): the chat closed with the menu open.
        search.reset()
        #expect(search.isOpen)
        await search.resume()?.value
        #expect(count == 2)
        #expect(search.entries.map(\.path) == ["a.txt"])
        #expect(search.resume() == nil)
    }

    @Test func failuresBecomeOneLine() async {
        let search = MentionSearch()
        search.pause = { _ in }
        search.machine = { "studio" }
        search.fetch = { _ in throw HubError(status: 422, code: "files_unavailable", message: "x") }
        await search.track(token("a"))?.value
        #expect(search.error == "File search isn't available for sessions on studio")
        #expect(search.entries.isEmpty)
        #expect(search.answered == "a")
        let message = { (e: HubError) in MentionSearch.message(for: e, machine: "m") }
        #expect(message(HubError(status: 422, code: "cwd_missing", message: "")) == "This session's folder is gone")
        #expect(message(HubError(status: 422, code: "bad_cwd", message: "")) == "This session has no working folder")
        #expect(message(HubError(status: 403, code: "not_readable", message: ""))
            == "The hub can't read this session's folder")
        #expect(message(HubError(status: 0, code: "network", message: "")) == "Can't reach the hub")
        #expect(message(HubError(status: 404, code: "http_404", message: "")) == "Update the hub to search files")
        #expect(MentionSearch.message(for: CancellationError(), machine: "m") == "Couldn't search files")
    }

    @Test func theFilesRouteDecodes() throws {
        let json = #"{"cwd":"/w/demo","query":"ma","source":"git","truncated":false,"entries":[{"path":"src/main.py","kind":"file","mtime":1.5},{"path":"src","kind":"dir","mtime":2}]}"#
        let body = try HubWire.decoder.decode(FilesResponse.self, from: Data(json.utf8))
        #expect(body.entries.map(\.path) == ["src/main.py", "src"])
        #expect(body.entries.map(\.isDir) == [false, true])
        #expect(body.source == "git")
        #expect(body.truncated == false)
    }
}

extension HubStubbed {
    @Suite struct HubClientFilesTests {
        @Test func asksTheSessionsFilesRouteWithTheQuery() async throws {
            HubStub.requests = []
            HubStub.handler = { req in
                req.url?.path.hasSuffix("/files") == true
                    ? (200, Data(#"{"entries":[{"path":"docs/a b.md","kind":"file"}]}"#.utf8))
                    : (404, Data())
            }
            let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c",
                                   session: HubStub.session())
            let body = try await client.files("claude:abc@x", query: "a b")
            #expect(body.entries.map(\.path) == ["docs/a b.md"])
            let req = try #require(HubStub.requests.first)
            #expect(req.httpMethod == "GET")
            #expect(req.url?.absoluteString
                == "http://127.0.0.1:8477/v1/sessions/claude%3Aabc%40x/files?limit=50&q=a%20b")
        }

        @Test func aSatelliteSessionsRefusalCarriesItsCode() async {
            HubStub.handler = { _ in
                (422, Data(#"{"error":{"code":"files_unavailable","message":"satellite"}}"#.utf8))
            }
            let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c",
                                   session: HubStub.session())
            let error = await #expect(throws: HubError.self) { try await client.files("k", query: "") }
            #expect(error?.code == "files_unavailable")
        }
    }
}
