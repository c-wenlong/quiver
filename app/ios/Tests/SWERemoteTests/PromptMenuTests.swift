import XCTest
@testable import SWERemote

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

final class SlashTokenTests: XCTestCase {
    func testOnlyAtTheVeryStartWhileTheFirstWordIsTyped() {
        XCTAssertEqual(Slash.token(in: "/", cursor: nil), SlashToken(end: 1, query: ""))
        XCTAssertEqual(Slash.token(in: "/rev", cursor: nil), SlashToken(end: 4, query: "rev"))
        XCTAssertNil(Slash.token(in: "/review ", cursor: nil))
        XCTAssertNil(Slash.token(in: " /review", cursor: nil))
        XCTAssertNil(Slash.token(in: "see /review", cursor: nil))
        XCTAssertNil(Slash.token(in: "", cursor: nil))
    }

    func testCaretInsideTheFirstWord() {
        // "/review the diff", caret after "/re".
        XCTAssertEqual(Slash.token(in: "/review the diff", cursor: 3), SlashToken(end: 7, query: "re"))
        // Caret in the argument: closed.
        XCTAssertNil(Slash.token(in: "/review the diff", cursor: 10))
        XCTAssertNil(Slash.token(in: "/review", cursor: 0))
    }
}

final class SlashFilterTests: XCTestCase {
    private let commands = [
        SlashCommand(name: "compact", source: "builtin"),
        SlashCommand(name: "review", source: "builtin"),
        SlashCommand(name: "demo:preview", source: "plugin"),
        SlashCommand(name: "Reset-cache", source: "project"),
        SlashCommand(name: "release", source: "user"),
    ]

    func testPrefixBeforeContainsKeepingHubOrder() {
        XCTAssertEqual(Slash.filter(commands, query: "re").map(\.name), ["review", "Reset-cache", "release", "demo:preview"])
        XCTAssertEqual(Slash.filter(commands, query: "view").map(\.name), ["review", "demo:preview"])
    }

    func testEmptyQueryKeepsEverythingAndCaseIsIgnored() {
        XCTAssertEqual(Slash.filter(commands, query: "").count, 5)
        XCTAssertEqual(Slash.filter(commands, query: "RESET").map(\.name), ["Reset-cache"])
        XCTAssertEqual(Slash.filter(commands, query: "zzz"), [])
    }
}

final class SlashInsertTests: XCTestCase {
    func testDefaultInsertIsSlashNameAndASpace() {
        let token = Slash.token(in: "/rev", cursor: nil)!
        let r = Slash.insert(SlashCommand(name: "review"), replacing: token, in: "/rev")
        XCTAssertEqual(r.text, "/review ")
        XCTAssertEqual(r.cursor, 8)
        XCTAssertNil(Slash.token(in: r.text, cursor: r.cursor))
    }

    func testInsertTextReplacesTheDefault() {
        let token = Slash.token(in: "/dep", cursor: nil)!
        let skill = SlashCommand(name: "deploy-docs", source: "skill", insert: "$deploy-docs ")
        XCTAssertEqual(Slash.insert(skill, replacing: token, in: "/dep").text, "$deploy-docs ")
        XCTAssertEqual(skill.label, "$deploy-docs")
        // An insert without its space still gets one.
        XCTAssertEqual(SlashCommand(name: "x", insert: "$x").insertText, "$x ")
    }

    func testArgumentRequiredLeavesTheCaretReadyForIt() {
        let token = Slash.token(in: "/mo", cursor: nil)!
        let model = SlashCommand(name: "model", argumentHint: "<name>", argumentRequired: true)
        let r = Slash.insert(model, replacing: token, in: "/mo")
        XCTAssertEqual(r.text, "/model ")
        XCTAssertEqual(r.cursor, r.text.count)
    }

