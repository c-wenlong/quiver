import Foundation
import Testing
@testable import QuiverCore

/// The `/` menu's token, filter and pick logic — the iPhone app's
/// `PromptMenuTests` slash cases.
@Suite struct SlashTokenTests {
    @Test func slashAtTheStartOpensTheMenu() {
        #expect(Slash.token(in: "/", cursor: nil) == SlashToken(end: 1, query: ""))
        #expect(Slash.token(in: "/rev", cursor: nil) == SlashToken(end: 4, query: "rev"))
        #expect(Slash.token(in: "/review ", cursor: nil) == nil)
        #expect(Slash.token(in: " /review", cursor: nil) == nil)
        #expect(Slash.token(in: "see /review", cursor: nil) == nil)
        #expect(Slash.token(in: "", cursor: nil) == nil)
    }

    @Test func midTokenCursorReadsUpToTheCaret() {
        // "/review the diff", caret after "/re".
        #expect(Slash.token(in: "/review the diff", cursor: 3) == SlashToken(end: 7, query: "re"))
        // Past the first word's end, or before the /: no token.
        #expect(Slash.token(in: "/review the diff", cursor: 10) == nil)
        #expect(Slash.token(in: "/review", cursor: 0) == nil)
    }
}

@Suite struct SlashFilterTests {
    private let commands = [
        SlashCommand(name: "compact", source: "builtin"),
        SlashCommand(name: "review", source: "builtin"),
        SlashCommand(name: "demo:preview", source: "plugin"),
        SlashCommand(name: "Reset-cache", source: "project"),
        SlashCommand(name: "release", source: "user"),
    ]

    @Test func prefixMatchesFirstThenContains() {
        #expect(Slash.filter(commands, query: "re").map(\.name)
            == ["review", "Reset-cache", "release", "demo:preview"])
        #expect(Slash.filter(commands, query: "view").map(\.name) == ["review", "demo:preview"])
    }

    @Test func matchingIsCaseInsensitiveAndAnEmptyQueryKeepsAll() {
        #expect(Slash.filter(commands, query: "").count == 5)
        #expect(Slash.filter(commands, query: "RESET").map(\.name) == ["Reset-cache"])
        #expect(Slash.filter(commands, query: "zzz").isEmpty)
    }
}

@Suite struct SlashInsertTests {
    @Test func theNameGetsASlashAndATrailingSpace() {
        let token = Slash.token(in: "/rev", cursor: nil)!
        let r = Slash.insert(SlashCommand(name: "review"), replacing: token, in: "/rev")
        #expect(r.text == "/review ")
        #expect(r.cursor == 8)
        // The pick closes the menu: the caret sits past the space.
        #expect(Slash.token(in: r.text, cursor: r.cursor) == nil)
    }

    @Test func theHubsInsertTextWins() {
        let token = Slash.token(in: "/dep", cursor: nil)!
        let skill = SlashCommand(name: "deploy-docs", source: "skill", insert: "$deploy-docs ")
        #expect(Slash.insert(skill, replacing: token, in: "/dep").text == "$deploy-docs ")
        #expect(SlashCommand(name: "x", insert: "$x").insertText == "$x ")
    }

    @Test func anExistingSpaceIsReused() {
        // "/mo del", caret after "/mo": the pick eats the word and the
        // space already typed stays put.
        let token = Slash.token(in: "/mo del", cursor: 3)!
        let r = Slash.insert(SlashCommand(name: "model", argumentHint: "<name>", argumentRequired: true),
                             replacing: token, in: "/mo del")
        #expect(r.text == "/model del")
        #expect(r.cursor == 7)
    }

    @Test func sourceTagsReadInSentenceCase() {
        #expect(SlashCommand(name: "a", source: "builtin").sourceLabel == "Built-in")
        #expect(SlashCommand(name: "a", source: "project").sourceLabel == "Project")
        #expect(SlashCommand(name: "a", source: "plugin").sourceLabel == "Plugin")
        #expect(SlashCommand(name: "a").sourceLabel == nil)
    }
}

