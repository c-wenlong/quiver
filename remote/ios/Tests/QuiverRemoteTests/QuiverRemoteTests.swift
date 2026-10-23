import XCTest
@testable import QuiverRemote

final class SSEParserTests: XCTestCase {
    func testDataLineEndsFrameOnBlankLine() {
        var p = SSEParser()
        XCTAssertNil(p.feed("id: 4"))
        XCTAssertNil(p.feed("event: message.appended"))
        XCTAssertNil(p.feed("data: {\"type\":\"x\"}"))
        XCTAssertEqual(p.feed(""), "{\"type\":\"x\"}")
    }

    func testKeepaliveCommentYieldsNothing() {
        var p = SSEParser()
        XCTAssertNil(p.feed(": ka"))
        XCTAssertNil(p.feed(""))
    }
}

final class PairLinkTests: XCTestCase {
    func testQuiverScheme() {
        let link = PairLink(URL(string: "quiver://pair?host=100.64.0.5&port=9000&token=abc&name=phone")!)
        XCTAssertEqual(link?.baseURL.absoluteString, "http://100.64.0.5:9000")
        XCTAssertEqual(link?.token, "abc")
        XCTAssertEqual(link?.name, "phone")
    }

    func testDefaultPortAndMissingToken() {
        XCTAssertEqual(PairLink(URL(string: "quiver://pair?host=mac&token=t")!)?.baseURL.absoluteString, "http://mac:8477")
        XCTAssertNil(PairLink(URL(string: "quiver://pair?host=mac")!))
        XCTAssertEqual(
            PairLink(URL(string: "quiver://pair?host=box.tailnet.ts.net&port=8477&token=t&scheme=https")!)?.baseURL.absoluteString,
            "https://box.tailnet.ts.net:8477")
        // Only https upgrades; anything else stays plain http.
        XCTAssertEqual(
            PairLink(URL(string: "quiver://pair?host=mac&token=t&scheme=ftp")!)?.baseURL.absoluteString,
            "http://mac:8477")
        XCTAssertNil(PairLink(URL(string: "https://example.com/")!))
    }

    func testBrowserFragmentForm() {
        let link = PairLink(URL(string: "http://mac.ts.net:8477/#pair=tok&name=ipad")!)
        XCTAssertEqual(link?.baseURL.absoluteString, "http://mac.ts.net:8477")
        XCTAssertEqual(link?.token, "tok")
        XCTAssertEqual(link?.name, "ipad")
    }

    func testHubURLFromTypedInput() {
        XCTAssertEqual(hubURL(from: " 100.64.1.2 ")?.absoluteString, "http://100.64.1.2:8477")
        XCTAssertEqual(hubURL(from: "mac:9000")?.absoluteString, "http://mac:9000")
        XCTAssertEqual(hubURL(from: "http://mac:8477/#approve")?.absoluteString, "http://mac:8477")
        XCTAssertNil(hubURL(from: "   "))
    }
}

