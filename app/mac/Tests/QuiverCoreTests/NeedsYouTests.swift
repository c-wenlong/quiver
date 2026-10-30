import Foundation
import Testing
@testable import QuiverCore

/// A one-shot gate: `wait` parks until `open`, or returns at once after.
private actor Latch {
    private var opened = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func wait() async {
        if opened { return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func open() {
        opened = true
        waiters.forEach { $0.resume() }
        waiters = []
    }
}

private func item(_ key: String, _ status: String?, at: Double = 100, kind: String? = nil) -> HubInboxItem {
    HubInboxItem(key: key, harness: "claude", title: key, status: status, lastActiveAt: at, kind: kind)
}

@Suite struct NeedsYouGroupingTests {
    @Test func decodesTheHubsInboxRows() throws {
        let json = #"""
        {"items": [
          {"key": "claude:a", "machine_id": "m1", "harness": "claude", "agent": "Claude Code",
           "title": "Fix it", "cwd": "/w", "status": "approval", "last_active_at": 1700000000.5,
           "request_id": "r1"},
          {"key": "codex:b", "status": "done"},
          {"key": "gh:42", "kind": "github", "status": "github", "repo": "o/r",
           "reason": "mention", "url": "https://github.com/o/r/issues/1", "last_active_at": 5}
        ]}
        """#
        let items = try HubWire.decoder.decode(HubInboxItems.self, from: Data(json.utf8)).items
        #expect(items.count == 3)
        #expect(items[0].machineId == "m1")
        #expect(items[0].requestId == "r1")
        #expect(items[0].lastActiveAt == 1700000000.5)
        #expect(items[0].isApproval)
        #expect(items[1].displayTitle == "Untitled session")
        #expect(items[1].dismissStamp == 0)
        #expect(items[2].isGitHub)
    }

    @Test func sectionsComeInPageOrderWithoutGitHubRows() {
        let groups = NeedsYouSection.groups([
            item("done:1", "done"),
            item("gh:1", "github", kind: "github"),
            item("err:1", "error"),
            item("wait:1", "followup"),
            item("int:1", "interrupted"),
            item("appr:1", "approval"),
        ])
        #expect(groups.map(\.section) == [.waiting, .errors, .finished])
        #expect(groups[0].items.map(\.key) == ["wait:1", "appr:1"])
        #expect(groups[1].items.map(\.key) == ["err:1", "int:1"])
        #expect(groups[2].items.map(\.key) == ["done:1"])
        #expect(NeedsYouSection.groups([item("gh:1", nil, kind: "github")]).isEmpty)
    }

    /// A status a newer hub invents still needs a look, so it waits on
    /// you rather than vanishing.
    @Test func anUnfamiliarStatusWaitsOnYou() {
        #expect(NeedsYouSection.of(item("x", "paused")) == .waiting)
        #expect(NeedsYouSection.of(item("x", nil)) == .waiting)
        #expect(NeedsYouSection.statusLabel(item("x", "paused")) == "Paused")
        #expect(NeedsYouSection.statusLabel(item("x", "approval")) == "Approval needed")
        #expect(NeedsYouSection.statusLabel(item("x", "followup")) == "Waiting")
    }

    @Test func aDismissalHidesRowsUntilTheyMovePastItsStamp() {
        var ledger = InboxDismissals()
        ledger.choose("a", stamp: 100)
        #expect(ledger.overlay([item("a", "error", at: 100), item("b", "error")]).map(\.key) == ["b"])
        #expect(ledger.stamps["a"] == 100)
        // New activity brings it back and retires the entry.
        #expect(ledger.overlay([item("a", "error", at: 101)]).map(\.key) == ["a"])
        #expect(ledger.stamps.isEmpty)
    }
}

@MainActor
@Suite struct NeedsYouModelTests {
    @Test func aLoadKeepsSessionRowsAndAFailureShowsNothing() async {
        let model = NeedsYouModel()
        await model.load { [item("a", "followup"), item("gh:1", "github", kind: "github")] }
        #expect(model.items.map(\.key) == ["a"])
        #expect(model.reachable)

        await model.load { throw HubError(status: 0, code: "hub_down", message: "down") }
        #expect(model.items.isEmpty)
        #expect(!model.reachable)
    }

    @Test func aCancelledLoadKeepsTheList() async {
        let model = NeedsYouModel()
        await model.load { [item("a", "followup")] }
        await model.load { throw CancellationError() }
        #expect(model.items.map(\.key) == ["a"])
    }

    @Test func clearPostsEachRowButNeverAnApproval() async {
        let model = NeedsYouModel()
        await model.load { [item("a", "error", at: 7), item("b", "approval"), item("c", "done", at: 9)] }
        var posted: [String: Double] = [:]
        await model.dismiss(model.items, post: { posted[$0] = $1 }, refetch: { [] })
        #expect(posted == ["a": 7, "c": 9])
        #expect(model.items.map(\.key) == ["b"])

        // A poll that left before the posts landed can't put them back.
        await model.load { [item("a", "error", at: 7), item("b", "approval"), item("c", "done", at: 9)] }
        #expect(model.items.map(\.key) == ["b"])
    }

    @Test func aRefusedClearRestoresTheRow() async {
        let model = NeedsYouModel()
        let rows = [item("a", "error"), item("c", "done")]
        await model.load { rows }
        await model.dismiss(rows, post: { key, _ in
            if key == "a" { throw HubError(status: 500, code: "store_failed", message: "no") }
        }, refetch: { rows })
        #expect(model.items.map(\.key) == ["a"])
    }

    /// A poll that was already in flight when a row was cleared answers
    /// with the list from before: it must not land over the clear.
    @Test func aSlowLoadCannotLandOverANewerClear() async {
        let model = NeedsYouModel()
        let rows = [item("a", "error"), item("b", "error")]
        await model.load { rows }
        let started = Latch()
        let release = Latch()
        let slow = Task { @MainActor in
            await model.load {
                await started.open()
                await release.wait()
                return [item("a", "error"), item("b", "error"), item("z", "followup")]
            }
        }
        await started.wait()
        await model.dismiss([rows[0]], post: { _, _ in }, refetch: { [] })
        await release.open()
        await slow.value
        #expect(model.items.map(\.key) == ["b"])
    }
}

extension HubStubbed {
@Suite struct NeedsYouClientTests {
    let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "secret",
                           session: HubStub.session())

    @Test func inboxAsksForFinishedRowsAndDismissPostsTheStamp() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/inbox"
                ? (200, Data(#"{"items": [{"key": "claude:a", "status": "done", "last_active_at": 3}]}"#.utf8))
                : (200, Data(#"{"ok": true}"#.utf8))
        }
        let items = try await client.inbox(done: true)
        #expect(items.map(\.key) == ["claude:a"])
        let get = try #require(HubStub.requests.first)
        #expect(get.url?.query == "done=1")
        #expect(get.value(forHTTPHeaderField: "Authorization") == "Bearer secret")

        try await client.dismissInbox("claude:a", lastActiveAt: 3.5)
        let post = try #require(HubStub.requests.last)
        #expect(post.httpMethod == "POST")
        #expect(post.url?.path == "/v1/inbox/dismiss")
        let body = try #require(
            try JSONSerialization.jsonObject(with: HubStub.bodyData(post)) as? [String: Any])
        #expect(body["key"] as? String == "claude:a")
        #expect(body["last_active_at"] as? Double == 3.5)
    }
}
}

@MainActor
@Suite struct NeedsYouApprovalTests {
    /// The hub ships a pending approval whatever this Mac cleared; a
    /// session cleared a moment ago that now asks for approval with the
    /// same activity stamp must still show.
    @Test func anApprovalShowsEvenRightAfterItsSessionWasCleared() async {
        let model = NeedsYouModel()
        await model.load { [item("a", "followup", at: 50)] }
        await model.dismiss(model.items, post: { _, _ in }, refetch: { [] })
        #expect(model.items.isEmpty)
        await model.load { [item("a", "approval", at: 50)] }
        #expect(model.items.map(\.key) == ["a"])
    }
}
