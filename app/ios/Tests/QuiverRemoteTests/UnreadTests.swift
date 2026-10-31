import XCTest
@testable import QuiverRemote

/// Unread dots (#1439): the row's flag, the tracker laid over it, the
/// debounced read posts and the request that carries them.
final class UnreadTests: XCTestCase {
    private func row(_ key: String, unread: Bool?, at: Double?) -> SessionRow {
        var r = SessionRow(key: key, harness: "claude", agent: nil, cwd: "/w", title: key, lastActiveAt: at,
                           status: "done", machineId: "m1", ownership: "observed", attachable: true)
        r.unread = unread
        return r
    }

    // MARK: Decoding

    func testTheRowDecodesUnreadAndAnOlderHubReadsAsRead() throws {
        let json = #"""
        {"sessions":[{"key":"claude:1","last_active_at":10,"unread":true},
                     {"key":"claude:2","last_active_at":11,"unread":false},
                     {"key":"claude:3","last_active_at":12}],
         "machines":[],"server_time":20}
        """#
        let rows = try Hub.decoder.decode(SessionsResponse.self, from: Data(json.utf8)).sessions
        XCTAssertEqual(rows.map(\.unread), [true, false, nil])
        let tracker = UnreadTracker()
        XCTAssertEqual(rows.map(tracker.isUnread), [true, false, false], "no field, no dot")
    }

    func testTheReadAnswerDecodes() throws {
        let ack = try Hub.decoder.decode(ReadAck.self, from: Data(#"{"ok":true,"at":1760000000.5}"#.utf8))
        XCTAssertEqual(ack, ReadAck(ok: true, at: 1_760_000_000.5))
    }

    // MARK: Tracker

    func testTheOpenChatShowsNoDot() {
        var t = UnreadTracker()
        t.open("a", at: nil)
        XCTAssertFalse(t.isUnread(row("a", unread: true, at: 500)), "new activity in the open chat is being read")
        XCTAssertTrue(t.isUnread(row("b", unread: true, at: 500)))
    }

    func testAMarkHidesTheDotUntilTheRowMovesPastIt() {
        var t = UnreadTracker()
        t.open("a", at: 100)
        t.close("a", at: 120)
        XCTAssertNil(t.openKey)
        // A list fetched before the hub saw the read still says unread.
        XCTAssertFalse(t.isUnread(row("a", unread: true, at: 120)))
        XCTAssertFalse(t.isUnread(row("a", unread: true, at: 110)))
        // New activity after it: the dot comes back.
        XCTAssertTrue(t.isUnread(row("a", unread: true, at: 121)))
    }

    func testAMarkNeverMovesBack() {
        var t = UnreadTracker()
        t.mark("a", at: 200)
        t.mark("a", at: 150)
        XCTAssertEqual(t.marks["a"], 200)
        t.mark("a", at: nil)
        XCTAssertEqual(t.marks["a"], 200)
    }

    func testClosingAnotherChatLeavesTheOpenOneOpen() {
        var t = UnreadTracker()
        // Pushing chat b over a: b appears before a disappears.
        t.open("a", at: 1)
        t.open("b", at: 1)
        t.close("a", at: 2)
        XCTAssertEqual(t.openKey, "b")
    }

    func testReconcileDropsMarksTheHubCaughtUpWithOrTheRowPassed() {
        var t = UnreadTracker()
        t.mark("caught", at: 100)
        t.mark("waiting", at: 100)
        t.mark("passed", at: 100)
        t.mark("gone", at: 100)
        t.reconcile([
            row("caught", unread: false, at: 100),
            row("waiting", unread: true, at: 100),
            row("passed", unread: true, at: 101),
        ])
        XCTAssertEqual(t.marks, ["waiting": 100])
        // A dropped mark doesn't come back: the hub's flag rules.
        XCTAssertTrue(t.isUnread(row("passed", unread: true, at: 101)))
        XCTAssertFalse(t.isUnread(row("caught", unread: false, at: 100)))
    }

    func testNewActivityAfterTheHubCaughtUpRelights() {
        var t = UnreadTracker()
        t.open("a", at: 100)
        t.close("a", at: 100)
        t.reconcile([row("a", unread: false, at: 100)])
        XCTAssertTrue(t.marks.isEmpty)
        XCTAssertTrue(t.isUnread(row("a", unread: true, at: 130)))
    }

    // MARK: Debounce

    func testABurstOfMessagesMakesOnePostIntervalAfterTheLast() {
        var d = ReadDebounce()
        XCTAssertNil(d.message("a", now: 0))
        XCTAssertNil(d.message("a", now: 1))
        XCTAssertNil(d.message("a", now: 2))
        XCTAssertEqual(d.due, 2 + ReadDebounce.interval)
        XCTAssertNil(d.fire(now: 3), "not yet: the last message pushed it out")
        XCTAssertNil(d.fire(now: 4.99))
        XCTAssertEqual(d.fire(now: 5), "a")
        XCTAssertNil(d.fire(now: 10), "one post per burst")
        XCTAssertNil(d.key)
    }

    func testClosingFlushesAWaitingPost() {
        var d = ReadDebounce()
        _ = d.message("a", now: 0)
        XCTAssertNil(d.flush("b"), "another chat's close leaves it")
        XCTAssertEqual(d.flush("a"), "a")
        XCTAssertNil(d.fire(now: 100), "flushed, so the timer finds nothing")
        XCTAssertNil(d.flush("a"), "nothing waiting, nothing to post")
    }

    func testAMessageInAnotherChatHandsBackTheWaitingPost() {
        var d = ReadDebounce()
        _ = d.message("a", now: 0)
        XCTAssertEqual(d.message("b", now: 1), "a")
        XCTAssertEqual(d.key, "b")
    }

    @MainActor
    func testThePosterPostsOnOpenOnceAfterABurstAndFlushesOnClose() async {
        var clock = 0.0
        var posted: [String] = []
        let poster = ReadPoster(now: { clock }, sleep: { clock += $0 }, post: { posted.append($0) })

        poster.opened("a")
        XCTAssertEqual(posted, ["a"], "opening posts at once")

        poster.message("a")
        clock = 1
        poster.message("a")
        clock = 2
        poster.message("a")
        XCTAssertEqual(posted, ["a"], "nothing until the burst settles")
        await poster.settle()
        XCTAssertEqual(posted, ["a", "a"], "one trailing post for the burst")
        XCTAssertEqual(clock, 2 + ReadDebounce.interval)

        poster.message("a")
        poster.closed("a")
        XCTAssertEqual(posted, ["a", "a", "a"], "closing sends the waiting post")
        await poster.settle()
        XCTAssertEqual(posted.count, 3, "and the timer has nothing left")

        poster.closed("a")
        XCTAssertEqual(posted.count, 3, "a close with nothing waiting posts nothing")
    }

    @MainActor
    func testOpeningAnotherChatSendsTheWaitingPostFirst() {
        var posted: [String] = []
        let poster = ReadPoster(now: { 0 }, sleep: { _ in }, post: { posted.append($0) })
        poster.message("a")
        poster.opened("b")
        XCTAssertEqual(posted, ["a", "b"])
    }

    @MainActor
    func testAResetDropsTheWaitingPost() async {
        var posted: [String] = []
        let poster = ReadPoster(now: { 0 }, sleep: { _ in await Task.yield() }, post: { posted.append($0) })
        poster.message("a")
        poster.reset()
        await poster.settle()
        poster.closed("a")
        XCTAssertEqual(posted, [], "another hub went on screen: nothing goes to it")
    }

    // MARK: AppModel

    @MainActor
    func testOpeningAChatClearsItsDotAndTheHubsCursorBecomesTheMark() async {
        let app = AppModel(hub: PairedHub(baseURL: URL(string: "http://hub.test:8477")!,
                                          credential: Credential(deviceId: "d", credential: "c", name: nil)))
        let posted = expectation(description: "read posted")
        var keys: [String] = []
        app.markReadOnHub = { client, key in
            XCTAssertEqual(client.baseURL.host, "hub.test")
            keys.append(key)
            posted.fulfill()
            return ReadAck(ok: true, at: 150)
        }
        app.chatOpened("claude:1", newest: 140)
        XCTAssertEqual(app.unread.openKey, "claude:1")
        XCTAssertFalse(app.isUnread(row("claude:1", unread: true, at: 500)), "open: no dot")
        await fulfillment(of: [posted], timeout: 2)
        XCTAssertEqual(keys, ["claude:1"])
        // Let the answer land.
        for _ in 0..<20 where app.unread.marks["claude:1"] != 150 { await Task.yield() }
        XCTAssertEqual(app.unread.marks["claude:1"], 150, "the hub's cursor clears up to it")

        app.chatClosed("claude:1", newest: 140)
        XCTAssertNil(app.unread.openKey)
        XCTAssertFalse(app.isUnread(row("claude:1", unread: true, at: 150)))
        XCTAssertTrue(app.isUnread(row("claude:1", unread: true, at: 151)), "a reply after the read relights it")
    }

    @MainActor
    func testARefusedReadIsIgnored() async {
        let app = AppModel(hub: PairedHub(baseURL: URL(string: "http://hub.test:8477")!,
                                          credential: Credential(deviceId: "d", credential: "c", name: nil)))
        let posted = expectation(description: "read posted")
        app.markReadOnHub = { _, _ in
            posted.fulfill()
            throw HubError(status: 404, code: "unknown_session", message: "gone")
        }
        app.chatOpened("claude:9", newest: 70)
        await fulfillment(of: [posted], timeout: 2)
        await Task.yield()
        XCTAssertEqual(app.unread.marks["claude:9"], 70, "only the optimistic mark; no error anywhere")
        XCTAssertNil(app.loadError)
    }

    // MARK: Request

    func testTheReadRequestPostsAnEmptyObjectToTheEncodedKey() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [ReadHubStub.self]
        let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c",
                               session: URLSession(configuration: config))
        let ack = try await client.markRead("claude:abc@host")
        XCTAssertEqual(ack, ReadAck(ok: true, at: 42.5))
        let seen = try XCTUnwrap(ReadHubStub.last)
        XCTAssertEqual(seen.method, "POST")
        XCTAssertEqual(seen.path, "/v1/sessions/claude%3Aabc%40host/read")
        XCTAssertEqual(seen.auth, "Bearer c")
        XCTAssertEqual(seen.contentType, "application/json")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: seen.body) as? [String: Any])
        XCTAssertTrue(body.isEmpty, "no `at`: the hub stamps now with its own clock")
    }
}

/// Answers a read with `{ok, at}` and keeps what it was sent.
final class ReadHubStub: URLProtocol {
    struct Seen {
        var method: String?
        var path: String
        var auth: String?
        var contentType: String?
        var body: Data
    }

    private static let lock = NSLock()
    private static var _last: Seen?
    static var last: Seen? { lock.withLock { _last } }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        // URLSession hands a protocol the body as a stream, not httpBody.
        var body = request.httpBody ?? Data()
        if body.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            var buffer = [UInt8](repeating: 0, count: 1024)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                if n <= 0 { break }
                body.append(buffer, count: n)
            }
            stream.close()
        }
        let seen = Seen(method: request.httpMethod, path: request.url?.path(percentEncoded: true) ?? "",
                        auth: request.value(forHTTPHeaderField: "Authorization"),
                        contentType: request.value(forHTTPHeaderField: "Content-Type"), body: body)
        Self.lock.withLock { Self._last = seen }
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                       headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(#"{"ok":true,"at":42.5}"#.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