@Suite struct SlashNotesTests {
    @Test func unsupportedOrEmptySaysSo() {
        let r = CommandsResponse(supported: false, customAvailable: false, commands: [])
        let notes = Slash.notes(r, harness: "Gemini", machine: "studio", remote: false)
        #expect(notes.empty == "No commands for Gemini")
        #expect(notes.footnotes.isEmpty)
        #expect(Slash.notes(CommandsResponse(supported: true, commands: []), harness: "Codex",
                          machine: "m", remote: false).empty == "No commands for Codex")
    }

    @Test func satelliteWithoutCustomCommandsGetsOneFootnote() {
        let r = CommandsResponse(supported: true, customAvailable: false,
                                 commands: [SlashCommand(name: "compact")],
                                 note: "custom commands aren't available for this machine yet")
        let notes = Slash.notes(r, harness: "Claude Code", machine: "studio", remote: true)
        #expect(notes.empty == nil)
        #expect(notes.footnotes == ["Custom commands from studio aren't available yet"])
    }

    @Test func otherNotesShowCapitalized() {
        let r = CommandsResponse(supported: true, customAvailable: true,
                                 commands: [SlashCommand(name: "a")],
                                 note: "built-ins checked last spring")
        #expect(Slash.notes(r, harness: "x", machine: "m", remote: false).footnotes
            == ["Built-ins checked last spring"])
    }

    @Test func theCommandsRouteDecodes() throws {
        let json = #"{"key":"k","harness":"claude","channel":"tmux","attached":true,"supported":true,"custom_available":false,"commands":[{"name":"model","description":"Switch model","source":"builtin","argument_hint":"<name>","argument_required":true},{"name":"demo-skill","description":"A skill","source":"skill","insert":"$demo-skill "}],"note":"n"}"#
        let body = try HubWire.decoder.decode(CommandsResponse.self, from: Data(json.utf8))
        #expect(body.customAvailable == false)
        #expect(body.commands[0].argumentHint == "<name>")
        #expect(body.commands[0].argumentRequired == true)
        #expect(body.commands[1].insert == "$demo-skill ")
    }
}

@Suite @MainActor struct SlashCommandMenuTests {
    @Test func fetchesOnceOnOpenAndFiltersLocally() async {
        let menu = SlashCommandMenu()
        var calls = 0
        menu.fetch = {
            calls += 1
            return CommandsResponse(commands: [SlashCommand(name: "compact"), SlashCommand(name: "review")])
        }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        #expect(menu.track(SlashToken(end: 3, query: "re")) == nil)
        #expect(menu.matches.map(\.name) == ["review"])
        menu.track(nil)
        #expect(menu.track(SlashToken(end: 1, query: "")) == nil)
        #expect(calls == 1)
        await menu.refresh()
        #expect(calls == 2)
        // The chat closed: the next open fetches again.
        menu.reset()
        menu.track(nil)
        await menu.track(SlashToken(end: 1, query: ""))?.value
        #expect(calls == 3)
    }

    @Test func errorRetriesOnTheNextOpen() async {
        let menu = SlashCommandMenu()
        var fail = true
        menu.fetch = {
            if fail { throw HubError(status: 0, code: "network", message: "") }
            return CommandsResponse(commands: [SlashCommand(name: "a")])
        }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        #expect(menu.error == "Can't reach the hub")
        fail = false
        menu.track(nil)
        await menu.track(SlashToken(end: 1, query: ""))?.value
        #expect(menu.error == nil)
        #expect(menu.matches.map(\.name) == ["a"])
    }

    @Test func dismissHoldsUntilTheTokenChanges() async {
        let menu = SlashCommandMenu()
        await menu.track(SlashToken(end: 1, query: ""))?.value
        menu.dismiss()
        #expect(!menu.isOpen)
        menu.track(SlashToken(end: 1, query: ""))
        #expect(!menu.isOpen)
        menu.track(SlashToken(end: 2, query: "c"))
        #expect(menu.isOpen)
    }

    @Test func resumeRefetchesForAnOpenMenu() async {
        let menu = SlashCommandMenu()
        var calls = 0
        menu.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await menu.track(SlashToken(end: 1, query: ""))?.value
        // stop(): the chat closed with the menu open.
        menu.reset()
        #expect(menu.isOpen)
        await menu.resume()?.value
        #expect(calls == 2)
        #expect(menu.matches.map(\.name) == ["compact"])
        #expect(menu.resume() == nil)
    }

    @Test func failedRefreshKeepsTheListAndReopenRetries() async {
        let menu = SlashCommandMenu()
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
        #expect(menu.error == "Can't reach the hub")
        #expect(menu.matches.map(\.name) == ["review"])
        // Typing on doesn't hammer the hub.
        #expect(menu.track(SlashToken(end: 2, query: "r")) == nil)
        // Reopening retries, and success clears the error.
        fail = false
        menu.track(nil)
        await menu.track(SlashToken(end: 1, query: ""))?.value
        #expect(calls == 3)
        #expect(menu.error == nil)
    }

    @Test func warmFetchesAheadOfTheFirstSlash() async {
        let menu = SlashCommandMenu()
        var calls = 0
        menu.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await menu.warm()?.value
        #expect(calls == 1)
        // The first / opens filled: the fetch already answered.
        #expect(menu.track(SlashToken(end: 1, query: "")) == nil)
        #expect(menu.matches.map(\.name) == ["compact"])
    }

