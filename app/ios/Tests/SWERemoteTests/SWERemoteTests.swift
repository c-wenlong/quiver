import SwiftUI
import XCTest
@testable import SWERemote

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

final class EventStreamTests: XCTestCase {
    func testOnlyARefusalNoRetryCanChangeEndsTheStream() {
        XCTAssertTrue(EventStream.ends(status: 403, notFoundInARow: 0))
        // A restarted hub answers 404 until its catalog scan lands.
        for n in 1..<EventStream.notFoundLimit {
            XCTAssertFalse(EventStream.ends(status: 404, notFoundInARow: n), "404 number \(n) may be a restart")
        }
        XCTAssertTrue(EventStream.ends(status: 404, notFoundInARow: EventStream.notFoundLimit),
                      "unknown_session that long: the hub dropped it")
        for status in [0, 500, 502, 503, 504] {
            XCTAssertFalse(EventStream.ends(status: status, notFoundInARow: 99), "\(status) is worth reconnecting for")
        }
    }
}

final class PairLinkTests: XCTestCase {
    func testSWEScheme() {
        let link = PairLink(URL(string: "swe://pair?host=100.64.0.5&port=9000&token=abc&name=phone")!)
        XCTAssertEqual(link?.baseURL.absoluteString, "http://100.64.0.5:9000")
        XCTAssertEqual(link?.token, "abc")
        XCTAssertEqual(link?.name, "phone")
    }

    /// Links a hub from before the rename still emits.
    func testLegacyQuiverScheme() {
        let link = PairLink(URL(string: "quiver://pair?host=100.64.0.5&port=9000&token=abc&name=phone")!)
        XCTAssertEqual(link?.baseURL.absoluteString, "http://100.64.0.5:9000")
        XCTAssertEqual(link?.token, "abc")
        XCTAssertEqual(link?.name, "phone")
    }

    func testDefaultPortAndMissingToken() {
        XCTAssertEqual(PairLink(URL(string: "swe://pair?host=mac&token=t")!)?.baseURL.absoluteString, "http://mac:8477")
        XCTAssertNil(PairLink(URL(string: "swe://pair?host=mac")!))
        XCTAssertEqual(
            PairLink(URL(string: "swe://pair?host=box.tailnet.ts.net&port=8477&token=t&scheme=https")!)?.baseURL.absoluteString,
            "https://box.tailnet.ts.net:8477")
        // Only https upgrades; anything else stays plain http.
        XCTAssertEqual(
            PairLink(URL(string: "swe://pair?host=mac&token=t&scheme=ftp")!)?.baseURL.absoluteString,
            "http://mac:8477")
        XCTAssertNil(PairLink(URL(string: "https://example.com/")!))
    }

    func testBrowserFragmentForm() {
        let link = PairLink(URL(string: "http://mac.ts.net:8477/#pair=tok&name=ipad")!)
        XCTAssertEqual(link?.baseURL.absoluteString, "http://mac.ts.net:8477")
        XCTAssertEqual(link?.token, "tok")
        XCTAssertEqual(link?.name, "ipad")
        // Tickets minted while the web app lived at `/app/` keep parsing.
        let legacy = PairLink(URL(string: "http://mac.ts.net:8477/app/#pair=tok")!)
        XCTAssertEqual(legacy?.baseURL.absoluteString, "http://mac.ts.net:8477")
        XCTAssertEqual(legacy?.token, "tok")
    }

    func testHubURLFromTypedInput() {
        XCTAssertEqual(hubURL(from: " 100.64.1.2 ")?.absoluteString, "http://100.64.1.2:8477")
        XCTAssertEqual(hubURL(from: "mac:9000")?.absoluteString, "http://mac:9000")
        XCTAssertEqual(hubURL(from: "http://mac:8477/#approve")?.absoluteString, "http://mac:8477")
        XCTAssertNil(hubURL(from: "   "))
        // https with no port is `tailscale serve` on 443, not the hub's 8477.
        XCTAssertEqual(hubURL(from: "https://mac.tailnet.ts.net")?.absoluteString, "https://mac.tailnet.ts.net")
        XCTAssertEqual(hubURL(from: "https://mac.tailnet.ts.net:8443/")?.absoluteString, "https://mac.tailnet.ts.net:8443")
    }