    func testWholeFirstWordGoesAndTheRestStays() {
        let text = "/rev the diff"
        let token = Slash.token(in: text, cursor: 3)!
        let r = Slash.insert(SlashCommand(name: "review"), replacing: token, in: text)
        XCTAssertEqual(r.text, "/review the diff")
        XCTAssertEqual(r.cursor, 8)
    }

    func testSourceLabels() {
        XCTAssertEqual(SlashCommand(name: "a", source: "builtin").sourceLabel, "Built-in")
        XCTAssertEqual(SlashCommand(name: "a", source: "project").sourceLabel, "Project")
        XCTAssertEqual(SlashCommand(name: "a", source: "plugin").sourceLabel, "Plugin")
        XCTAssertNil(SlashCommand(name: "a").sourceLabel)
    }
}

final class SlashNotesTests: XCTestCase {
    func testUnsupportedOrEmptySaysSo() {
        let r = CommandsResponse(supported: false, customAvailable: false, commands: [])
        let notes = Slash.notes(r, harness: "Gemini", machine: "studio", remote: false)
        XCTAssertEqual(notes.empty, "No commands for Gemini")
        XCTAssertEqual(notes.footnotes, [])
        XCTAssertEqual(Slash.notes(CommandsResponse(supported: true, commands: []), harness: "Codex", machine: "m",
                                   remote: false).empty, "No commands for Codex")
    }

    func testSatelliteWithoutCustomCommandsGetsOneFootnote() {
        let r = CommandsResponse(supported: true, customAvailable: false, commands: [SlashCommand(name: "compact")],
                                 note: "custom commands aren't available for this machine yet")
        let notes = Slash.notes(r, harness: "Claude Code", machine: "studio", remote: true)
        XCTAssertNil(notes.empty)
        XCTAssertEqual(notes.footnotes, ["Custom commands from studio aren't available yet"])
    }

    func testOtherNotesShowCapitalized() {
        let r = CommandsResponse(supported: true, customAvailable: true, commands: [SlashCommand(name: "a")],
                                 note: "built-ins checked last spring")
        XCTAssertEqual(Slash.notes(r, harness: "x", machine: "m", remote: false).footnotes, ["Built-ins checked last spring"])
    }

    func testDecodesTheCommandsRoute() throws {
        let json = #"{"key":"k","harness":"claude","channel":"tmux","attached":true,"supported":true,"custom_available":false,"commands":[{"name":"model","description":"Switch model","source":"builtin","argument_hint":"<name>","argument_required":true},{"name":"demo-skill","description":"A skill","source":"skill","insert":"$demo-skill "}],"note":"n"}"#
        let body = try Hub.decoder.decode(CommandsResponse.self, from: Data(json.utf8))
        XCTAssertEqual(body.customAvailable, false)
        XCTAssertEqual(body.commands[0].argumentHint, "<name>")
        XCTAssertEqual(body.commands[0].argumentRequired, true)
        XCTAssertEqual(body.commands[1].insert, "$demo-skill ")
    }
}

final class MentionReviewFixTests: XCTestCase {
    func testIOS17KeepsFollowingTheEndAfterAPick() {
        // iOS 17: a pick leaves no fixed caret, so a later @ still opens.
        let token = Mention.token(in: "@ma", cursor: nil)!
        let next = Mention.insert("main.py", isDir: false, replacing: token, in: "@ma")
        let cursor = Caret.afterPick(next.cursor, tracksCaret: false)
        XCTAssertNil(cursor)
        let typed = next.text + "and @rea"
        XCTAssertEqual(Mention.token(in: typed, cursor: cursor)?.query, "rea")
        // A fixed caret left from the pick would have missed it.
        XCTAssertNil(Mention.token(in: typed, cursor: next.cursor))
        // iOS 18 keeps the offset, which its field keeps up to date.
        XCTAssertEqual(Caret.afterPick(9, tracksCaret: true), 9)
    }