    @Test func aKeptListFillsAReopenedChatAtOnce() async {
        let key = "claude:kept-\(#line)"
        let first = SlashCommandMenu()
        first.cacheKey = key
        var calls = 0
        first.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "compact")]) }
        await first.warm()?.value
        #expect(calls == 1)

        // A fresh menu for the same session: the kept list shows at
        // once, and warm() still refreshes it.
        let second = SlashCommandMenu()
        second.cacheKey = key
        second.fetch = { calls += 1; return CommandsResponse(commands: [SlashCommand(name: "rename")]) }
        let refreshing = second.warm()
        #expect(second.matches.map(\.name) == ["compact"])
        await refreshing?.value
        #expect(calls == 2)
        #expect(second.matches.map(\.name) == ["rename"])
        // track() picks the kept list up too, for a menu warm() missed.
        let third = SlashCommandMenu()
        third.cacheKey = key
        third.track(SlashToken(end: 1, query: ""))
        #expect(third.matches.map(\.name) == ["rename"])
    }

    @Test func aStaleAnswerNeverOverwritesANewerList() async {
        let key = "claude:stale-\(#line)"
        let first = SlashCommandMenu()
        first.cacheKey = key
        let gate = HoldGate()
        first.fetch = {
            try await gate.pass()
            return CommandsResponse(commands: [SlashCommand(name: "old")])
        }
        let slow = first.warm()
        #expect(await gate.arrived())
        // The chat reopened and asked again; its newer answer lands
        // first, then the request left behind finishes — the kept list
        // must stay the newer one.
        let second = SlashCommandMenu()
        second.cacheKey = key
        second.fetch = { CommandsResponse(commands: [SlashCommand(name: "new")]) }
        await second.warm()?.value
        gate.open()
        await slow?.value
        let probe = SlashCommandMenu()
        probe.cacheKey = key
        probe.track(SlashToken(end: 1, query: ""))
        #expect(probe.matches.map(\.name) == ["new"])
    }

    @Test func keptListsStayScopedToTheirHub() async {
        let key = "claude:hub-\(#line)"
        let a = SlashCommandMenu()
        a.cacheKey = key
        a.scope = { "hub-a" }
        a.fetch = { CommandsResponse(commands: [SlashCommand(name: "clear")]) }
        await a.warm()?.value

        // A same-keyed session on another hub can't see that list.
        let b = SlashCommandMenu()
        b.cacheKey = key
        b.scope = { "hub-b" }
        b.fetch = { CommandsResponse(commands: [SlashCommand(name: "review")]) }
        let fetching = b.track(SlashToken(end: 1, query: ""))
        #expect(b.matches.isEmpty)
        await fetching?.value
        #expect(b.matches.map(\.name) == ["review"])
        // And the first hub's list is untouched.
        let probe = SlashCommandMenu()
        probe.cacheKey = key
        probe.scope = { "hub-a" }
        probe.track(SlashToken(end: 1, query: ""))
        #expect(probe.matches.map(\.name) == ["clear"])
    }

    @Test func shouldLoadRules() {
        #expect(!SlashCommandMenu.shouldLoad(open: false, opening: false, loaded: false, loading: false, failed: false))
        #expect(SlashCommandMenu.shouldLoad(open: true, opening: false, loaded: false, loading: false, failed: false))
        #expect(!SlashCommandMenu.shouldLoad(open: true, opening: true, loaded: true, loading: false, failed: false))
        #expect(!SlashCommandMenu.shouldLoad(open: true, opening: true, loaded: false, loading: true, failed: false))
        #expect(SlashCommandMenu.shouldLoad(open: true, opening: true, loaded: true, loading: false, failed: true))
        #expect(!SlashCommandMenu.shouldLoad(open: true, opening: false, loaded: false, loading: false, failed: true))
    }

    @Test func returnPicksSendsOrWaits() {
        #expect(Slash.ReturnAction.action(row: true, loading: false) == .pick)
        #expect(Slash.ReturnAction.action(row: true, loading: true) == .pick)
        #expect(Slash.ReturnAction.action(row: false, loading: true) == .wait)
        // An answered list with nothing pickable — no match, a failed
        // fetch, an unsupported harness — falls through to send.
        #expect(Slash.ReturnAction.action(row: false, loading: false) == .send)
    }

    @Test func failuresBecomeOneLine() {
        #expect(SlashCommandMenu.message(for: HubError(status: 422, code: "unknown_session", message: "x"))
            == "The hub doesn't know this session any more")
        #expect(SlashCommandMenu.message(for: HubError(status: 404, code: "http_404", message: ""))
            == "Update the hub to list commands")
        #expect(SlashCommandMenu.message(for: CancellationError()) == "Couldn't load commands")
    }
}