    func testHttpsFragmentLinkWithoutAPortIs443() {
        XCTAssertEqual(PairLink(URL(string: "https://mac.tailnet.ts.net/#pair=tok")!)?.baseURL.absoluteString,
                       "https://mac.tailnet.ts.net")
        XCTAssertEqual(PairLink(URL(string: "http://mac.ts.net/app/#pair=tok")!)?.baseURL.absoluteString,
                       "http://mac.ts.net:8477")
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

    func testCodexPatchArrivesAsAJSONString() {
        // Codex's apply_patch input is the patch text, so args_full is a
        // JSON string rather than an object.
        let patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-old\n+new\n*** End Patch"
        let full = String(decoding: try! JSONSerialization.data(withJSONObject: patch, options: [.fragmentsAllowed]), as: UTF8.self)
        let diff = ToolDiff.from(ToolCall(name: "apply_patch", callId: nil, argsSummary: nil, argsFull: full))
        XCTAssertEqual(diff?.file, "src/a.py")
        XCTAssertEqual(diff?.added, 1)
        XCTAssertEqual(diff?.removed, 1)
        XCTAssertNil(ToolDiff.from(ToolCall(name: "apply_patch", callId: nil, argsSummary: nil, argsFull: #""""#)))
    }

    func testPatchUnderAnInputKeyNamesItsFile() {
        let tool = ToolCall(name: "apply_patch", callId: nil, argsSummary: nil,
                            argsFull: #"{"input":"*** Add File: n.txt\n+hi"}"#)
        XCTAssertEqual(ToolDiff.from(tool)?.file, "n.txt")
        XCTAssertEqual(ToolDiff.from(tool)?.added, 1)
    }

    func testMultiEditSeparatesOnlyTheEditsShown() {
        let tool = ToolCall(name: "MultiEdit", callId: nil, argsSummary: nil,
                            argsFull: #"{"file_path":"a","edits":[{"replace_all":true},{"old_string":"x","new_string":"y"},{"old_string":"p","new_string":"q"}]}"#)
        let kinds = ToolDiff.from(tool)?.lines.map(\.kind)
        XCTAssertEqual(kinds, [.removed, .added, .meta, .removed, .added], "no separator before the first edit shown")
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
        let s = RichText.inline("Read [AGENTS.md](/Users/k/.swe/AGENTS.md), [ai.py](finance/ai.py) and [PR](https://github.com/x/y/pull/1).")
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
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
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
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
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
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"message.appended","seq":10,"data":{"kind":"assistant","text":"working"}}"#))
        // The call is on an older page and still running.
        XCTAssertTrue(chat.prepend(page(#"{"type":"message.appended","seq":5,"data":{"kind":"tool_use","text":"","tool":{"name":"Bash","call_id":"t1","args_summary":"Bash · sleep"}}}"#,
                                        nextBefore: nil, hasMore: false)))
        chat.apply(env(#"{"type":"message.appended","seq":11,"data":{"kind":"tool_result","text":"done","result_ref":"t1"}}"#))
        guard case .tool(_, let result) = chat.items[0].kind else { return XCTFail("expected the call first") }
        XCTAssertEqual(result, "done")
    }

    func testPageOfOnlyOrphanResultsShowsNothingButKeepsPaging() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        let added = chat.prepend(page(#"{"type":"message.appended","seq":7,"data":{"kind":"tool_result","text":"x","result_ref":"t9"}}"#,
                                      nextBefore: 7, hasMore: true))
        XCTAssertFalse(added)
        XCTAssertTrue(chat.items.isEmpty)
        XCTAssertTrue(chat.hasEarlier)
    }

    func testAPageWhoseCursorDidntMoveBackEndsHistory() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        // Asked before 7, answered "more before 7": asking again would
        // get the same page forever.
        let added = chat.prepend(page(#"{"type":"message.appended","seq":7,"data":{"kind":"tool_result","text":"x","result_ref":"t9"}}"#,
                                      nextBefore: 7, hasMore: true), askedBefore: 7)
        XCTAssertFalse(added)
        XCTAssertFalse(chat.hasEarlier, "loadEarlier's loop stops here")
        // A cursor that moved back keeps paging.
        let next = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        _ = next.prepend(page(#"{"type":"message.appended","seq":7,"data":{"kind":"tool_result","text":"x","result_ref":"t9"}}"#,
                              nextBefore: 7, hasMore: true), askedBefore: 20)
        XCTAssertTrue(next.hasEarlier)
    }

    private func say(_ seq: Int) -> Envelope {
        env(#"{"type":"message.appended","seq":"# + String(seq) + #","data":{"kind":"assistant","text":"m"# + String(seq) + #""}}"#)
    }

    func testRevealDelayShortensWithBacklogAndReplaysAtOnce() {
        XCTAssertEqual(ChatModel.revealDelay(waiting: 1), .milliseconds(180))
        XCTAssertEqual(ChatModel.revealDelay(waiting: 12), .milliseconds(100))
        XCTAssertNil(ChatModel.revealDelay(waiting: 25))
        XCTAssertNil(ChatModel.revealDelay(waiting: 0))
    }

    func testABurstIsRevealedOneAtATime() async throws {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        for seq in 1...3 { chat.enqueue(say(seq)) }
        XCTAssertTrue(chat.items.isEmpty)
        XCTAssertTrue(chat.working)
        var counts: [Int] = []
        for _ in 0..<200 where chat.items.count < 3 {
            if counts.last != chat.items.count { counts.append(chat.items.count) }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertEqual(chat.items.map(\.id), ["m1", "m2", "m3"])
        XCTAssertTrue(counts.contains(1) && counts.contains(2), "saw \(counts)")
        XCTAssertFalse(chat.working)
    }

    /// Holds the reveal's pauses until the test lets them go. On the main
    /// actor like the chat, so a released pause resumes within `settle`'s
    /// yields instead of waiting on a hop back from the thread pool.
    @MainActor private final class Gate {
        var waits: [Duration] = []
        private var held: [CheckedContinuation<Void, Never>] = []
        func pause(_ d: Duration) async {
            waits.append(d)
            await withCheckedContinuation { held.append($0) }
        }
        func release() { held.forEach { $0.resume() }; held = [] }
    }

    private func settle(_ until: () -> Bool) async {
        for _ in 0..<200 where !until() { await Task.yield() }
    }

    func testAnEventJustAfterOneWasShownStillWaitsItsTurn() async {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        let gate = Gate()
        chat.pause = gate.pause
        chat.enqueue(say(1))
        await settle { chat.items.count == 1 }
        XCTAssertTrue(gate.waits.isEmpty)
        // The queue is empty again when the next event arrives.
        chat.enqueue(say(2))
        await settle { !gate.waits.isEmpty }
        XCTAssertEqual(chat.items.count, 1)
        XCTAssertGreaterThan(gate.waits.first ?? .zero, .zero)
        gate.release()
        await settle { chat.items.count == 2 }
        XCTAssertEqual(chat.items.map(\.id), ["m1", "m2"])
    }

    func testAResultJustAfterItsCallLandsWithoutWaiting() async {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        let gate = Gate()
        chat.pause = gate.pause
        chat.enqueue(env(#"{"type":"message.appended","seq":1,"data":{"kind":"tool_use","text":"","tool":{"name":"Bash","call_id":"t1","args_summary":"Bash · ls"}}}"#))
        await settle { chat.items.count == 1 }
        chat.enqueue(env(#"{"type":"message.appended","seq":2,"data":{"kind":"tool_result","text":"a.txt","result_ref":"t1"}}"#))
        await settle { if case .tool(_, "a.txt"?) = chat.items.first?.kind { true } else { false } }
        guard case .tool(_, let result) = chat.items.first?.kind else { return XCTFail("expected the call") }
        XCTAssertEqual(result, "a.txt")
        XCTAssertTrue(gate.waits.isEmpty)
    }

    func testAReplayAfterReconnectingLandsAtOnce() async throws {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        for seq in 1...30 { chat.enqueue(say(seq)) }
        await Task.yield()
        XCTAssertEqual(chat.items.count, 30)
    }

    func testLeavingShowsWhatsStillWaiting() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        for seq in 1...3 { chat.enqueue(say(seq)) }
        chat.stop()
        XCTAssertEqual(chat.items.count, 3)
        XCTAssertFalse(chat.working)
    }

    private func result(_ json: String) -> CommandResult {
        try! Hub.decoder.decode(CommandResult.self, from: Data(json.utf8))
    }

    func testADeliveredSendStopsSayingSending() {
        let ok = result(#"{"op":"message","ok":true,"note":"sent to tmux pane","client_id":"c1"}"#)
        // A slash command like /rename never comes back as a user message,
        // so the result alone has to settle the bubble.
        XCTAssertEqual(SendState.settled(ok, current: .sending, agentActive: false), .delivered(nil))
        XCTAssertEqual(SendState.settled(ok, current: .sending, agentActive: true),
                       .delivered("Delivered. The agent reads it at its next step"))
        let failed = result(#"{"op":"message","ok":false,"note":"pane unreachable","client_id":"c1"}"#)
        XCTAssertEqual(SendState.settled(failed, current: .queued("Queued until the agent is idle"), agentActive: false),
                       .failed("pane unreachable"))
        // Once settled, a late result doesn't reopen it.
        XCTAssertNil(SendState.settled(failed, current: .delivered(nil), agentActive: false))
        XCTAssertNil(SendState.settled(ok, current: .failed("x"), agentActive: false))
    }

    func testSendButtonStopsTheAgentOnlyWithNothingTyped() {
        XCTAssertEqual(ChatModel.composerAction(draft: "hi", agentWorking: true), .send)
        XCTAssertEqual(ChatModel.composerAction(draft: "hi", agentWorking: false), .send)
        XCTAssertEqual(ChatModel.composerAction(draft: " \n", agentWorking: true), .stop)
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: false), .disabled)
    }

    func testStopIsOfferedWhileTheAgentWorksOrAReplyIsDue() {
        XCTAssertTrue(ChatModel.canInterrupt(status: .active, awaitingReply: false))
        XCTAssertTrue(ChatModel.canInterrupt(status: .idle, awaitingReply: true))
        XCTAssertFalse(ChatModel.canInterrupt(status: .idle, awaitingReply: false))
        XCTAssertFalse(ChatModel.canInterrupt(status: nil, awaitingReply: false))
        XCTAssertFalse(ChatModel.canInterrupt(status: .done, awaitingReply: false))
    }

    func testMessagesStillBeingRevealedDontOfferStop() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        for seq in 1...3 { chat.enqueue(say(seq)) }
        XCTAssertTrue(chat.working)
        XCTAssertFalse(chat.canInterrupt)
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: chat.canInterrupt), .disabled)
    }

    func testReopeningAChatReusesItsModel() {
        let app = AppModel(hub: nil)
        let first = app.chat("claude:1")
        first.apply(env(#"{"type":"message.appended","seq":1,"data":{"kind":"assistant","text":"hi"}}"#))
        XCTAssertTrue(app.chat("claude:1") === first)
        XCTAssertEqual(app.chat("claude:1").items.count, 1)
        XCTAssertFalse(app.chat("claude:2") === first)
    }

    // MARK: Queued tray

    private func user(_ seq: Int, _ text: String) -> Envelope {
        env(#"{"type":"message.appended","seq":"# + String(seq) + #","data":{"kind":"user","text":""# + text + #""}}"#)
    }

    private func commandResult(_ json: String) -> Envelope {
        env(#"{"type":"command.result","seq":50,"data":"# + json + "}")
    }

    func testASendWhileTheAgentWorksWaitsInTheTrayNotTheTranscript() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("run the tests", clientId: "c1", toTray: true)
        XCTAssertTrue(chat.items.isEmpty)
        XCTAssertEqual(chat.tray.map(\.clientId), ["c1"])
        XCTAssertEqual(chat.tray.first?.phase, .posting)
        chat.posted("c1", waiting: true)
        XCTAssertEqual(chat.tray.first?.phase, .waiting)
        XCTAssertTrue(chat.items.isEmpty)
        XCTAssertFalse(chat.awaitingReply)
    }

    func testAnIdleSendGoesStraightToTheTranscript() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("hello", clientId: "c1", toTray: false)
        XCTAssertTrue(chat.queued.isEmpty)
        XCTAssertEqual(chat.items.map(\.id), ["send-c1"])
        XCTAssertTrue(chat.awaitingReply)
    }

    func testTheHubDecidesWhetherASendWaits() {
        // The phone thought the agent was busy; the hub sent it at once.
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("one", clientId: "c1", toTray: true)
        chat.posted("c1", waiting: false)
        XCTAssertTrue(chat.queued.isEmpty)
        XCTAssertEqual(chat.items.first?.kind, .user(text: "one", send: .sending))
        // The phone thought it idle; the hub holds it.
        chat.stage("two", clientId: "c2", toTray: false)
        chat.posted("c2", waiting: true)
        XCTAssertEqual(chat.tray.map(\.clientId), ["c2"])
        XCTAssertEqual(chat.items.map(\.id), ["send-c1"])
    }

    func testTheEchoMovesAQueuedSendIntoTheTranscript() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"message.appended","seq":1,"data":{"kind":"assistant","text":"working on it"}}"#))
        chat.stage("and update the docs", clientId: "c1", toTray: true)
        chat.posted("c1", waiting: true)
        chat.apply(user(2, "and update the docs"))
        XCTAssertTrue(chat.queued.isEmpty)
        XCTAssertEqual(chat.items.map(\.id), ["m1", "m2"])
        XCTAssertEqual(chat.items.last?.kind, .user(text: "and update the docs", send: nil))
        // Its result after the echo shows nothing more.
        chat.apply(commandResult(#"{"op":"message","ok":true,"client_id":"c1"}"#))
        XCTAssertEqual(chat.items.count, 2)
    }

    func testItsResultMovesAQueuedSendIntoTheTranscript() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("/rename tray", clientId: "c1", toTray: true)
        chat.posted("c1", waiting: true)
        chat.apply(commandResult(#"{"op":"message","ok":true,"note":"sent to tmux pane","client_id":"c1"}"#))
        XCTAssertTrue(chat.queued.isEmpty)
        XCTAssertEqual(chat.items.first?.kind, .user(text: "/rename tray", send: .delivered(nil)))
    }

    func testAnOlderPageIsNotTakenForAnEcho() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("continue", clientId: "c1", toTray: true)
        chat.posted("c1", waiting: true)
        XCTAssertTrue(chat.prepend(page(#"{"type":"message.appended","seq":3,"data":{"kind":"user","text":"continue"}}"#,
                                        nextBefore: nil, hasMore: false)))
        XCTAssertEqual(chat.tray.map(\.clientId), ["c1"])
    }

    func testARemovedSendLeavesWithoutAFailure() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("never mind", clientId: "c1", toTray: true)
        chat.posted("c1", waiting: true)
        chat.markRemoving("c1")
        XCTAssertTrue(chat.tray.isEmpty)
        chat.apply(commandResult(#"{"op":"message","ok":false,"note":"removed before sending","client_id":"c1","cancelled":true}"#))
        XCTAssertTrue(chat.queued.isEmpty)
        XCTAssertTrue(chat.items.isEmpty)
        // Removed from another device: same.
        chat.apply(commandResult(#"{"op":"message","ok":false,"note":"removed before sending","client_id":"c9","cancelled":true}"#))
        XCTAssertTrue(chat.items.isEmpty)
        XCTAssertEqual(result(#"{"op":"message","ok":false,"cancelled":true}"#).cancelled, true)
    }

    func testARefusedRemoveComesBackWithANote() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("one", clientId: "c1", toTray: true)
        chat.stage("two", clientId: "c2", toTray: true)
        chat.posted("c1", waiting: true)
        chat.posted("c2", waiting: true)
        chat.markRemoving("c1")
        chat.removeFailed("c1", HubError(status: 0, code: "network", message: "x"))
        XCTAssertEqual(chat.tray.first, QueuedSend(clientId: "c1", text: "one", phase: .waiting, note: "Can't reach the hub"))
        // Already went out: it waits for its echo, with nothing to send now.
        chat.markRemoving("c2")
        chat.removeFailed("c2", HubError(status: 409, code: "not_waiting", message: "that message already went out"))
        XCTAssertEqual(chat.tray.last?.phase, .left)
        XCTAssertEqual(chat.tray.last?.note, "Already sent")
        // Unknown to the hub: nothing to remove, so it goes.
        chat.markRemoving("c1")
        chat.removeFailed("c1", HubError(status: 404, code: "unknown_send", message: "x"))
        XCTAssertEqual(chat.tray.map(\.clientId), ["c2"])
    }

    func testSendNowShowsSendingAndRefusalsBecomeShortNotes() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("ship it", clientId: "c1", toTray: true)
        XCTAssertFalse(chat.markSendingNow("c1"), "the hub can't act on it before it answers the POST")
        chat.posted("c1", waiting: true)
        XCTAssertTrue(chat.markSendingNow("c1"))
        XCTAssertEqual(chat.tray.first?.phase, .sendingNow)
        chat.steerFailed("c1", HubError(status: 409, code: "not_attached", message: "x"))
        XCTAssertEqual(chat.tray.first?.phase, .waiting)
        XCTAssertEqual(chat.tray.first?.note, "Attach to send now")
        XCTAssertTrue(chat.markSendingNow("c1"))
        XCTAssertNil(chat.tray.first?.note)
        chat.steerFailed("c1", HubError(status: 409, code: "not_waiting", message: "that message already went out"))
        XCTAssertEqual(chat.tray.first?.phase, .left)

        func note(_ code: String, _ message: String = "") -> String {
            ChatModel.queueNote(HubError(status: 409, code: code, message: message))
        }
        XCTAssertEqual(note("not_waiting", "that message already went out"), "Already sent")
        XCTAssertEqual(note("not_waiting", "that message was already sent now"), "Already sent")
        XCTAssertEqual(note("not_waiting", "that message was already removed"), "Already removed")
        XCTAssertEqual(note("not_waiting", "that message already failed"), "Already failed")
        XCTAssertEqual(note("not_attached"), "Attach to send now")
        XCTAssertEqual(note("queue_unsupported"), "Not available on this machine")
        XCTAssertEqual(note("backlog_full"), "Too many waiting. Try again shortly")
        XCTAssertEqual(note("unknown_send"), "The hub lost this message")
    }

    func testTheHubsQueueListMergesByClientId() throws {
        let body = try Hub.decoder.decode(QueueResponse.self, from: Data(#"""
            {"sends":[{"client_id":"a","text":"from before","attachments":[],"queued_at":1.5},
                      {"client_id":"b","text":"mine","attachments":["r1"],"queued_at":2}]}
            """#.utf8))
        XCTAssertEqual(body.sends.map(\.clientId), ["a", "b"])
        let local = [
            QueuedSend(clientId: "b", text: "mine", phase: .sendingNow),
            QueuedSend(clientId: "gone", text: "went out while away", phase: .waiting),
            QueuedSend(clientId: "new", text: "posting", phase: .posting),
        ]
        let merged = ChatModel.mergeQueue(local: local, hub: body.sends, settled: [])
        XCTAssertEqual(merged.queued.map(\.clientId), ["a", "b", "new"])
        XCTAssertEqual(merged.queued[0], QueuedSend(clientId: "a", text: "from before", phase: .waiting))
        XCTAssertEqual(merged.queued[1].phase, .sendingNow)
        // What the hub no longer holds leaves the tray but is kept for its result.
        XCTAssertEqual(merged.departed.map(\.clientId), ["gone"])
        // A send whose echo or result was already seen (while history
        // loaded, say) is neither restored nor kept.
        let stale = ChatModel.mergeQueue(local: [QueuedSend(clientId: "b", text: "mine", phase: .waiting)],
                                         hub: body.sends, settled: ["a", "b"])
        XCTAssertTrue(stale.queued.isEmpty)
        XCTAssertTrue(stale.departed.isEmpty)

        // On the model: a send already in the transcript isn't added twice.
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("mine", clientId: "b", toTray: false)
        chat.mergeQueue(body.sends)
        XCTAssertEqual(chat.tray.map(\.clientId), ["a"])
    }

    func testOnlyTheSameTextOrAnAttachmentPrefixIsAnEcho() {
        XCTAssertTrue(ChatModel.echoes("run tests", "run tests"))
        XCTAssertTrue(ChatModel.echoes("@/tmp/a.png run tests", "run tests"))
        XCTAssertTrue(ChatModel.echoes("@/tmp/b.txt @/tmp/a.png run tests", "run tests"))
        XCTAssertFalse(ChatModel.echoes("please run tests", "run tests"))
        XCTAssertFalse(ChatModel.echoes("@/tmp/a.png please run tests", "run tests"))
        XCTAssertFalse(ChatModel.echoes("@ run tests", "run tests"))
        XCTAssertFalse(ChatModel.echoes("  run tests", "run tests"))

        // Another device's message ending in the same words leaves the row.
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("run tests", clientId: "c1", toTray: true)
        chat.posted("c1", waiting: true)
        chat.apply(user(1, "please run tests"))
        XCTAssertEqual(chat.tray.map(\.clientId), ["c1"])
        XCTAssertEqual(chat.items.count, 1)
    }

    func testSatelliteSessionsSteerMidTurnInsteadOfQueueing() {
        XCTAssertEqual(ChatModel.route(agentActive: true, holdsSends: true), .init(toTray: true, mode: "queue"))
        XCTAssertEqual(ChatModel.route(agentActive: true, holdsSends: false), .init(toTray: false, mode: "steer"))
        XCTAssertEqual(ChatModel.route(agentActive: false, holdsSends: true), .init(toTray: false, mode: "queue"))
        XCTAssertEqual(ChatModel.route(agentActive: false, holdsSends: false), .init(toTray: false, mode: "queue"))
        let row = try! Hub.decoder.decode(SessionRow.self, from: Data(#"{"key":"claude:1@mini","status":"active","remote":true}"#.utf8))
        XCTAssertFalse(row.holdsQueuedSends)
        let local = try! Hub.decoder.decode(SessionRow.self, from: Data(#"{"key":"claude:1","status":"active"}"#.utf8))
        XCTAssertTrue(local.holdsQueuedSends)
    }

    func testASendThatLeftWhileAwayStillShowsItsReplayedResult() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.stage("/compact", clientId: "c1", toTray: true)
        chat.stage("/rename later", clientId: "c2", toTray: true)
        chat.posted("c1", waiting: true)
        chat.posted("c2", waiting: true)
        // Reopened: the hub no longer holds either.
        chat.mergeQueue([])
        XCTAssertTrue(chat.tray.isEmpty)
        XCTAssertTrue(chat.items.isEmpty)
        // The replay brings their results.
        chat.apply(commandResult(#"{"op":"message","ok":true,"note":"sent to tmux pane","client_id":"c1"}"#))
        chat.apply(commandResult(#"{"op":"message","ok":false,"note":"session stayed busy for 30 min; message not sent","client_id":"c2"}"#))
        XCTAssertEqual(chat.items.map(\.kind), [
            .user(text: "/compact", send: .delivered(nil)),
            .user(text: "/rename later", send: .failed("session stayed busy for 30 min; message not sent")),
        ])
    }

    func testANotWaitingRefusalKeepsTheHubsReason() {
        func note(_ reason: String) -> String {
            let body = Data(#"{"error":{"code":"not_waiting","message":""#.utf8) + Data(reason.utf8) + Data(#""}}"#.utf8)
            let error = HubClient.error(status: 409, body: body)
            XCTAssertEqual(error.code, "not_waiting")
            return ChatModel.queueNote(error)
        }
        XCTAssertEqual(note("that message already went out"), "Already sent")
        XCTAssertEqual(note("that message was already sent now"), "Already sent")
        XCTAssertEqual(note("that message was already removed"), "Already removed")
        XCTAssertEqual(note("that message already failed"), "Already failed")
    }

    private func queuedChat(_ texts: [String]) -> ChatModel {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        for (i, text) in texts.enumerated() {
            chat.stage(text, clientId: "c\(i + 1)", toTray: true)
            chat.posted("c\(i + 1)", waiting: true)
        }
        return chat
    }

    func testEditPutsTheTextInThePromptBoxAndCancelPutsTheDraftBack() {
        let chat = queuedChat(["lint", "deploy"])
        chat.draft = "half typed"
        XCTAssertTrue(chat.beginEdit("c2"))
        XCTAssertEqual(chat.editingId, "c2")
        XCTAssertEqual(chat.draft, "deploy")
        // Switching rows keeps the original draft to come back to.
        XCTAssertTrue(chat.beginEdit("c1"))
        XCTAssertEqual(chat.draft, "lint")
        XCTAssertEqual(ChatModel.composerAction(draft: "", agentWorking: true, editing: true), .disabled)
        XCTAssertEqual(ChatModel.composerAction(draft: "lint", agentWorking: true, editing: true), .send)
        chat.cancelEdit()
        XCTAssertNil(chat.editingId)
        XCTAssertEqual(chat.draft, "half typed")
        // Only a send the hub still holds can be edited.
        XCTAssertTrue(chat.markSendingNow("c1"))
        XCTAssertFalse(chat.beginEdit("c1"))
        chat.stage("pending", clientId: "c9", toTray: true)
        XCTAssertFalse(chat.beginEdit("c9"))
    }

    func testASavedEditUpdatesTheRowInPlace() {
        let chat = queuedChat(["lint", "deploy"])
        chat.draft = "typing"
        chat.beginEdit("c1")
        chat.draft = "lint --fix"
        chat.editSaved("c1", text: "lint --fix")
        XCTAssertEqual(chat.tray.map(\.text), ["lint --fix", "deploy"])
        XCTAssertNil(chat.editingId)
        XCTAssertEqual(chat.draft, "typing")
        // Its echo is the edited text.
        chat.apply(user(1, "lint --fix"))
        XCTAssertEqual(chat.tray.map(\.text), ["deploy"])
    }

    func testWhatARefusedEditMeans() {
        func outcome(_ status: Int, _ json: String) -> ChatModel.EditOutcome {
            ChatModel.editOutcome(HubClient.error(status: status, body: Data(json.utf8)))
        }
        // An older hub doesn't know the op: remove and queue again.
        XCTAssertEqual(outcome(422, #"{"error":{"code":"bad_command","message":"unknown op 'edit'"}}"#), .requeue)
        XCTAssertEqual(outcome(400, #"{"error":{"code":"bad_request","message":"op must be steer or cancel"}}"#), .requeue)
        XCTAssertEqual(outcome(409, #"{"error":{"code":"not_waiting","message":"that message already went out"}}"#), .wentOut)
        XCTAssertEqual(outcome(409, #"{"error":{"code":"not_attached","message":"x"}}"#), .failed("Attach to send now"))
        XCTAssertEqual(ChatModel.editOutcome(HubError(status: 0, code: "network", message: "x")), .failed("Can't reach the hub"))
    }

    func testAnEditThatCameTooLateKeepsTheTextToSendAgain() {
        let chat = queuedChat(["lint"])
        chat.beginEdit("c1")
        chat.draft = "lint everything"
        chat.editRefused("c1", text: "lint everything",
                         HubClient.error(status: 409, body: Data(#"{"error":{"code":"not_waiting","message":"that message already went out"}}"#.utf8)))
        XCTAssertNil(chat.editingId)
        XCTAssertEqual(chat.draft, "lint everything")
        XCTAssertEqual(chat.editNote, "That message already went out")
        XCTAssertEqual(chat.tray.first?.phase, .left)
    }

    func testAnEditAnOlderHubRefusesIsQueuedAgainAtTheEnd() {
        let chat = queuedChat(["lint", "deploy"])
        chat.beginEdit("c1")
        chat.draft = "lint --fix"
        let old = HubClient.error(status: 422, body: Data(#"{"error":{"code":"bad_command","message":"unknown op"}}"#.utf8))
        chat.editRefused("c1", text: "lint --fix", old)
        // Still editing until the remove goes through.
        XCTAssertEqual(chat.editingId, "c1")
        XCTAssertNil(chat.editNote)
        chat.markRemoving("c1")
        chat.requeued("c1")
        XCTAssertNil(chat.editingId)
        XCTAssertEqual(chat.trayNote, ChatModel.requeuedNote)
        chat.stage("lint --fix", clientId: "c3", toTray: true)
        chat.posted("c3", waiting: true)
        XCTAssertEqual(chat.tray.map(\.text), ["deploy", "lint --fix"])
        // Any other refusal keeps the edit open with a note.
        chat.beginEdit("c2")
        chat.editRefused("c2", text: "deploy now", HubError(status: 0, code: "network", message: "x"))
        XCTAssertEqual(chat.editingId, "c2")
        XCTAssertEqual(chat.editNote, "Can't reach the hub")
    }

    func testTrayHeaderCountsMessages() {
        XCTAssertEqual(QueuedTray.countLabel(1), "1 message queued")
        XCTAssertEqual(QueuedTray.countLabel(5), "5 messages queued")
    }

    func testChatCacheDropsTheLeastRecentlyOpened() {
        let app = AppModel(hub: nil)
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

    func testInboxGroupingSortsWhatTheHubShips() {
        let now = Date().timeIntervalSince1970
        let item = { (key: String, status: String) in
            InboxItem(key: key, machineId: "m1", harness: "claude", agent: nil, title: key, cwd: "/w",
                      status: status, lastActiveAt: now - 60, requestId: nil)
        }
        var gh = item("gh:9", "github")
        gh.kind = "github"
        gh.repo = "o/r"
        gh.reason = "review_requested"
        gh.url = "https://github.com/o/r/pull/9"
        let groups = InboxGrouping.groups([
            item("a", "approval"), item("f", "followup"), item("e", "error"),
            item("i", "interrupted"), item("d", "done"), gh, item("x", "paused"),
        ])
        XCTAssertEqual(groups.map(\.0), [.waiting, .errors, .github, .finished])
        XCTAssertEqual(groups[0].1.map(\.key), ["a", "f", "x"], "an unfamiliar status waits rather than vanishes")
        XCTAssertEqual(groups[1].1.map(\.key), ["e", "i"])
        XCTAssertEqual(groups[2].1.map(\.key), ["gh:9"])
        XCTAssertEqual(groups[3].1.map(\.key), ["d"])
        XCTAssertTrue(gh.isGitHub)
        XCTAssertEqual(GitHubReason.label(gh.reason), "Review requested")
        XCTAssertEqual(GitHubReason.label("ci_fail"), "CI failing")
        XCTAssertEqual(GitHubReason.label(nil), "GitHub")
    }

    func testInboxItemDecodesGitHubFieldsAndDismissStamp() throws {
        let json = #"""
        {"items":[{"key":"claude:1","status":"followup","last_active_at":7},
                  {"key":"gh:55","kind":"github","repo":"o/r","subject_type":"pr","title":"PR 55",
                   "reason":"assignee","url":"https://github.com/o/r/pull/55","status":"github",
                   "unread":true,"last_active_at":9.5}]}
        """#
        let items = try Hub.decoder.decode(InboxResponse.self, from: Data(json.utf8)).items
        XCTAssertFalse(items[0].isGitHub)
        XCTAssertEqual(items[0].dismissStamp, 7)
        XCTAssertTrue(items[1].isGitHub)
        XCTAssertEqual(items[1].repo, "o/r")
        XCTAssertEqual(items[1].dismissStamp, 9.5)
    }

    func testDismissalLedgerCoversARefreshThatMissedThePost() {
        let item = { (key: String, at: Double) in
            InboxItem(key: key, machineId: "m1", harness: "claude", agent: nil, title: key, cwd: "/w",
                      status: "done", lastActiveAt: at, requestId: nil)
        }
        var ledger = DismissalLedger()
        ledger.choose("claude:1", stamp: 100)

        // A response captured before the dismiss reached the hub still
        // ships the row at its old stamp — the ledger filters it.
        XCTAssertTrue(ledger.overlay([item("claude:1", 100), item("claude:2", 50)])
            .map(\.key) == ["claude:2"])
        // New activity past the stamp brings the row back and retires
        // the entry, so a later fetch at the same stamp lists again.
        XCTAssertEqual(ledger.overlay([item("claude:1", 140)]).map(\.key), ["claude:1"])
        XCTAssertEqual(ledger.overlay([item("claude:1", 140)]).map(\.key), ["claude:1"],
                       "the retired entry must not filter the resurrected row")

        // A refused post drops the entry so the row returns.
        ledger.choose("claude:3", stamp: 10)
        ledger.drop("claude:3")
        XCTAssertEqual(ledger.overlay([item("claude:3", 5)]).map(\.key), ["claude:3"])
    }

    func testDismissalLedgerNeverHidesAPendingApproval() {
        let item = { (status: String, at: Double) in
            InboxItem(key: "claude:1", machineId: "m1", harness: "claude", agent: nil, title: "t", cwd: "/w",
                      status: status, lastActiveAt: at, requestId: status == "approval" ? "r1" : nil)
        }
        var ledger = DismissalLedger()
        ledger.choose("claude:1", stamp: 100)
        // The session was cleared, then asked for approval before its
        // last_active_at moved: the hub ships the approval regardless.
        XCTAssertEqual(ledger.overlay([item("approval", 100)]).map(\.key), ["claude:1"],
                       "a pending approval must never be hidden by a dismiss")
        // The same row as a plain status is still the one the dismiss cleared.
        XCTAssertEqual(ledger.overlay([item("done", 100)]).map(\.key), [])
    }

    func testPairedDeviceDecodesAndLabels() throws {
        let json = #"""
        {"devices":[
          {"id":"d1","name":"iPhone","display_name":"Kai's phone","kind":"iphone","model":"iPhone16,2",
           "client":"SWE Remote 1.0","os":"iOS 26","node_name":"iphone.tail","paired_via":"tailscale",
           "role":"phone","created_at":1,"last_seen_at":9,"revoked":false,"this_device":true},
          {"id":"d2","name":"mini","role":"satellite","machine_id":"mini","last_seen_at":null},
          {"id":"d3","revoked":true}]}
        """#
        let devices = try Hub.decoder.decode(DevicesResponse.self, from: Data(json.utf8)).devices
        XCTAssertEqual(devices[0].label, "Kai's phone")
        XCTAssertEqual(devices[0].symbol, "iphone")
        XCTAssertEqual(devices[0].thisDevice, true)
        XCTAssertTrue(devices[1].isSatellite)
        XCTAssertEqual(devices[1].symbol, "desktopcomputer")
        XCTAssertEqual(devices[2].label, "Device")
        XCTAssertEqual(devices[2].revoked, true)
    }
}

@MainActor
final class SessionSearchTests: XCTestCase {
    private func row(_ key: String, _ title: String, cwd: String = "/w", ago: Double) -> SessionRow {
        SessionRow(key: key, harness: "claude", agent: "Claude Code", cwd: cwd, title: title,
                   lastActiveAt: Date().timeIntervalSince1970 - ago, status: "done", machineId: "m",
                   ownership: nil, attachable: nil)
    }

    func testLocalMatchIsPlainTextTitlesFirst() {
        let rows = [row("a", "Fix ledger cursor", ago: 100), row("b", "ledger notes", ago: 5), row("c", "Dark mode", cwd: "/w/ledger(", ago: 1)]
        // Titles first (newest leading), then the folder match.
        XCTAssertEqual(SessionSearch.localMatches(rows, query: "ledger").map(\.key), ["b", "a", "c"])
        XCTAssertEqual(SessionSearch.localMatches(rows, query: "ledger(").map(\.key), ["c"], "regex syntax is just text")
        XCTAssertEqual(SessionSearch.localMatches([row("x", String(repeating: "a", count: 40), ago: 1)], query: "(a|aa)+$"), [])
    }

    func testLocalMatchWordsApartAndRecency() {
        let day = 86400.0
        let rows = [row("old", "ledger cursor", ago: 60 * day), row("new", "ledger cursor", ago: day),
                    row("apart", "cursor fix", cwd: "/w/ledger", ago: 0)]
        // Equal title matches: the newer leads. Words split across title
        // and folder rank after every title match, however recent.
        XCTAssertEqual(SessionSearch.localMatches(rows, query: "ledger cursor").map(\.key), ["new", "old", "apart"])
        XCTAssertEqual(SessionSearch.recencyWeight(1000, now: 1000), 1, accuracy: 1e-9)
        XCTAssertEqual(SessionSearch.recencyWeight(1000 - 14 * day, now: 1000), 0.65, accuracy: 1e-9)
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

    func testNoMachineMessageFollowsWhatTheHubReported() {
        // The hub answered `launchable` and it's empty: swe is current,
        // nothing is installed or everything is switched off.
        let current = NewSessionOptions.noMachineMessage([machine("m", name: "mac", launchable: [])])
        XCTAssertFalse(current.contains("Update swe"))
        XCTAssertEqual(current, "No harness can start on the hub: install one there, or turn it on in SWE's Settings › Providers.")
        // Satellites too: no single "hub" to name.
        let fleet = NewSessionOptions.noMachineMessage([machine("m", name: "mac", launchable: []),
                                                        machine("s", name: "box", launchable: [])])
        XCTAssertTrue(fleet.hasPrefix("No harness can start on any machine"))
        // A row older than `launchable`, or no rows: swe is too old.
        XCTAssertTrue(NewSessionOptions.noMachineMessage([machine("m", name: "mac", harnesses: [])]).hasPrefix("Update swe on the hub."))
        XCTAssertTrue(NewSessionOptions.noMachineMessage([]).hasPrefix("Update swe on the hub."))
    }

    func testFolderSearchMatchesEveryWordAnyCase() {
        let folders = ["/Users/k/Projects/quiver", "/Users/k/Projects/quiver-render", "/Users/k/Indie/kopifund"]
        XCTAssertEqual(NewSessionOptions.matching(folders, query: "QUIVER rend"), ["/Users/k/Projects/quiver-render"])
        XCTAssertEqual(NewSessionOptions.matching(folders, query: "  "), folders)
        XCTAssertEqual(NewSessionOptions.matching(folders, query: "nothing"), [])
    }

    func testTheFolderListFoldsAndSearchesOnlyWhenThereAreMore() {
        let folders = ["/p/a", "/p/b", "/p/c", "/p/d", "/p/e", "/q/api"]
        XCTAssertEqual(RecentFolderPicker.shown(folders, query: "", showAll: false), ["/p/a", "/p/b", "/p/c", "/p/d"])
        XCTAssertEqual(RecentFolderPicker.shown(folders, query: "", showAll: true), folders)
        // A search reads every folder, folded or not.
        XCTAssertEqual(RecentFolderPicker.shown(folders, query: " api ", showAll: false), ["/q/api"])
        XCTAssertTrue(RecentFolderPicker.offersSearch(folderCount: 5, query: ""))
        // Four or fewer all show: nothing to search, until a query is typed.
        XCTAssertFalse(RecentFolderPicker.offersSearch(folderCount: 4, query: ""))
        XCTAssertTrue(RecentFolderPicker.offersSearch(folderCount: 2, query: "a"))
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

    func testModelFieldFollowsTheMachinesList() {
        var m = machine("m1", name: "mac", launchable: ["claude", "gemini"])
        m.modelAtLaunch = ["claude"]
        m.effortAtLaunch = ["claude"]
        m.lastModels = ["claude": "claude-sonnet-4-5", "gemini": "gemini-3-pro"]
        XCTAssertTrue(NewSessionOptions.showsModelField(on: m))
        // An old satellite's row lacks the field entirely: the row hides.
        XCTAssertFalse(NewSessionOptions.showsModelField(on: machine("m2", name: "old", launchable: ["claude"])))
        // And it keys on `model_at_launch` alone, not `effort_at_launch`.
        var effortOnly = machine("m3", name: "e", launchable: ["claude"])
        effortOnly.effortAtLaunch = ["claude"]
        XCTAssertFalse(NewSessionOptions.showsModelField(on: effortOnly))
        XCTAssertTrue(NewSessionOptions.acceptsModel("claude", on: m))
        XCTAssertFalse(NewSessionOptions.acceptsModel("gemini", on: m))
        XCTAssertFalse(NewSessionOptions.acceptsModel(nil, on: m))
        XCTAssertEqual(NewSessionOptions.lastModel("claude", on: m), "claude-sonnet-4-5")
        XCTAssertEqual(NewSessionOptions.lastModel("gemini", on: m), "gemini-3-pro")
        XCTAssertNil(NewSessionOptions.lastModel("pi", on: m))
    }

    func testYoloFieldFollowsTheMachinesList() {
        var m = machine("m1", name: "mac", launchable: ["claude", "gemini"])
        m.yoloAtLaunch = ["claude"]
        XCTAssertTrue(NewSessionOptions.showsYoloField(on: m))
        // An old satellite's row lacks the field entirely: the row hides.
        XCTAssertFalse(NewSessionOptions.showsYoloField(on: machine("m2", name: "old", launchable: ["claude"])))
        // A row that reports the key but lists nothing shows the box off.
        var none = machine("m3", name: "n", launchable: ["claude"])
        none.yoloAtLaunch = []
        XCTAssertTrue(NewSessionOptions.showsYoloField(on: none))
        XCTAssertTrue(NewSessionOptions.acceptsYolo("claude", on: m))
        XCTAssertFalse(NewSessionOptions.acceptsYolo("gemini", on: m))
        XCTAssertFalse(NewSessionOptions.acceptsYolo("claude", on: none))
        XCTAssertFalse(NewSessionOptions.acceptsYolo(nil, on: m))
    }

    func testProviderFieldFollowsTheMachinesList() {
        var m = machine("m1", name: "mac", launchable: ["claude", "codex", "gemini"])
        m.providersAtLaunch = ["claude": ["openrouter", "z_ai"], "codex": []]
        m.providerNames = ["openrouter": "OpenRouter"]
        XCTAssertTrue(NewSessionOptions.showsProviderField("claude", on: m))
        XCTAssertEqual(NewSessionOptions.providers("claude", on: m), ["openrouter", "z_ai"])
        // An empty or missing list for the harness hides the row.
        XCTAssertFalse(NewSessionOptions.showsProviderField("codex", on: m))
        XCTAssertFalse(NewSessionOptions.showsProviderField("gemini", on: m))
        XCTAssertFalse(NewSessionOptions.showsProviderField(nil, on: m))
        // An old satellite's row lacks the field entirely: the row hides.
        XCTAssertFalse(NewSessionOptions.showsProviderField("claude", on: machine("m2", name: "old", launchable: ["claude"])))
        // Labels come from provider_names, falling back to the slug.
        XCTAssertEqual(NewSessionOptions.providerName("openrouter", on: m), "OpenRouter")
        XCTAssertEqual(NewSessionOptions.providerName("z_ai", on: m), "z_ai")
        // A pick survives only while the harness still offers it.
        XCTAssertEqual(NewSessionOptions.providerPick("z_ai", for: "claude", on: m), "z_ai")
        XCTAssertNil(NewSessionOptions.providerPick("z_ai", for: "codex", on: m))
        XCTAssertNil(NewSessionOptions.providerPick("nope", for: "claude", on: m))
        XCTAssertNil(NewSessionOptions.providerPick(nil, for: "claude", on: m))
        XCTAssertNil(NewSessionOptions.providerPick("", for: "claude", on: m))
    }

    func testLaunchBodyCarriesProviderOnlyWhenPicked() throws {
        let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c")
        func body(_ provider: String?) throws -> [String: Any] {
            let req = client.startSessionRequest(machine: "m1", harness: "claude", cwd: "/w", prompt: "hi",
                                                 yolo: true, provider: provider)
            XCTAssertEqual(req.httpMethod, "POST")
            XCTAssertEqual(req.url?.path, "/v1/sessions")
            return try XCTUnwrap(JSONSerialization.jsonObject(with: req.httpBody ?? Data()) as? [String: Any])
        }
        let picked = try body("openrouter")
        XCTAssertEqual(picked["provider"] as? String, "openrouter")
        XCTAssertEqual(picked["yolo"] as? Bool, true)
        XCTAssertNil(try body(nil)["provider"])
        XCTAssertNil(try body("")["provider"])
        XCTAssertNil(try body(nil)["model"])
    }

    func testModelPickOnlySendsASupportedNonemptyToken() {
        var m = machine("m1", name: "mac", launchable: ["claude", "gemini"])
        m.modelAtLaunch = ["claude"]
        XCTAssertEqual(NewSessionOptions.modelPick(" claude-opus-4-1 ", for: "claude", on: m), "claude-opus-4-1")
        // An empty field and a harness that can't take one both send
        // no `model` key.
        XCTAssertNil(NewSessionOptions.modelPick("   ", for: "claude", on: m))
        XCTAssertNil(NewSessionOptions.modelPick("gpt-5", for: "gemini", on: m))
        XCTAssertNil(NewSessionOptions.modelPick("x", for: "claude", on: machine("m2", name: "old")))
    }

    private func modelMachine() -> Machine {
        var m = machine("m1", name: "mac", launchable: ["claude", "codex", "gemini", "pi"])
        m.modelAtLaunch = ["claude", "codex", "pi"]
        m.recentModels = ["claude": ["opus", "sonnet", "haiku"], "codex": ["gpt-5", "sonnet"]]
        m.lastModels = ["claude": "opus", "codex": "gpt-5"]
        return m
    }

    func testModelMenuListsDefaultRecentsNewestFirstThenOther() {
        let m = modelMachine()
        XCTAssertEqual(NewSessionOptions.modelChoices("claude", on: m),
                       [.harnessDefault, .recent("opus"), .recent("sonnet"), .recent("haiku"), .other])
        // A harness with no recent models still offers Default and Other.
        XCTAssertEqual(NewSessionOptions.modelChoices("pi", on: m), [.harnessDefault, .other])
        XCTAssertEqual(NewSessionOptions.defaultModelChoice("claude", on: m), .recent("opus"))
        XCTAssertEqual(NewSessionOptions.defaultModelChoice("pi", on: m), .harnessDefault)
        XCTAssertEqual(NewSessionOptions.recentModels(nil, on: m), [])
        XCTAssertEqual(ModelChoice.other.label, "Other\u{2026}")
        XCTAssertEqual(ModelChoice.harnessDefault.label, "Harness default")
    }

    func testModelMenuFallsBackToLastModelsOnAnOldHub() {
        var m = machine("m1", name: "mac", launchable: ["claude", "codex"])
        m.modelAtLaunch = ["claude", "codex"]
        m.lastModels = ["claude": "opus", "codex": ""]
        XCTAssertNil(m.recentModels)
        XCTAssertEqual(NewSessionOptions.recentModels("claude", on: m), ["opus"])
        XCTAssertEqual(NewSessionOptions.recentModels("codex", on: m), [])
        // A newer hub's list wins, with blanks and repeats dropped.
        m.recentModels = ["claude": ["sonnet", "", "sonnet", "opus"]]
        XCTAssertEqual(NewSessionOptions.recentModels("claude", on: m), ["sonnet", "opus"])
        XCTAssertEqual(NewSessionOptions.recentModels("codex", on: m), [])
    }

    func testUntouchedMenuFollowsTheHarnessesNewestModel() {
        let m = modelMachine()
        var sel = ModelSelection()
        sel.follow("claude", on: m)
        XCTAssertEqual(sel.choice, .recent("opus"))
        XCTAssertEqual(sel.model(for: "claude", on: m), "opus")
        sel.follow("codex", on: m)
        XCTAssertEqual(sel.choice, .recent("gpt-5"))
        sel.follow("pi", on: m)
        XCTAssertEqual(sel.choice, .harnessDefault)
        XCTAssertNil(sel.model(for: "pi", on: m))
        XCTAssertFalse(sel.touched)
    }

    func testAPickedDefaultOrOtherSurvivesASwitch() {
        let m = modelMachine()
        var sel = ModelSelection()
        sel.follow("claude", on: m)
        sel.choose(.harnessDefault)
        sel.follow("codex", on: m)
        XCTAssertEqual(sel.choice, .harnessDefault)
        XCTAssertNil(sel.model(for: "codex", on: m))

        sel.choose(.other)
        sel.otherText = "  my-model  "
        sel.follow("claude", on: m)
        XCTAssertEqual(sel.choice, .other)
        XCTAssertEqual(sel.model(for: "claude", on: m), "my-model")
        // An empty Other field sends nothing, and still stays.
        sel.otherText = "   "
        XCTAssertNil(sel.model(for: "claude", on: m))
        sel.follow("codex", on: m)
        XCTAssertEqual(sel.choice, .other)
    }

    func testAPickedRecentModelStaysOnlyWhileTheNewHarnessListsIt() {
        let m = modelMachine()
        var sel = ModelSelection()
        sel.follow("claude", on: m)
        sel.choose(.recent("sonnet"))
        // Codex lists sonnet too, so the pick stays.
        sel.follow("codex", on: m)
        XCTAssertEqual(sel.choice, .recent("sonnet"))
        XCTAssertEqual(sel.model(for: "codex", on: m), "sonnet")
        // Codex never ran haiku: re-default instead of sending it.
        sel.follow("claude", on: m)
        sel.choose(.recent("haiku"))
        sel.follow("codex", on: m)
        XCTAssertEqual(sel.choice, .recent("gpt-5"))
        XCTAssertFalse(sel.touched)
        // Re-defaulted means untouched again: the next switch follows.
        sel.follow("claude", on: m)
        XCTAssertEqual(sel.choice, .recent("opus"))
    }

    func testARefreshKeepsTheSelectedModelInTheMenu() {
        var m = modelMachine()
        var sel = ModelSelection()
        sel.follow("claude", on: m)
        sel.choose(.recent("haiku"))
        // The 15 s machine poll drops haiku from the recent five.
        m.recentModels?["claude"] = ["fresh", "opus", "sonnet"]
        XCTAssertEqual(sel.choices(for: "claude", on: m),
                       [.harnessDefault, .recent("fresh"), .recent("opus"), .recent("sonnet"), .recent("haiku"), .other])
        // A listed or non-model choice adds nothing.
        sel.choose(.recent("opus"))
        XCTAssertEqual(sel.choices(for: "claude", on: m), NewSessionOptions.modelChoices("claude", on: m))
        sel.choose(.other)
        XCTAssertEqual(sel.choices(for: "claude", on: m), NewSessionOptions.modelChoices("claude", on: m))
    }

    func testModelSelectionSendsNothingForAHarnessThatCantTakeOne() {
        var m = modelMachine()
        m.recentModels?["gemini"] = ["gemini-3-pro"]
        var sel = ModelSelection()
        sel.follow("gemini", on: m)
        XCTAssertEqual(sel.choice, .recent("gemini-3-pro"))
        XCTAssertNil(sel.model(for: "gemini", on: m))
        sel.choose(.other)
        sel.otherText = "x"
        XCTAssertNil(sel.model(for: "gemini", on: m))
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
        // An old satellite's row carries no launch-option fields at all.
        XCTAssertNil(m.modelAtLaunch)
        XCTAssertNil(m.effortAtLaunch)
        XCTAssertNil(m.lastModels)
        let newer = try Hub.decoder.decode(Machine.self, from: Data(
            #"{"id":"m2","launchable":["claude","codex"],"model_at_launch":["claude","codex"],"effort_at_launch":["claude"],"last_models":{"claude":"claude-opus-4-1"}}"#.utf8))
        XCTAssertEqual(newer.modelAtLaunch, ["claude", "codex"])
        XCTAssertEqual(newer.effortAtLaunch, ["claude"])
        XCTAssertEqual(newer.lastModels?["claude"], "claude-opus-4-1")
        XCTAssertNil(newer.recentModels)
        let recent = try Hub.decoder.decode(Machine.self, from: Data(
            #"{"id":"m3","model_at_launch":["claude"],"recent_models":{"claude":["claude-opus-4-1","claude-sonnet-4-5"]},"last_models":{"claude":"claude-opus-4-1"}}"#.utf8))
        XCTAssertEqual(recent.recentModels?["claude"], ["claude-opus-4-1", "claude-sonnet-4-5"])
        // An old agent's row carries no provider fields.
        XCTAssertNil(m.providersAtLaunch)
        XCTAssertNil(m.providerNames)
        // Slug keys stay as sent: snake-case decoding leaves dict keys alone.
        let providers = try Hub.decoder.decode(Machine.self, from: Data(
            #"{"id":"m4","launchable":["claude","codex"],"providers_at_launch":{"claude":["openrouter","z_ai"],"codex":[]},"provider_names":{"openrouter":"OpenRouter","z_ai":"Z.ai"}}"#.utf8))
        XCTAssertEqual(providers.providersAtLaunch?["claude"], ["openrouter", "z_ai"])
        XCTAssertEqual(providers.providersAtLaunch?["codex"], [])
        XCTAssertEqual(providers.providerNames?["z_ai"], "Z.ai")
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

    func testLinkCardsCollectDistinctWebLinksInOrder() {
        let text = """
        See [the page](https://claude.ai/code/artifact/abc) and https://example.com/a.
        Again <https://claude.ai/code/artifact/abc> plus [docs](http://docs.example.org/x).
        """
        XCTAssertEqual(LinkCards.urls(in: text).map(\.absoluteString), [
            "https://claude.ai/code/artifact/abc", "https://example.com/a", "http://docs.example.org/x",
        ])
    }

    func testLinkCardsSkipCodeAndNonWebSchemes() {
        let text = """
        Use `https://inline.example.com` or [file](/Users/me/x.py), [mail](mailto:a@b.com), [call](tel:123).

        ```
        curl https://block.example.com
        ```

        [ftp](ftp://old.example.com) [real](https://real.example.com/p)
        """
        XCTAssertEqual(LinkCards.urls(in: text).map(\.absoluteString), ["https://real.example.com/p"])
    }

    func testLinkCardsHostAndPath() {
        let url = URL(string: "https://www.claude.ai/code/artifact/abc?x=1")!
        XCTAssertEqual(LinkCards.host(url), "claude.ai")
        XCTAssertEqual(LinkCards.path(url), "/code/artifact/abc?x=1")
        XCTAssertNil(LinkCards.path(URL(string: "https://claude.ai/")!))
        XCTAssertTrue(LinkCards.isWeb(url))
        XCTAssertFalse(LinkCards.isWeb(URL(string: "mailto:a@b.com")!))
    }

    func testLinkCardsReadTableCellsInOrder() {
        let text = """
        | Page | Link |
        |---|---|
        | a | [one](https://one.example.com) |
        | b | https://two.example.com/x |
        """
        XCTAssertEqual(LinkCards.urls(in: text).map(\.absoluteString),
                       ["https://one.example.com", "https://two.example.com/x"])
    }
}

final class ChatStreamGateTests: XCTestCase {
    func testOnlyTheBackgroundStopsAndRestartsTheStream() {
        var gate = ChatStreamGate()
        XCTAssertEqual(gate.phase(.inactive), ChatStreamGate.Action.none, "Control Center leaves it running")
        XCTAssertEqual(gate.phase(.active), ChatStreamGate.Action.none, "and coming back restarts nothing")
        XCTAssertEqual(gate.phase(.inactive), ChatStreamGate.Action.none)
        XCTAssertEqual(gate.phase(.background), .stop)
        XCTAssertEqual(gate.phase(.inactive), ChatStreamGate.Action.none)
        XCTAssertEqual(gate.phase(.active), .start, "back from the background: started once")
        XCTAssertEqual(gate.phase(.active), ChatStreamGate.Action.none)
    }
}