    func testFolderWithSpacesIsQuotedAndStaysOpen() {
        let token = Mention.token(in: "see @My", cursor: nil)!
        let folder = Mention.insert("My Documents", isDir: true, replacing: token, in: "see @My")
        XCTAssertEqual(folder.text, "see @\"My Documents/")
        XCTAssertEqual(folder.cursor, folder.text.count)
        let inside = Mention.token(in: folder.text, cursor: folder.cursor)
        XCTAssertEqual(inside, MentionToken(start: 4, end: folder.text.count, query: "My Documents/"))
        // Drilling in: the file closes the quote.
        let file = Mention.insert("My Documents/plan notes.txt", isDir: false, replacing: inside!, in: folder.text)
        XCTAssertEqual(file.text, "see @\"My Documents/plan notes.txt\" ")
        XCTAssertEqual(file.cursor, file.text.count)
        XCTAssertNil(Mention.token(in: file.text, cursor: file.cursor))
    }

    func testQuotedTokenMidText() {
        let text = "open @\"My Do\" please"
        // Caret after "@\"My D".
        let token = Mention.token(in: text, cursor: 11)
        XCTAssertEqual(token, MentionToken(start: 5, end: 13, query: "My D"))
        let r = Mention.insert("My Docs/a b.md", isDir: false, replacing: token!, in: text)
        XCTAssertEqual(r.text, "open @\"My Docs/a b.md\" please")
        XCTAssertEqual(r.cursor, 23)
    }

    func testClosedQuotesAndQuotesInWordsDoNotTrigger() {
        XCTAssertNil(Mention.token(in: "@\"a b\" then", cursor: nil))
        XCTAssertNil(Mention.token(in: "say x@\"y", cursor: nil))
        XCTAssertEqual(Mention.token(in: "@\"a b\" then @c", cursor: nil)?.query, "c")
    }
}

@MainActor
final class CommandMenuTests: XCTestCase {
    // The kept lists are static: start each test without the last one's.
    override func setUp() async throws {
        try await super.setUp()
        await CommandMenu.forgetKeptLists()
    }

    func testFetchesOnceOnOpenAndFiltersLocally() async {
        let menu = CommandMenu()
        var calls = 0
        menu.fetch = {
            calls += 1
            return CommandsResponse(commands: [SlashCommand(name: "compact"), SlashCommand(name: "review")])
        }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        XCTAssertNil(menu.track(SlashToken(end: 3, query: "re")))
        XCTAssertEqual(menu.matches.map(\.name), ["review"])
        menu.track(nil)
        XCTAssertNil(menu.track(SlashToken(end: 1, query: "")))
        XCTAssertEqual(calls, 1)
        await menu.refresh()
        XCTAssertEqual(calls, 2)
        // The chat closed: the next open fetches again.
        menu.reset()
        menu.track(nil)
        await menu.track(SlashToken(end: 1, query: ""))?.value
        XCTAssertEqual(calls, 3)
    }

    func testErrorRetriesOnTheNextOpen() async {
        let menu = CommandMenu()
        var fail = true
        menu.fetch = {
            if fail { throw HubError(status: 0, code: "network", message: "") }
            return CommandsResponse(commands: [SlashCommand(name: "a")])
        }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        XCTAssertEqual(menu.error, "Can't reach the hub")
        fail = false
        menu.track(nil)
        await menu.track(SlashToken(end: 1, query: ""))?.value
        XCTAssertNil(menu.error)
        XCTAssertEqual(menu.matches.map(\.name), ["a"])
    }

    func testDismissHoldsUntilTheTokenChanges() async {
        let menu = CommandMenu()
        await menu.track(SlashToken(end: 1, query: ""))?.value
        menu.dismiss()
        XCTAssertFalse(menu.isOpen)
        menu.track(SlashToken(end: 1, query: ""))
        XCTAssertFalse(menu.isOpen)
        menu.track(SlashToken(end: 2, query: "c"))
        XCTAssertTrue(menu.isOpen)
    }