final class ToolDiffTests: XCTestCase {
    func testEditDiffKeepsContextAndCounts() {
        let tool = ToolCall(name: "Edit", callId: "c1", argsSummary: nil,
                            argsFull: #"{"file_path":"a.js","old_string":"a\nb\nc","new_string":"a\nB\nc"}"#)
        let diff = ToolDiff.from(tool)
        XCTAssertEqual(diff?.file, "a.js")
        XCTAssertEqual(diff?.added, 1)
        XCTAssertEqual(diff?.removed, 1)
        XCTAssertEqual(diff?.lines.map(\.text), ["a", "b", "B", "c"])
    }

    func testWriteIsAllAdded() {
        let tool = ToolCall(name: "write", callId: nil, argsSummary: nil, argsFull: #"{"path":"n.txt","content":"x\ny"}"#)
        XCTAssertEqual(ToolDiff.from(tool)?.added, 2)
        XCTAssertEqual(ToolDiff.from(tool)?.removed, 0)
    }

    func testNonEditToolHasNoDiff() {
        XCTAssertNil(ToolDiff.from(ToolCall(name: "Bash", callId: nil, argsSummary: nil, argsFull: #"{"command":"ls"}"#)))
    }

    func testLongUnchangedPrefixFolds() {
        let lines = ToolDiff.diff(old: "1\n2\n3\n4\n5\nold", new: "1\n2\n3\n4\n5\nnew")
        XCTAssertEqual(lines.first?.kind, .meta)
        XCTAssertEqual(lines.filter { $0.kind == .context }.count, 3)
    }
}

final class MarkdownTests: XCTestCase {
    func testTableFromACodexMessage() {
        let text = """
        Nothing changed yet.

        | Resource | Action |
        |---|---|
        | Cloud Scheduler `toolbox-v2-db-sync` | Keep paused, then delete. |
        | Secrets | Remove the `*-dsn` secrets \\| after revoking. |

        **Keep both databases.**
        """
        let blocks = Markdown.parse(text)
        XCTAssertEqual(blocks.count, 3)
        XCTAssertEqual(blocks[0], .paragraph("Nothing changed yet."))
        guard case .table(let t) = blocks[1] else { return XCTFail("expected a table") }
        XCTAssertEqual(t.header, ["Resource", "Action"])
        XCTAssertEqual(t.rows.count, 2)
        XCTAssertEqual(t.rows[0][0], "Cloud Scheduler `toolbox-v2-db-sync`")
        XCTAssertEqual(t.rows[1][1], "Remove the `*-dsn` secrets | after revoking.", "an escaped pipe stays in its cell")
        XCTAssertEqual(blocks[2], .paragraph("**Keep both databases.**"))
    }

    func testAlignmentRow() {
        XCTAssertEqual(Markdown.separatorAlignments("| :--- | :---: | ---: |"), [.leading, .center, .trailing])
        XCTAssertNil(Markdown.separatorAlignments("---"), "a lone rule is not a table separator")
        XCTAssertNil(Markdown.separatorAlignments("| a | b |"))
    }

    func testHeadingsListsQuotesRulesAndCode() {
        let blocks = Markdown.parse("""
        ## Plan
        1. First
        2. Second
          continued
        - nested parent
          - child
        > quoted
        ---
        ```swift
        let x = 1
        ```
        #not a heading
        """)
        XCTAssertEqual(blocks, [
            .heading(level: 2, text: "Plan"),
            .listItem(indent: 0, marker: "1.", text: "First"),
            .listItem(indent: 0, marker: "2.", text: "Second\ncontinued"),
            .listItem(indent: 0, marker: "•", text: "nested parent"),
            .listItem(indent: 1, marker: "•", text: "child"),
            .quote("quoted"),
            .rule,
            .code(language: "swift", text: "let x = 1"),
            .paragraph("#not a heading"),
        ])
    }

    func testReviewFixes() {
        // Only \| is an escape; other backslashes reach the inline parser.
        XCTAssertEqual(Markdown.cells(#"| `C:\Users\alice` | \d+ \| x |"#), [#"`C:\Users\alice`"#, #"\d+ | x"#])
        // Hashes in a heading's text survive; a spaced closing run doesn't.
        XCTAssertEqual(Markdown.heading("## C#")?.1, "C#")
        XCTAssertEqual(Markdown.heading("## #123")?.1, "#123")
        XCTAssertEqual(Markdown.heading("## Title ##")?.1, "Title")
        // A fence or table indented under a list item stays its own block.
        let blocks = Markdown.parse("1. Run\n   ```\n   make test\n   ```\n2. Then\n   | a | b |\n   |---|---|\n   | 1 | 2 |")
        XCTAssertEqual(blocks.count, 4)
        XCTAssertEqual(blocks[0], .listItem(indent: 0, marker: "1.", text: "Run"))
        XCTAssertEqual(blocks[1], .code(language: nil, text: "make test"))
        guard case .table(let t) = blocks[3] else { return XCTFail("expected a table") }
        XCTAssertEqual(t.rows, [["1", "2"]])
    }

    func testRenderingDefectsFromTheHarnessSurvey() {
        let blocks = Markdown.parse("""
        Done.

        ![Backfill outcomes](/Users/k/Downloads/outcomes.png)

        - [ ] **Privacy (your call).**
        - [x] Shipped
        <json-render>{"root":"d",
        "elements":{}}</json-render>
        After.
        """)
        XCTAssertEqual(blocks[1], .image(alt: "Backfill outcomes", source: "/Users/k/Downloads/outcomes.png"))
        guard case .listItem(_, _, let open) = blocks[2], case .listItem(_, _, let done) = blocks[3] else { return XCTFail() }
        XCTAssertEqual(Markdown.taskBox(open)?.0, .open)
        XCTAssertEqual(Markdown.taskBox(open)?.1, "**Privacy (your call).**")
        XCTAssertEqual(Markdown.taskBox(done)?.0, .done)
        guard case .rendered = blocks[4] else { return XCTFail("expected a rendered view block") }
        XCTAssertEqual(blocks[5], .paragraph("After."))
        XCTAssertEqual(Markdown.inlineImagesAsText("see ![chart](a.png) here"), "see *Image: chart* here")
    }

    func testRenderingReviewEdgeCases() {
        // Code spans and escaped syntax keep the example as written.
        XCTAssertEqual(Markdown.inlineImagesAsText("write `![chart](a.png)` or ![b](b.png)"), "write `![chart](a.png)` or *Image: b*")
        XCTAssertEqual(Markdown.inlineImagesAsText(#"literal \![x](y.png)"#), #"literal \![x](y.png)"#)
        // Two images on one line are not one image.
        XCTAssertNil(Markdown.imageLine("![first](a.png)![second](b.png)"))
        // Text after the closing tag still shows.
        XCTAssertEqual(Markdown.parse("<json-render>{}</json-render> Saved successfully."),
                       [.rendered("<json-render>{}</json-render>"), .paragraph("Saved successfully.")])
        // A view or an image under a list item gets its own block.
        let blocks = Markdown.parse("- Result\n  <json-render>{}</json-render>\n- Chart\n  ![c](c.png)")
        XCTAssertEqual(blocks, [.listItem(indent: 0, marker: "•", text: "Result"), .rendered("<json-render>{}</json-render>"),
                                .listItem(indent: 0, marker: "•", text: "Chart"), .image(alt: "c", source: "c.png")])
    }

    func testLocalFileLinksBecomeFileNames() {
        let s = RichText.inline("Read [AGENTS.md](/Users/k/.quiver/AGENTS.md), [ai.py](finance/ai.py) and [PR](https://github.com/x/y/pull/1).")
        let links = s.runs.compactMap(\.link)
        XCTAssertEqual(links.map(\.absoluteString), ["https://github.com/x/y/pull/1"])
        XCTAssertTrue(String(s.characters).contains("AGENTS.md"))
    }

    func testMcpToolNamesRead() {
        XCTAssertEqual(ToolArgs.displayName("mcp__computer-use__app_batch"), "computer-use · app_batch")
        XCTAssertEqual(ToolArgs.displayName("mcp__dv__github__create_pull_request"), "dv · github__create_pull_request")
        XCTAssertEqual(ToolArgs.displayName("Bash"), "Bash")
        let call = ToolCall(name: "mcp__computer-use__app_batch", callId: nil, argsSummary: "mcp__computer-use__app_batch", argsFull: nil)
        XCTAssertEqual(ToolArgs.summary(call), "computer-use · app_batch")
        let withPath = ToolCall(name: "mcp__fs__read", callId: nil, argsSummary: "mcp__fs__read · src/a.py", argsFull: nil)
        XCTAssertEqual(ToolArgs.summary(withPath), "fs · read · src/a.py")
    }

    func testUnclosedFenceIsCode() {
        XCTAssertEqual(Markdown.parse("```\nhalf"), [.code(language: nil, text: "half")])
    }

    func testRaggedRowsPadToTheWidestRow() {
        guard case .table(let t)? = Markdown.parse("| a |\n|---|\n| 1 | 2 | 3 |").first else { return XCTFail() }
        XCTAssertEqual(t.columnCount, 3)
    }
}

final class GroupingTests: XCTestCase {
    private func row(_ key: String, cwd: String, status: String?, ago: Double, now: Double) -> SessionRow {
        SessionRow(key: key, harness: "claude", agent: "Claude Code", cwd: cwd, title: key, lastActiveAt: now - ago,
                   status: status, machineId: "m1", ownership: "observed", attachable: true)
    }

    func testRecentGroupByFolderAndOldFoldIntoEarlier() {
        let now = 1_000_000.0
        let groups = SessionGrouping.groups([
            row("a", cwd: "/u/k/projects/sources", status: "active", ago: 60, now: now),
            row("b", cwd: "/u/k/projects/sources", status: "done", ago: 3600, now: now),
            row("c", cwd: "/u/k/quiver", status: "done", ago: 3 * 86_400, now: now),
            row("d", cwd: "/u/k/quiver", status: "followup", ago: 5 * 86_400, now: now),
            row("e", cwd: "/u/k/alke", status: "approval", ago: 9 * 86_400, now: now),
        ], machines: [], now: now)
        XCTAssertEqual(groups.map(\.title), ["projects/sources", "k/alke", "Earlier"])
        XCTAssertEqual(groups[0].rows.map(\.key), ["a", "b"])
        XCTAssertEqual(groups[1].rows.map(\.key), ["e"], "a pending approval never folds away")
        XCTAssertEqual(groups[2].rows.map(\.key), ["c", "d"], "a follow-up days old is stale")
    }

    func testStaleFollowupIsNotWaiting() {
        let now = Date().timeIntervalSince1970
        XCTAssertTrue(row("a", cwd: "/x", status: "followup", ago: 3600, now: now).needsYouNow)
        XCTAssertFalse(row("b", cwd: "/x", status: "followup", ago: 4 * 86_400, now: now).needsYouNow)
        XCTAssertEqual(Machine(id: "m", name: "dev-macbook-pro.local", os: nil, online: nil, lastSeenAt: nil, harnesses: nil).displayName,
                       "dev-macbook-pro")
    }

    func testDecodesHubRowWithUnknownStatus() throws {
        let json = #"{"sessions":[{"key":"claude:1","harness":"claude","status":"paused","ownership":"attached","attachable":true,"last_active_at":5}],"server_time":9}"#
        let body = try Hub.decoder.decode(SessionsResponse.self, from: Data(json.utf8))
        XCTAssertEqual(body.sessions.first?.sessionStatus, .other("paused"))
        XCTAssertEqual(body.sessions.first?.isAttached, true)
        XCTAssertEqual(body.sessions.first?.displayTitle, "Untitled session")
    }
}

@MainActor
final class ChatModelTests: XCTestCase {
    private func env(_ json: String) -> Envelope {
        try! Hub.decoder.decode(Envelope.self, from: Data(json.utf8))
    }

    func testToolResultMergesIntoCallAndDuplicatesAreDropped() {
        let chat = ChatModel(key: "claude:1", app: AppModel())
        let use = env(#"{"type":"message.appended","seq":1,"data":{"kind":"tool_use","text":"","tool":{"name":"Bash","call_id":"t1","args_summary":"Bash · ls"}}}"#)
        chat.apply(use)
        chat.apply(use)
        chat.apply(env(#"{"type":"message.appended","seq":2,"data":{"kind":"tool_result","text":"a.txt","result_ref":"t1"}}"#))
        XCTAssertEqual(chat.items.count, 1)
        guard case .tool(let call, let result) = chat.items[0].kind else { return XCTFail("expected a tool item") }
        XCTAssertEqual(call.name, "Bash")
        XCTAssertEqual(result, "a.txt")
    }

    func testApprovalRequestedThenResolved() {
        let chat = ChatModel(key: "claude:1", app: AppModel())
        chat.apply(env(#"{"type":"approval.requested","seq":3,"data":{"request_id":"r1","prompt":"Run npm test?"}}"#))
        chat.apply(env(#"{"type":"approval.resolved","seq":4,"data":{"request_id":"r1","decision":"allow"}}"#))
        XCTAssertEqual(chat.items.first?.kind, .approval(requestId: "r1", prompt: "Run npm test?", state: .resolved("allow")))
    }

    func testResultOnNewerPageWaitsForItsCallOnAnOlderPage() {
        var orphans: [String: String] = [:]
        // The newest page starts with a result whose call is a page back.
        var newer: [ChatItem] = []
        var newerCards: [String: String] = [:]
        ChatModel.add(MessageData(kind: "tool_result", text: "a.txt", ts: nil, tool: nil, resultRef: "t1"),
                      id: "m5", to: &newer, cards: &newerCards, orphans: &orphans)
        XCTAssertTrue(newer.isEmpty)
        XCTAssertEqual(orphans, ["t1": "a.txt"])

        // The older page holds that call, plus a result for an even older one.
        var older: [ChatItem] = []
        var olderCards: [String: String] = [:]
        ChatModel.add(MessageData(kind: "tool_result", text: "b", ts: nil, tool: nil, resultRef: "t0"),
                      id: "m3", to: &older, cards: &olderCards, orphans: &orphans)
        ChatModel.add(MessageData(kind: "tool_use", text: "", ts: nil,
                                  tool: ToolCall(name: "Bash", callId: "t1", argsSummary: "ls", argsFull: nil), resultRef: nil),
                      id: "m4", to: &older, cards: &olderCards, orphans: &orphans)
        XCTAssertEqual(older.count, 1)
        guard case .tool(_, let result) = older[0].kind else { return XCTFail("expected a tool item") }
        XCTAssertEqual(result, "a.txt")
        XCTAssertEqual(orphans, ["t0": "b"])
    }

    private func page(_ messages: String, nextBefore: Int?, hasMore: Bool) -> MessagesResponse {
        let json = #"{"session":"claude:1","messages":["# + messages + #"],"next_before":"# + (nextBefore.map(String.init) ?? "null") + #","has_more":"# + (hasMore ? "true" : "false") + "}"
        return try! Hub.decoder.decode(MessagesResponse.self, from: Data(json.utf8))
    }

    func testLiveResultFindsACallFromAnOlderPage() {
        let chat = ChatModel(key: "claude:1", app: AppModel())
        chat.apply(env(#"{"type":"message.appended","seq":10,"data":{"kind":"assistant","text":"working"}}"#))
        // The call is on an older page and still running.
        XCTAssertTrue(chat.prepend(page(#"{"type":"message.appended","seq":5,"data":{"kind":"tool_use","text":"","tool":{"name":"Bash","call_id":"t1","args_summary":"Bash · sleep"}}}"#,
                                        nextBefore: nil, hasMore: false)))
        chat.apply(env(#"{"type":"message.appended","seq":11,"data":{"kind":"tool_result","text":"done","result_ref":"t1"}}"#))
        guard case .tool(_, let result) = chat.items[0].kind else { return XCTFail("expected the call first") }
        XCTAssertEqual(result, "done")
    }

    func testPageOfOnlyOrphanResultsShowsNothingButKeepsPaging() {
        let chat = ChatModel(key: "claude:1", app: AppModel())
        let added = chat.prepend(page(#"{"type":"message.appended","seq":7,"data":{"kind":"tool_result","text":"x","result_ref":"t9"}}"#,
                                      nextBefore: 7, hasMore: true))
        XCTAssertFalse(added)
        XCTAssertTrue(chat.items.isEmpty)
        XCTAssertTrue(chat.hasEarlier)
    }

    func testReopeningAChatReusesItsModel() {
        let app = AppModel()
        let first = app.chat("claude:1")
        first.apply(env(#"{"type":"message.appended","seq":1,"data":{"kind":"assistant","text":"hi"}}"#))
        XCTAssertTrue(app.chat("claude:1") === first)
        XCTAssertEqual(app.chat("claude:1").items.count, 1)
        XCTAssertFalse(app.chat("claude:2") === first)
    }

    func testChatCacheDropsTheLeastRecentlyOpened() {
        let app = AppModel()
        let oldest = app.chat("claude:0")
        for i in 1...AppModel.chatCacheSize { _ = app.chat("claude:\(i)") }
        XCTAssertFalse(app.chat("claude:0") === oldest)
    }
}

final class ToolPresentationTests: XCTestCase {
    func testSummaryDropsRepeatedName() {
        XCTAssertEqual(ToolArgs.summary(ToolCall(name: "Bash", callId: nil, argsSummary: "Bash · ls -la", argsFull: nil)), "ls -la")
        XCTAssertEqual(ToolArgs.summary(ToolCall(name: "Bash", callId: nil, argsSummary: nil, argsFull: nil)), "Bash")
    }

    func testCommandShowsAsPlainText() {
        let call = ToolCall(name: "Bash", callId: nil, argsSummary: nil, argsFull: #"{"command":"echo a\necho b","timeout":5}"#)
        XCTAssertEqual(ToolArgs.readable(call), "echo a\necho b")
    }

    func testRunsOfThreeToolsFold() {
        let tool = { (id: String) in ChatItem(id: id, kind: .tool(ToolCall(name: "Bash", callId: id, argsSummary: nil, argsFull: nil), result: nil)) }
        let items = [tool("1"), tool("2"), ChatItem(id: "t", kind: .assistant("hi")), tool("3"), tool("4"), tool("5")]
        let entries = ChatEntry.fold(items)
        XCTAssertEqual(entries.map(\.id), ["1", "2", "t", "run-3"])
    }
}

final class HarnessIconTests: XCTestCase {
    func testBundledLogosResolveWithAliases() {
        XCTAssertEqual(HarnessIcon.assetName("claude"), "harness-claude")
        XCTAssertEqual(HarnessIcon.assetName("claude-code"), "harness-claude")
        XCTAssertNotNil(HarnessIcon.assetName("devin"))
        XCTAssertNotNil(HarnessIcon.assetName("codex"))
        XCTAssertNil(HarnessIcon.assetName("no-such-harness"))
    }
}

final class ReviewFixTests: XCTestCase {
    func testFoldersThatShortenAlikeStaySeparate() {
        let now = Date().timeIntervalSince1970
        let mk = { (key: String, cwd: String) in
            SessionRow(key: key, harness: "claude", agent: nil, cwd: cwd, title: key, lastActiveAt: now - 60,
                       status: "active", machineId: "m1", ownership: nil, attachable: nil)
        }
        let groups = SessionGrouping.groups([mk("a", "/Users/k/work/a/projects/api"), mk("b", "/Users/k/work/b/projects/api")],
                                            machines: [], now: now)
        XCTAssertEqual(groups.count, 2)
        XCTAssertEqual(groups.map(\.title), ["~/work/a/projects/api", "~/work/b/projects/api"])
    }

    @MainActor
    func testClearingAnOldSessionSticks() {
        UserDefaults.standard.removeObject(forKey: "quiver.inbox.dismissed.v2")
        let d = InboxDismissals()
        let monthAgo = Date().timeIntervalSince1970 - 30 * 86_400
        d.dismiss("claude:old", at: monthAgo)
        XCTAssertTrue(d.isDismissed("claude:old", at: monthAgo))
        XCTAssertTrue(InboxDismissals().isDismissed("claude:old", at: monthAgo), "survives a relaunch")
        XCTAssertFalse(d.isDismissed("claude:old", at: monthAgo + 1), "new activity brings it back")
    }
}

@MainActor
final class SessionSearchTests: XCTestCase {
    private func row(_ key: String, _ title: String, cwd: String = "/w", ago: Double) -> SessionRow {
        SessionRow(key: key, harness: "claude", agent: "Claude Code", cwd: cwd, title: title,
                   lastActiveAt: Date().timeIntervalSince1970 - ago, status: "done", machineId: "m",
                   ownership: nil, attachable: nil)
    }

    func testLocalMatchIsPlainTextNewestFirst() {
        let rows = [row("a", "Fix ledger cursor", ago: 100), row("b", "ledger notes", ago: 5), row("c", "Dark mode", cwd: "/w/ledger(", ago: 1)]
        XCTAssertEqual(SessionSearch.localMatches(rows, query: "LEDGER c").map(\.key), ["a"])
        XCTAssertEqual(SessionSearch.localMatches(rows, query: "ledger").map(\.key), ["c", "b", "a"])
        XCTAssertEqual(SessionSearch.localMatches(rows, query: "ledger(").map(\.key), ["c"], "regex syntax is just text")
        XCTAssertEqual(SessionSearch.localMatches([row("x", String(repeating: "a", count: 40), ago: 1)], query: "(a|aa)+$"), [])
    }

    func testSnippetRunsSplitMarkers() {
        let runs = SessionSearch.snippetRuns("the \u{E000}ledger\u{E001} cursor")
        XCTAssertEqual(runs.map(\.text), ["the ", "ledger", " cursor"])
        XCTAssertEqual(runs.map(\.hit), [false, true, false])
    }

    func testDecodesHubSearchResult() throws {
        let json = #"{"results":[{"key":"claude:1","source":"index","snippet":"a b","marked_snippet":"a b","role":"user","session":{"key":"claude:1","title":"T"}}],"indexed":true}"#
        let body = try Hub.decoder.decode(SearchResponse.self, from: Data(json.utf8))
        XCTAssertEqual(body.results.first?.session.displayTitle, "T")
        XCTAssertEqual(body.indexed, true)
    }
}

final class NewSessionOptionsTests: XCTestCase {
    private func row(_ key: String, _ harness: String, cwd: String, machine: String = "m1", at: Double) -> SessionRow {
        SessionRow(key: key, harness: harness, agent: nil, cwd: cwd, title: nil, lastActiveAt: at,
                   status: nil, machineId: machine, ownership: nil, attachable: nil)
    }

    private func machine(_ id: String, name: String, online: Bool = true, harnesses: [String]? = nil, launchable: [String]? = nil) -> Machine {
        Machine(id: id, name: name, os: nil, online: online, lastSeenAt: nil, harnesses: harnesses, launchable: launchable)
    }

    func testFoldersAreDistinctNewestFirstPerMachine() {
        let rows = [row("a", "claude", cwd: "/p/old", at: 1), row("b", "codex", cwd: "/p/new", at: 3),
                    row("c", "claude", cwd: "/p/old", at: 2), row("d", "claude", cwd: "/other", machine: "m2", at: 9),
                    row("e", "claude", cwd: "", at: 5)]
        XCTAssertEqual(NewSessionOptions.folders(on: "m1", sessions: rows), ["/p/new", "/p/old"])
    }

    func testHarnessesKeepTheMachinesOrderAndFallBack() {
        let m = machine("m1", name: "mac", launchable: ["qwen-code", "claude", "kilo"])
        XCTAssertEqual(NewSessionOptions.harnesses(on: m), ["qwen-code", "claude", "kilo"])
        // A row older than `launchable` offers what it detected; the hub
        // refuses any it can't start.
        let old = machine("m1", name: "mac", harnesses: ["claude", "gemini"])
        XCTAssertEqual(NewSessionOptions.harnesses(on: old), ["claude", "gemini"])
    }

    func testMachinesDropThoseThatCanLaunchNothingOnlineFirst() {
        let ms = [machine("z", name: "zeta", launchable: ["claude"]),
                  machine("o", name: "alpha", online: false, launchable: ["claude"]),
                  machine("n", name: "none", launchable: []),
                  machine("b", name: "beta.local", launchable: ["codex"])]
        XCTAssertEqual(NewSessionOptions.machines(ms).map(\.id), ["b", "z", "o"])
    }

    func testFolderSearchMatchesEveryWordAnyCase() {
        let folders = ["/Users/k/Projects/quiver", "/Users/k/Projects/quiver-render", "/Users/k/Indie/kopifund"]
        XCTAssertEqual(NewSessionOptions.matching(folders, query: "QUIVER rend"), ["/Users/k/Projects/quiver-render"])
        XCTAssertEqual(NewSessionOptions.matching(folders, query: "  "), folders)
        XCTAssertEqual(NewSessionOptions.matching(folders, query: "nothing"), [])
    }

    func testHarnessNamesComeFromTheMachineCapitalised() {
        var m = machine("m1", name: "mac", launchable: ["devin", "kilo"])
        m.harnessNames = ["devin": "devin", "kilo": "Kilo"]
        XCTAssertEqual(NewSessionOptions.name("devin", on: m, sessions: []), "Devin")
        XCTAssertEqual(NewSessionOptions.name("kilo", on: m, sessions: []), "Kilo")
        XCTAssertEqual(NewSessionOptions.name("pi", on: nil, sessions: []), "Pi")
    }

    func testDefaultHarnessIsTheFoldersLastOneWhenOffered() {
        let rows = [row("a", "codex", cwd: "/p", at: 5), row("b", "claude", cwd: "/q", at: 9)]
        XCTAssertEqual(NewSessionOptions.defaultHarness(folder: "/p", machineId: "m1", offered: ["claude", "codex"], sessions: rows), "codex")
        XCTAssertEqual(NewSessionOptions.defaultHarness(folder: "/p", machineId: "m1", offered: ["claude"], sessions: rows), "claude")
        XCTAssertNil(NewSessionOptions.defaultHarness(folder: "/p", machineId: "m1", offered: [], sessions: rows))
    }

    func testDecodesHubLaunchAndMachineRows() throws {
        let launch = try Hub.decoder.decode(LaunchStatus.self, from: Data(
            #"{"ok":true,"launch_id":"l1a2","state":"pending","queued":true,"note":"queued for satellite"}"#.utf8))
        XCTAssertEqual(launch.launchId, "l1a2")
        XCTAssertEqual(launch.queued, true)
        let m = try Hub.decoder.decode(Machine.self, from: Data(
            #"{"id":"m","name":"mac","harnesses":["claude","gemini"],"launchable":["claude"],"harness_names":{"claude":"Claude Code"}}"#.utf8))
        XCTAssertEqual(m.launchable, ["claude"])
        XCTAssertEqual(m.harnessNames?["claude"], "Claude Code")
        let listing = try Hub.decoder.decode(FolderListing.self, from: Data(
            #"{"path":"/Users/k","parent":"/Users","folders":[{"name":"Projects","path":"/Users/k/Projects"}],"truncated":false}"#.utf8))
        XCTAssertEqual(listing.folders.map(\.name), ["Projects"])
    }
}

@MainActor
final class LaunchFlowTests: XCTestCase {
    private func status(_ state: String, key: String? = nil, note: String? = nil, queued: Bool? = nil) -> LaunchStatus {
        LaunchStatus(launchId: "l1", state: state, key: key, note: note, queued: queued)
    }

    func testPollsUntilStarted() async {
        let flow = LaunchFlow()
        var answers = [status("pending"), status("started", key: "claude:new")]
        var asked: [String] = []
        let key = await flow.run(post: { self.status("pending") },
                                 fetch: { asked.append($0); return answers.removeFirst() },
                                 interval: .zero)
        XCTAssertEqual(key, "claude:new")
        XCTAssertEqual(asked, ["l1", "l1"])
        XCTAssertEqual(flow.phase, .started("claude:new"))
    }

    func testTheLastPollIsStillRead() async {
        let flow = LaunchFlow()
        let key = await flow.run(post: { self.status("pending") },
                                 fetch: { _ in self.status("started", key: "claude:late") },
                                 attempts: 1, interval: .zero)
        XCTAssertEqual(key, "claude:late")
    }

    func testCancelledLaunchStopsWithoutAKey() async {
        let flow = LaunchFlow()
        let task = Task { await flow.run(post: { self.status("pending") },
                                         fetch: { _ in self.status("pending") },
                                         interval: .seconds(30)) }
        task.cancel()
        let key = await task.value
        XCTAssertNil(key)
        XCTAssertEqual(flow.phase, .idle)
    }

    func testUntrackedLaunchSaysThereIsNoChat() async {
        let flow = LaunchFlow()
        let key = await flow.run(post: { self.status("pending") },
                                 fetch: { _ in self.status("untracked") },
                                 interval: .zero)
        XCTAssertNil(key)
        guard case .unconfirmed(let message) = flow.phase else { return XCTFail("expected unconfirmed") }
        XCTAssertTrue(message.contains("no chat"))
    }

    func testFailedLaunchShowsTheHubsNote() async {
        let flow = LaunchFlow()
        let key = await flow.run(post: { self.status("pending") },
                                 fetch: { _ in self.status("failed", note: "no such folder on this machine: /x") },
                                 interval: .zero)
        XCTAssertNil(key)
        XCTAssertEqual(flow.phase, .failed("The session didn't start: no such folder on this machine: /x"))
    }

    func testRejectedPostAndLostLaunch() async {
        let flow = LaunchFlow()
        var key = await flow.run(post: { throw HubError(status: 422, code: "bad_cwd", message: "That folder doesn't exist on this machine.") },
                                 fetch: { _ in self.status("pending") }, interval: .zero)
        XCTAssertNil(key)
        XCTAssertEqual(flow.phase, .failed("That folder doesn't exist on this machine."))
        key = await flow.run(post: { self.status("pending") },
                             fetch: { _ in throw HubError(status: 404, code: "unknown_launch", message: "lost") },
                             interval: .zero)
        XCTAssertNil(key)
        XCTAssertEqual(flow.phase, .failed("lost"))
    }

    func testTimeoutWordingSaysWhetherItWasQueued() async {
        let flow = LaunchFlow()
        var fetches = 0
        _ = await flow.run(post: { self.status("pending", queued: true) },
                           fetch: { _ in fetches += 1; throw HubError.network(URLError(.timedOut)) },
                           attempts: 3, interval: .zero)
        XCTAssertEqual(fetches, 3)
        guard case .unconfirmed(let message) = flow.phase else { return XCTFail("expected unconfirmed") }
        XCTAssertTrue(message.hasPrefix("Queued."))
    }
}
