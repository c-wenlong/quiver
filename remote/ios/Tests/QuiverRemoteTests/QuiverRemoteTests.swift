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

final class MessageBlocksTests: XCTestCase {
    func testSplitsFencedCode() {
        let blocks = MessageBlocks.split("Run this:\n```bash\nls -la\n```\nDone.")
        XCTAssertEqual(blocks, [.prose("Run this:"), .code("ls -la"), .prose("Done.")])
    }

    func testUnclosedFenceIsCode() {
        XCTAssertEqual(MessageBlocks.split("```\nhalf"), [.code("half")])
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