    func testWarmFetchesAheadOfTheFirstSlash() async {
        let menu = CommandMenu()
        var calls = 0
        menu.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await menu.warm()?.value
        XCTAssertEqual(calls, 1)
        // The first / opens filled: the fetch already answered.
        XCTAssertNil(menu.track(SlashToken(end: 1, query: "")))
        XCTAssertEqual(menu.matches.map(\.name), ["compact"])
    }

    func testAKeptListFillsAReopenedChatAtOnce() async {
        let key = "claude:kept-\(#line)"
        let first = CommandMenu()
        first.cacheKey = key
        var calls = 0
        first.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await first.warm()?.value
        XCTAssertEqual(calls, 1)

        // A fresh menu for the same session: the kept list shows at
        // once, and warm() still refreshes it.
        let second = CommandMenu()
        second.cacheKey = key
        second.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "rename")]) }
        let refreshing = second.warm()
        XCTAssertEqual(second.matches.map(\.name), ["compact"])
        await refreshing?.value
        XCTAssertEqual(calls, 2)
        XCTAssertEqual(second.matches.map(\.name), ["rename"])
        // track() picks the kept list up too, for a menu warm() missed.
        let third = CommandMenu()
        third.cacheKey = key
        third.track(SlashToken(end: 1, query: ""))
        XCTAssertEqual(third.matches.map(\.name), ["rename"])
    }

    func testAStaleAnswerNeverOverwritesANewerList() async {
        let key = "claude:stale-\(#line)"
        let first = CommandMenu()
        first.cacheKey = key
        var gate: CheckedContinuation<Void, Never>?
        first.fetch = {
            await withCheckedContinuation { gate = $0 }
            return CommandsResponse(commands: [SlashCommand(name: "old")])
        }
        let slow = first.warm()
        for _ in 0..<1000 where gate == nil { await Task.yield() }
        // The chat reopened and asked again; its newer answer lands
        // first, then the request left behind finishes — the kept list
        // must stay the newer one.
        let second = CommandMenu()
        second.cacheKey = key
        second.fetch = { CommandsResponse(commands: [SlashCommand(name: "new")]) }
        await second.warm()?.value
        gate?.resume()
        await slow?.value
        let probe = CommandMenu()
        probe.cacheKey = key
        probe.track(SlashToken(end: 1, query: ""))
        XCTAssertEqual(probe.matches.map(\.name), ["new"])
    }

    func testKeptListsStayScopedToTheirHub() async {
        let key = "claude:hub-\(#line)"
        let a = CommandMenu()
        a.cacheKey = key
        a.scope = { "hub-a" }
        a.fetch = { CommandsResponse(commands: [SlashCommand(name: "clear")]) }
        await a.warm()?.value

        // A same-keyed session on another hub can't see that list.
        let b = CommandMenu()
        b.cacheKey = key
        b.scope = { "hub-b" }
        b.fetch = { CommandsResponse(commands: [SlashCommand(name: "review")]) }
        let fetching = b.track(SlashToken(end: 1, query: ""))
        XCTAssertTrue(b.matches.isEmpty)
        await fetching?.value
        XCTAssertEqual(b.matches.map(\.name), ["review"])
        // And the first hub's list is untouched.
        let probe = CommandMenu()
        probe.cacheKey = key
        probe.scope = { "hub-a" }
        probe.track(SlashToken(end: 1, query: ""))
        XCTAssertEqual(probe.matches.map(\.name), ["clear"])
    }
}

@MainActor
final class MentionResumeTests: XCTestCase {
    func testResumeRestartsTheOpenSearch() async {
        let search = MentionSearch()
        var asked: [String] = []
        search.pause = { _ in }
        search.fetch = { q in asked.append(q); return FilesResponse(entries: [FileEntry(path: "a.txt")]) }
        await search.track(MentionToken(start: 0, end: 2, query: "a"))?.value
        // stop(): the app went to the background with the menu open.
        search.reset()
        XCTAssertTrue(search.isOpen)
        XCTAssertNil(search.answered)
        await search.resume()?.value
        XCTAssertEqual(asked, ["a", "a"])
        XCTAssertEqual(search.entries.map(\.path), ["a.txt"])
        // Nothing to restart once answered, or with the menu closed.
        XCTAssertNil(search.resume())
        search.track(nil)
        XCTAssertNil(search.resume())
    }
}

@MainActor
final class CommandMenuResumeTests: XCTestCase {
    // The kept lists are static: start each test without the last one's.
    override func setUp() async throws {
        try await super.setUp()
        await CommandMenu.forgetKeptLists()
    }

    func testResumeRefetchesForAnOpenMenu() async {
        let menu = CommandMenu()
        var calls = 0
        menu.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        // stop(): background with the menu open.
        menu.reset()
        XCTAssertTrue(menu.isOpen)
        await menu.resume()?.value
        XCTAssertEqual(calls, 2)
        XCTAssertEqual(menu.matches.map(\.name), ["compact"])
        XCTAssertNil(menu.resume())
    }

    func testIOS17SlashPickFollowsTheEnd() {
        let token = Slash.token(in: "/rev", cursor: nil)!
        let next = Slash.insert(SlashCommand(name: "review"), replacing: token, in: "/rev")
        XCTAssertNil(Caret.afterPick(next.cursor, tracksCaret: false))
        XCTAssertEqual(Mention.token(in: next.text + "@sr", cursor: nil)?.query, "sr")
    }
}

@MainActor
final class CommandMenuReviewFixTests: XCTestCase {
    // The kept lists are static: start each test without the last one's.
    override func setUp() async throws {
        try await super.setUp()
        await CommandMenu.forgetKeptLists()
    }

    func testOpenMenuReloadsAfterResetWhileFocused() async {
        let menu = CommandMenu()
        var calls = 0
        menu.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        // stop() while the box kept focus: the list is gone, the menu open.
        menu.reset()
        XCTAssertTrue(menu.isOpen)
        // Typing recovers on its own...
        await menu.track(SlashToken(end: 2, query: "c"))?.value
        XCTAssertEqual(calls, 2)
        XCTAssertEqual(menu.matches.map(\.name), ["compact"])
        // ...and so does start() through resume(), with nothing typed.
        menu.reset()
        await menu.resume()?.value
        XCTAssertEqual(calls, 3)
        XCTAssertNotNil(menu.response)
    }

    func testFailedRefreshKeepsTheListAndReopenRetries() async {
        let menu = CommandMenu()
        var fail = false
        var calls = 0
        menu.fetch = {
            calls += 1
            if fail { throw HubError(status: 0, code: "network", message: "") }
            return CommandsResponse(commands: [SlashCommand(name: "review")])
        }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        fail = true
        await menu.refresh()
        XCTAssertEqual(menu.error, "Can't reach the hub")
        XCTAssertEqual(menu.matches.map(\.name), ["review"])
        // Typing on doesn't hammer the hub.
        XCTAssertNil(menu.track(SlashToken(end: 2, query: "r")))
        // Reopening retries, and success clears the error.
        fail = false
        menu.track(nil)
        await menu.track(SlashToken(end: 1, query: ""))?.value
        XCTAssertEqual(calls, 3)
        XCTAssertNil(menu.error)
    }

    func testShouldLoad() {
        XCTAssertFalse(CommandMenu.shouldLoad(open: false, opening: false, loaded: false, loading: false, failed: false))
        XCTAssertTrue(CommandMenu.shouldLoad(open: true, opening: false, loaded: false, loading: false, failed: false))
        XCTAssertFalse(CommandMenu.shouldLoad(open: true, opening: true, loaded: true, loading: false, failed: false))
        XCTAssertFalse(CommandMenu.shouldLoad(open: true, opening: true, loaded: false, loading: true, failed: false))
        XCTAssertTrue(CommandMenu.shouldLoad(open: true, opening: true, loaded: true, loading: false, failed: true))
        XCTAssertFalse(CommandMenu.shouldLoad(open: true, opening: false, loaded: false, loading: false, failed: true))
    }
}
