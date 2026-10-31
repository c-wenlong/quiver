import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// Answers the hub client's requests inside URLSession, so the real
/// transport runs without a hub. URLSession loads on its own thread while
/// a test reads and resets on the main actor, so both go through a lock.
final class HubStub: URLProtocol, @unchecked Sendable {
    private static let state = Mutex<(handler: (@Sendable (URLRequest) -> (Int, Data))?, requests: [URLRequest])>((nil, []))
    static var handler: (@Sendable (URLRequest) -> (Int, Data))? {
        get { state.withLock { $0.handler } }
        set { state.withLock { $0.handler = newValue } }
    }
    static var requests: [URLRequest] {
        get { state.withLock { $0.requests } }
        set { state.withLock { $0.requests = newValue } }
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        let handler = Self.state.withLock { state in
            state.requests.append(request)
            return state.handler
        }
        let (status, data) = handler?(request) ?? (404, Data())
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [HubStub.self]
        return URLSession(configuration: config)
    }

    /// The bytes a request carried: URLSession hands a protocol the body
    /// as a stream, not as `httpBody`.
    static func bodyData(_ request: URLRequest) -> Data {
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                guard n > 0 else { break }
                data.append(buffer, count: n)
            }
            stream.close()
        }
        return data
    }

    /// The body a POST carried, as flat string fields.
    static func body(_ request: URLRequest) -> [String: String] {
        (try? JSONSerialization.jsonObject(with: bodyData(request))) as? [String: String] ?? [:]
    }

    /// A request's query as a name → value dict.
    static func query(_ request: URLRequest) -> [String: String] {
        let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(items.map { ($0.name, $0.value ?? "") }) { _, b in b }
    }
}

/// A live events connection: answers 200 with the frame `frame` picks
/// for the request, then holds the connection open, as the hub does
/// between events. Only a cancelled reader ever closes one, so `closed`
/// counting up proves the reader's loop let go of it.
final class HeldStream: URLProtocol, @unchecked Sendable {
    struct Watch: Sendable {
        let frame: @Sendable (URLRequest) -> String
        let opened = CountGate()
        let closed = CountGate()
    }
    private static let current = Mutex<Watch?>(nil)
    /// Starts counting afresh, answering each connection with `frame`.
    static func watch(_ frame: @escaping @Sendable (URLRequest) -> String) -> Watch {
        let watch = Watch(frame: frame)
        current.withLock { $0 = watch }
        return watch
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let watch = Self.current.withLock({ $0 }) else { return }
        Task { await watch.opened.bump() }
        // Without a content type URLSession sniffs the first 512 bytes
        // before handing over the response, and a short frame never gets
        // there.
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                       headerFields: ["Content-Type": "text/event-stream"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(watch.frame(request).utf8))
    }

    override func stopLoading() {
        guard let watch = Self.current.withLock({ $0 }) else { return }
        Task { await watch.closed.bump() }
    }

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [HeldStream.self]
        return URLSession(configuration: config)
    }
}

final class FakeLocator: HubLocating, @unchecked Sendable {
    var address: String?
    var tokens = 0
    init(address: String?) { self.address = address }
    func hubAddress() async throws -> String? { address }
    func pairToken(name: String) async throws -> String {
        tokens += 1
        return "tok-\(tokens)"
    }
}

@Suite struct HubWireTests {
    @Test func sseFramesYieldTheirDataLines() {
        var parser = SSEParser()
        #expect(parser.feed(": ka") == nil)
        #expect(parser.feed("id: 4") == nil)
        #expect(parser.feed("data: {\"a\":1}") == nil)
        #expect(parser.feed("") == "{\"a\":1}")
        #expect(parser.feed("") == nil)
    }

    @Test func sessionKeysArePercentEncodedInPaths() {
        #expect(HubClient.sessionPath("claude:abc@m1", "messages") == "/v1/sessions/claude%3Aabc%40m1/messages")
        #expect(HubSessionRow.key(harness: "codex", id: "019a") == "codex:019a")
    }

    @Test func errorsKeepTheHubsWordingUnlessKnown() {
        let body = Data(#"{"error": {"code": "weird", "message": "something broke"}}"#.utf8)
        #expect(HubError.from(status: 500, body: body).message == "Something broke")
        let unknown = HubError.from(status: 404, body: Data(#"{"error": {"code": "unknown_session"}}"#.utf8))
        #expect(unknown.message == "The hub doesn't list this session.")
        #expect(HubError.from(status: 401, body: Data()).isUnauthorized)
    }

    @Test func machineRowsCarryTheLaunchOptionFields() throws {
        let json = Data(#"""
        {"machines": [
            {"id": "m1", "name": "mac", "online": true,
             "launchable": ["claude", "codex"],
             "model_at_launch": ["claude"],
             "effort_at_launch": ["claude"],
             "last_models": {"claude": "claude-sonnet-4.5", "codex": "gpt-5"},
             "recent_models": {"claude": ["claude-sonnet-4.5", "claude-opus-4.1"],
                               "codex": ["gpt-5"]}},
            {"id": "m2", "name": "old-sat", "online": true,
             "launchable": ["gemini"]}
        ]}
        """#.utf8)
        let machines = try HubWire.decoder.decode(HubMachinesResponse.self, from: json).machines
        let new = try #require(machines.first)
        #expect(new.modelAtLaunch == ["claude"])
        #expect(new.effortAtLaunch == ["claude"])
        #expect(new.lastModels == ["claude": "claude-sonnet-4.5", "codex": "gpt-5"])
        #expect(new.recentModels == ["claude": ["claude-sonnet-4.5", "claude-opus-4.1"],
                                     "codex": ["gpt-5"]])
        // An older satellite reports none: the sheets hide the Model
        // row for it rather than send a pick it would drop.
        let old = machines[1]
        #expect(old.modelAtLaunch == nil)
        #expect(old.effortAtLaunch == nil)
        #expect(old.lastModels == nil)
        #expect(old.recentModels == nil)
    }

    @Test func baseURLsComeFromSwesAddress() {
        #expect(LocalHub.baseURL("127.0.0.1:8477")?.absoluteString == "http://127.0.0.1:8477")
        #expect(LocalHub.baseURL("0.0.0.0:8477")?.absoluteString == "http://127.0.0.1:8477")
        #expect(LocalHub.baseURL("::1:9000")?.absoluteString == "http://[::1]:9000")
        #expect(LocalHub.baseURL("nonsense") == nil)
    }

    @Test func plainHTTPOnlyReachesThisMacOrTheTailnet() {
        // Loopback, localhost and the tailnet keep http; anything else
        // would put the bearer credential on a wire readable by others.
        #expect(LocalHub.baseURL("localhost:8477")?.absoluteString == "http://localhost:8477")
        #expect(LocalHub.baseURL("mac.tail1234.ts.net:8477")?.absoluteString
            == "http://mac.tail1234.ts.net:8477")
        #expect(LocalHub.baseURL("100.64.1.2:8477")?.absoluteString == "http://100.64.1.2:8477")
        #expect(LocalHub.baseURL("100.127.255.254:8477") != nil)
        #expect(LocalHub.baseURL("192.168.1.5:8477") == nil)
        #expect(LocalHub.baseURL("10.0.0.4:8477") == nil)
        #expect(LocalHub.baseURL("100.200.1.1:8477") == nil)
        #expect(LocalHub.baseURL("8.8.8.8:8477") == nil)
        #expect(LocalHub.baseURL("hub.example.com:8477") == nil)
        // A dotted name is not an IP: prefixes can't judge where DNS lands.
        #expect(LocalHub.baseURL("127.evil.example:8477") == nil)
        #expect(LocalHub.baseURL("100.64.evil.example:8477") == nil)
        #expect(LocalHub.baseURL("127.0.0.1.evil.example:8477") == nil)
        // Non-canonical octets read differently to inet_aton — "0127" is
        // octal there, so it must not count as loopback here either.
        #expect(LocalHub.baseURL("0127.0.0.1:8477") == nil)
        #expect(LocalHub.baseURL("127.0.0.1:8477") != nil)
    }
}

/// Every suite that answers through `HubStub`, one test at a time: they
/// share its handler.
@Suite(.serialized) struct HubStubbed {}

extension HubStubbed {
@MainActor
@Suite struct LocalHubTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }
    var dir: URL { scratch.url }
    var store: HubDeviceStore { HubDeviceStore(url: dir.appending(path: "remote/app-device.json")) }

    func hub(_ locator: FakeLocator) -> LocalHub {
        LocalHub(locator: locator, store: store) { HubClient(baseURL: $0, credential: $1, session: HubStub.session()) }
    }

    @Test func pairsOnceAndKeepsTheCredential() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/pair"
                ? (201, Data(#"{"device_id": "d1", "credential": "secret", "name": "Quiver for Mac"}"#.utf8))
                : (404, Data())
        }
        let locator = FakeLocator(address: "127.0.0.1:8477")
        let client = try await hub(locator).client()
        #expect(client.credential == "secret")
        #expect(client.baseURL.absoluteString == "http://127.0.0.1:8477")
        #expect(HubStub.body(try #require(HubStub.requests.first)) == ["token": "tok-1", "name": "Quiver for Mac"])
        #expect(store.credential(for: "127.0.0.1:8477") == "secret")
        let attributes = try FileManager.default.attributesOfItem(atPath: store.url.path)
        #expect((attributes[.posixPermissions] as? Int) == 0o600)

        // A second app launch reads it back instead of pairing.
        let again = try await hub(locator).client()
        #expect(again.credential == "secret")
        #expect(locator.tokens == 1)
        // A hub at another address pairs afresh.
        #expect(store.credential(for: "127.0.0.1:9999") == nil)
    }

    /// A hub that moved leaves its file behind: re-pairing saves a new
    /// credential over the old one, which only an overwriting rename can
    /// do — `moveItem` would throw and the app could never reconnect.
    @Test func savingOverAnExistingCredentialReplacesIt() throws {
        let file = store.url
        try store.save(HubCredential(deviceId: "d1", credential: "old"), for: "127.0.0.1:8477")
        try store.save(HubCredential(deviceId: "d2", credential: "new"), for: "127.0.0.1:9000")
        #expect(store.credential(for: "127.0.0.1:9000") == "new")
        #expect(store.credential(for: "127.0.0.1:8477") == nil)
        let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
    }

    @Test func aDownHubOpensDetailsInstead() async {
        let local = hub(FakeLocator(address: nil))
        await local.refreshKeys()
        #expect(local.keys == .unavailable)
        #expect(!local.opensChat("claude:x"))
        await #expect(throws: HubError.self) { try await local.client() }
    }

    @Test func aHubOnAnUntrustedAddressIsBlockedNotReportedDown() async {
        let local = hub(FakeLocator(address: "192.168.1.5:8477"))
        await local.refreshKeys()
        #expect(local.keys == .unavailable)
        do {
            _ = try await local.client()
            Issue.record("a LAN hub address should be refused")
        } catch let error as HubError {
            #expect(error.code == "hub_blocked")
            #expect(error.message.contains("192.168.1.5"))
        } catch {
            Issue.record("expected a HubError, got \(error)")
        }
    }

    @Test func keysStayUnknownUntilPairedAndThenFollowTheHub() async throws {
        let local = hub(FakeLocator(address: "127.0.0.1:8477"))
        await local.refreshKeys()
        #expect(local.keys == .unknown)
        #expect(local.opensChat("claude:anything"))

        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        HubStub.handler = { req in
            req.value(forHTTPHeaderField: "Authorization") == "Bearer secret"
                ? (200, Data(#"{"sessions": [{"key": "claude:a"}, {"key": "codex:b"}]}"#.utf8))
                : (401, Data())
        }
        await local.refreshKeys()
        #expect(local.keys == .known(["claude:a", "codex:b"]))
        #expect(local.opensChat("codex:b"))
        #expect(!local.opensChat("claude:gone"))
    }

    @Test func theHubsRowsCarryEachSessionsModelAndNothingWhenUnknown() async throws {
        let local = hub(FakeLocator(address: "127.0.0.1:8477"))
        await local.refreshKeys()
        #expect(local.models.isEmpty, "not paired: nothing to show")

        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        HubStub.handler = { _ in
            (200, Data(#"""
            {"sessions": [{"key": "claude:a", "model": "claude-opus-4-5-20251101"},
                          {"key": "codex:b"}, {"key": "pi:c", "model": ""}]}
            """#.utf8))
        }
        await local.refreshKeys()
        #expect(local.model(for: "claude:a") == "claude-opus-4-5-20251101")
        #expect(local.model(for: "codex:b") == nil, "an older hub, or an unknown model")
        #expect(local.keys == .known(["claude:a", "codex:b", "pi:c"]))

        HubStub.handler = { _ in (401, Data()) }
        await local.refreshKeys()
        #expect(local.models.isEmpty, "a hub that stops answering shows no stale model")
    }

    /// A rename the stream announced while a list read was still out
    /// survives the read's answer: the rows were fetched first, but the
    /// meta is newer.
    @Test func aRefreshKeepsTheMetaThatLandedWhileItRead() async throws {
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        let local = hub(FakeLocator(address: "127.0.0.1:8477"))
        let rows = Data(#"{"sessions":[{"key":"claude:a","title":"old","harness_title":"old"}]}"#.utf8)
        HubStub.handler = { _ in (200, rows) }
        await local.refreshKeys()
        #expect(local.overlay["claude:a"]?.title == "old")

        let reading = CountGate()
        let release = DispatchSemaphore(value: 0)
        HubStub.handler = { _ in
            Task { await reading.bump() }
            release.wait()
            return (200, rows)
        }
        async let refresh: Void = local.refreshKeys()
        #expect(await reading.reach(1))
        local.noteMeta("claude:a", HubMetaReply(title: "new", harnessTitle: "old"))
        release.signal()
        await refresh
        #expect(local.overlay["claude:a"]?.title == "new")
    }

    @Test func aSessionRowDecodesAnOptionalModel() throws {
        let with = try HubWire.decoder.decode(HubSessionRow.self,
            from: Data(#"{"key": "claude:a", "model": "anthropic/claude-opus-4-5"}"#.utf8))
        #expect(with.model == "anthropic/claude-opus-4-5")
        #expect(with.modelLabel == "claude-opus-4-5")
        let without = try HubWire.decoder.decode(HubSessionRow.self, from: Data(#"{"key": "claude:a"}"#.utf8))
        #expect(without.model == nil)
        #expect(without.modelLabel == nil)
    }

    @Test func aRevokedCredentialPairsAgain() async throws {
        try store.save(HubCredential(deviceId: "old", credential: "revoked"), for: "127.0.0.1:8477")
        HubStub.handler = { req in
            if req.url?.path == "/v1/pair" { return (201, Data(#"{"device_id": "d2", "credential": "fresh"}"#.utf8)) }
            return req.value(forHTTPHeaderField: "Authorization") == "Bearer fresh"
                ? (200, Data(#"{"sessions": []}"#.utf8))
                : (401, Data(#"{"error": {"code": "unauthorized"}}"#.utf8))
        }
        let locator = FakeLocator(address: "127.0.0.1:8477")
        let rows = try await hub(locator).withClient { try await $0.sessions() }
        #expect(rows.isEmpty)
        #expect(locator.tokens == 1)
        #expect(store.credential(for: "127.0.0.1:8477") == "fresh")
    }
}

}

@MainActor
@Suite struct SessionChatModelTests {
    func model() -> SessionChatModel {
        SessionChatModel(key: "claude:s1", hub: LocalHub(locator: FakeLocator(address: nil),
                                                         store: HubDeviceStore(url: URL(filePath: "/dev/null"))))
    }

    func message(_ seq: Int, _ data: String) -> HubEnvelope {
        let json = try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8))
        return HubEnvelope(type: "message.appended", session: "claude:s1", seq: seq, data: json)
    }

    func event(_ type: String, _ data: String) -> HubEnvelope {
        HubEnvelope(type: type, session: "claude:s1", data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
    }

    @Test func historyFoldsToolOutputIntoItsCall() {
        let chat = model()
        chat.load(HubMessagesPage(messages: [
            message(1, #"{"kind": "user", "text": "run the tests"}"#),
            message(2, #"{"kind": "tool_use", "tool": {"name": "Bash", "call_id": "c1", "args_summary": "make test"}}"#),
            message(3, #"{"kind": "tool_result", "text": "ok", "result_ref": "c1"}"#),
            message(4, #"{"kind": "assistant", "text": "All green."}"#),
            message(5, #"{"kind": "assistant", "text": ""}"#),
        ], nextBefore: nil, hasMore: true))
        #expect(chat.items.map(\.id) == ["m1", "m2", "m4"])
        #expect(chat.items[1].kind == .tool(HubToolCall(name: "Bash", callId: "c1", argsSummary: "make test"), result: "ok"))
        #expect(chat.hasEarlier)
    }

    @Test func liveEventsAppendOnceAndSettleApprovals() {
        let chat = model()
        chat.apply(message(7, #"{"kind": "assistant", "text": "hi"}"#))
        chat.apply(message(7, #"{"kind": "assistant", "text": "hi"}"#))
        #expect(chat.items.count == 1)
        chat.apply(event("approval.requested", #"{"request_id": "r1", "prompt": "Run rm?"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r1", prompt: "Run rm?",
                                                 options: [], picked: nil, state: .open))
        chat.apply(event("approval.resolved", #"{"request_id": "r1", "decision": "deny"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r1", prompt: "Run rm?",
                                                 options: [], picked: "deny", state: .resolved("deny")))
    }

    @Test func anOlderPageBringsTheCallForAnOrphanResult() {
        let chat = model()
        chat.load(HubMessagesPage(messages: [
            message(10, #"{"kind": "tool_result", "text": "done", "result_ref": "c9"}"#),
            message(11, #"{"kind": "assistant", "text": "next"}"#),
        ], nextBefore: 10, hasMore: true))
        #expect(chat.items.map(\.id) == ["m11"])
        let added = chat.prepend(HubMessagesPage(messages: [
            message(9, #"{"kind": "tool_use", "tool": {"name": "Read", "call_id": "c9"}}"#),
        ], nextBefore: nil, hasMore: false))
        #expect(added)
        #expect(chat.items.first?.kind == .tool(HubToolCall(name: "Read", callId: "c9"), result: "done"))
        #expect(!chat.hasEarlier)
    }

    @Test func outputWhoseCallTheHubDroppedShowsOnItsOwn() {
        let chat = model()
        chat.load(HubMessagesPage(messages: [
            message(10, #"{"kind": "tool_result", "text": "done", "result_ref": "gone"}"#),
            message(11, #"{"kind": "assistant", "text": "next"}"#),
        ], nextBefore: nil, hasMore: false))
        #expect(chat.items.map(\.id) == ["m10", "m11"])
        #expect(chat.items.first?.kind == .tool(HubToolCall(name: "Tool output"), result: "done"))
    }
}

extension HubStubbed {
@MainActor
@Suite struct SessionChatOpenTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }
    var dir: URL { scratch.url }

    func opened(sessions: String, inbox: String = #"{"items": []}"#) async throws -> SessionChatModel {
        let store = HubDeviceStore(url: dir.appending(path: "app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        HubStub.handler = { req in
            switch req.url?.path {
            case "/v1/sessions": return (200, Data(sessions.utf8))
            case "/v1/inbox": return (200, Data(inbox.utf8))
            case "/v1/sessions/claude:s1/messages":
                return (200, Data(#"""
                {"messages": [
                  {"type": "message.appended", "seq": 1, "data": {"kind": "user", "text": "go"}},
                  {"type": "message.appended", "seq": 3, "data": {"kind": "assistant", "text": "done"}}
                ], "has_more": false}
                """#.utf8))
            default: return (404, Data())
            }
        }
        let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HubStub.session())
        }
        let chat = SessionChatModel(key: "claude:s1", hub: hub)
        await chat.start()
        chat.stop()
        // The open's and close's reads land before a test reads the POSTs.
        await chat.readOnOpen?.value
        await chat.readFlush?.value
        return chat
    }

    func event(_ type: String, seq: Int, _ data: String) -> HubEnvelope {
        HubEnvelope(type: type, session: "claude:s1", seq: seq,
                    data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
    }

    /// The header's model comes from the row read at open, and no event
    /// carries it: a finished turn reads the row again.
    @Test func theModelIsReadAgainAfterAReply() async throws {
        let chat = try await opened(sessions: #"{"sessions": [{"key": "claude:s1"}]}"#)
        chat.modelRetryDelays = []
        #expect(chat.row?.model == nil)
        func answer(_ body: String, status: Int = 200) {
            HubStub.handler = { req in
                req.url?.path == "/v1/sessions" ? (status, Data(body.utf8)) : (404, Data())
            }
        }
        // A hub that fails, or still doesn't know it, leaves the header.
        answer("{}", status: 503)
        await chat.refreshModel().value
        #expect(chat.row?.model == nil)
        answer(#"{"sessions": [{"key": "claude:s1"}]}"#)
        await chat.refreshModel().value
        #expect(chat.row?.model == nil)
        // The first reply names one, a later switch changes it.
        answer(#"{"sessions": [{"key": "claude:s1", "model": "claude-opus-4-5-20251101", "status": "done"}]}"#)
        await chat.refreshModel().value
        #expect(chat.row?.model == "claude-opus-4-5-20251101")
        #expect(chat.row?.status != "done", "only the model is taken from the new row")
        answer(#"{"sessions": [{"key": "claude:s1", "model": "claude-sonnet-4-5"}]}"#)
        await chat.refreshModel().value
        #expect(chat.row?.modelLabel == "claude-sonnet-4-5")
    }

    /// A hub page whose cursor doesn't move back (nothing new on it, and
    /// next_before where it was) ends the history instead of being asked
    /// for again forever.
    @Test(.timeLimit(.minutes(1))) func aCursorThatDoesNotMoveEndsLoadEarlier() async throws {
        let store = HubDeviceStore(url: dir.appending(path: "app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        let earlierAsks = Mutex(0)
        HubStub.handler = { req in
            switch req.url?.path {
            case "/v1/sessions": return (200, Data(#"{"sessions": [{"key": "claude:s1"}]}"#.utf8))
            case "/v1/inbox": return (200, Data(#"{"items": []}"#.utf8))
            case "/v1/sessions/claude:s1/messages":
                if req.url?.query?.contains("before=") == true {
                    let asks = earlierAsks.withLock { $0 += 1; return $0 }
                    // Stop a looping caller well before the time limit.
                    if asks > 20 { return (500, Data()) }
                }
                return (200, Data(#"""
                {"messages": [
                  {"type": "message.appended", "seq": 5, "data": {"kind": "user", "text": "go"}}
                ], "has_more": true, "next_before": 5}
                """#.utf8))
            default: return (404, Data())
            }
        }
        let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HubStub.session())
        }
        let chat = SessionChatModel(key: "claude:s1", hub: hub)
        await chat.start()
        chat.stop()
        #expect(chat.hasEarlier)
        await chat.loadEarlier()
        #expect(earlierAsks.withLock { $0 } == 1)
        #expect(!chat.hasEarlier)
    }

    /// The turn's end can reach the stream before the hub's catalog
    /// refresh (every 10 s) holds the new model, so one read may be stale:
    /// the read repeats after each wait until the model differs.
    @Test func aStaleReadIsRepeatedUntilTheCatalogCatchesUp() async throws {
        let chat = try await opened(sessions: #"{"sessions": [{"key": "claude:s1", "model": "claude-sonnet-4-5"}]}"#)
        let reads = Mutex(0)
        let waits = Mutex<[Duration]>([])
        HubStub.handler = { req in
            guard req.url?.path == "/v1/sessions" else { return (404, Data()) }
            // The catalog is behind for the first two reads.
            let n = reads.withLock { $0 += 1; return $0 }
            let model = n < 3 ? "claude-sonnet-4-5" : "claude-opus-4-5"
            return (200, Data(#"{"sessions": [{"key": "claude:s1", "model": "\#(model)"}]}"#.utf8))
        }
        chat.modelRetryDelays = [.seconds(6), .seconds(6), .seconds(6)]
        chat.modelSleep = { delay in waits.withLock { $0.append(delay) } }
        await chat.refreshModel().value
        #expect(chat.row?.model == "claude-opus-4-5")
        #expect(reads.withLock { $0 } == 3, "stops as soon as the model differs")
        #expect(waits.withLock { $0 } == [.seconds(6), .seconds(6)])

        // A model that really didn't change reads once per wait, then gives up.
        reads.withLock { $0 = 0 }
        waits.withLock { $0 = [] }
        HubStub.handler = { req in
            reads.withLock { $0 += 1 }
            return (200, Data(#"{"sessions": [{"key": "claude:s1", "model": "claude-opus-4-5"}]}"#.utf8))
        }
        await chat.refreshModel().value
        #expect(reads.withLock { $0 } == 4)
        #expect(waits.withLock { $0.count } == 3)
    }

    @Test func aSessionTheHubDoesntListIsMissing() async throws {
        let chat = try await opened(sessions: #"{"sessions": [{"key": "claude:other"}]}"#)
        #expect(chat.missing)
        #expect(chat.loadError == "The hub doesn't list this session.")
    }

    @Test func aWaitingApprovalGoesWhereItWasAsked() async throws {
        let chat = try await opened(sessions: #"{"sessions": [{"key": "claude:s1", "status": "approval"}]}"#,
                                    inbox: #"{"items": [{"key": "claude:s1", "status": "approval", "request_id": "r1"}]}"#)
        #expect(chat.items.map(\.id) == ["m1", "m3", "approval-r1"])
        // The replay from the start: history already shown is skipped,
        // the ask lands before the message that followed it.
        chat.apply(event("message.appended", seq: 1, #"{"kind": "user", "text": "go"}"#))
        chat.apply(event("approval.requested", seq: 2, #"{"request_id": "r1", "prompt": "Run make?"}"#))
        chat.apply(event("message.appended", seq: 3, #"{"kind": "assistant", "text": "done"}"#))
        #expect(chat.items.map(\.id) == ["m1", "approval-r1", "m3"])
        #expect(chat.items[1].kind == .approval(requestId: "r1", prompt: "Run make?",
                                              options: [], picked: nil, state: .open))
        // Live again past the history.
        chat.apply(event("message.appended", seq: 4, #"{"kind": "assistant", "text": "more"}"#))
        #expect(chat.items.map(\.id) == ["m1", "approval-r1", "m3", "m4"])
    }

    /// The hub's journal was wiped while we followed seq 500: it answers
    /// `last=500` with journal.truncated at its head (2). The chat takes
    /// the newer history and follows on from 2; staying at 500 would get
    /// truncated back on every follow and never see a new event. When the
    /// history fetch fails, it retries before following on: with the
    /// cursor lowered no reconnect would say truncated again.
    ///
    /// The stream reconnects on its own a second after the wipe, which is
    /// also how long the failed reload backs off, so either can ask from 2
    /// first. The test waits for the reload itself to land instead of the
    /// first ask from 2 (which once came before the retry, failing it).
    @Test(.timeLimit(.minutes(1)), arguments: [false, true])
    func aJournalWipedUnderTheCursorRestartsAtItsHead(failFirstReload: Bool) async throws {
        let store = HubDeviceStore(url: dir.appending(path: "app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        let pages = Mutex(0)
        let followed = CountGate()
        HubStub.requests = []
        HubStub.handler = { req in
            let last = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "last" }?.value
            switch req.url?.path {
            case "/v1/sessions": return (200, Data(#"{"sessions": [{"key": "claude:s1"}]}"#.utf8))
            case "/v1/sessions/claude:s1/messages":
                let n = pages.withLock { n in n += 1; return n }
                if failFirstReload, n == 2 { return (503, Data()) }
                let first = n == 1
                return (200, Data((first
                    ? #"{"messages": [{"type": "message.appended", "seq": 500, "data": {"kind": "assistant", "text": "before"}}], "has_more": false}"#
                    : #"{"messages": [{"type": "message.appended", "seq": 2, "data": {"kind": "assistant", "text": "after wipe"}}], "has_more": false}"#).utf8))
            case "/v1/sessions/claude:s1/events":
                Task { await followed.bump() }
                if let last, let n = Int(last), n > 2 {
                    return (200, Data("""
                        data: {"type": "journal.truncated", "seq": 2}

                        data: {"type": "journal.caught-up", "seq": 2}


                        """.utf8))
                }
                return (200, Data("data: {\"type\": \"journal.caught-up\", \"seq\": 2}\n\n".utf8))
            default: return (404, Data())
            }
        }
        let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HubStub.session())
        }
        let chat = SessionChatModel(key: "claude:s1", hub: hub)
        defer { chat.stop() }
        await chat.start()
        func follows() -> [String] {
            HubStub.requests.filter { $0.url?.path == "/v1/sessions/claude:s1/events" }
                .compactMap { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?
                    .queryItems?.first { $0.name == "last" }?.value }
        }
        // The newer history lands (after one retry when the first fails)...
        #expect(await observe(until: { chat.items.map(\.id) == ["m2"] }))
        #expect(pages.withLock { $0 } == (failFirstReload ? 3 : 2))
        // ...and the next follow asks from the head, not from 500 again.
        #expect(await followed.reach(2))
        #expect(follows().prefix(2) == ["500", "2"])
    }

    /// The stream's own reconnect: after journal.truncated at 2 it asks
    /// from 2 next time, not from the 500 it started at. A session of its
    /// own, so no other test's asks can land in its list.
    @Test func theStreamReconnectsFromATruncatedFramesSeq() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            guard req.url?.path == "/v1/sessions/claude:reconnect/events" else { return (404, Data()) }
            return (200, Data("data: {\"type\": \"journal.truncated\", \"seq\": 2}\n\n".utf8))
        }
        let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!,
                               credential: "secret",
                               session: HubStub.session(), streamSession: HubStub.session())
        var truncated: [Int?] = []
        var disconnects = 0
        for await event in client.events("claude:reconnect", last: 500) {
            switch event {
            case .truncated(let seq): truncated.append(seq)
            case .disconnected: disconnects += 1
            default: break
            }
            if truncated.count == 2 { break }
        }
        let lasts = HubStub.requests.filter { $0.url?.path == "/v1/sessions/claude:reconnect/events" }
            .compactMap { URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "last" }?.value }
        #expect(truncated == [2, 2])
        #expect(disconnects == 1)
        #expect(lasts == ["500", "2"])
    }

    /// A reader that leaves its loop without cancelling its task (a
    /// `break`, a `return`) ends the stream: its connection closes and no
    /// reconnect follows. A producer that outlived its reader would hold
    /// the connection open and reconnect forever.
    @Test(.timeLimit(.minutes(1))) func aReaderThatLeavesItsLoopClosesTheStream() async throws {
        let watch = HeldStream.watch { _ in ": ka\n\n" }
        let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "secret",
                               session: HubStub.session(), streamSession: HeldStream.session())
        for await event in client.events("claude:held", last: 0) {
            if case .connected = event { break }
        }
        // The counts bump in tasks of their own, so the close can land
        // before the open: wait for both, then check nothing else opened.
        #expect(await watch.closed.reach(1))
        #expect(await watch.opened.reach(1))
        #expect(await watch.opened.count == 1)
    }

    /// After journal.truncated the chat reloads and follows again from a
    /// new stream; the old one closes rather than reconnecting alongside
    /// it, and stop() closes the new one.
    @Test(.timeLimit(.minutes(1))) func aFollowAfterTruncatedClosesTheOldStream() async throws {
        let store = HubDeviceStore(url: dir.appending(path: "app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        let pages = Mutex(0)
        HubStub.handler = { req in
            switch req.url?.path {
            case "/v1/sessions": return (200, Data(#"{"sessions": [{"key": "claude:s1"}]}"#.utf8))
            case "/v1/sessions/claude:s1/messages":
                let first = pages.withLock { n in n += 1; return n == 1 }
                return (200, Data((first
                    ? #"{"messages": [{"type": "message.appended", "seq": 500, "data": {"kind": "assistant", "text": "before"}}], "has_more": false}"#
                    : #"{"messages": [{"type": "message.appended", "seq": 2, "data": {"kind": "assistant", "text": "after wipe"}}], "has_more": false}"#).utf8))
            default: return (404, Data())
            }
        }
        // From 500 the journal says truncated at 2; from 2 it is caught up.
        let watch = HeldStream.watch { req in
            let last = URLComponents(url: req.url!, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "last" }?.value
            return last == "500"
                ? "data: {\"type\": \"journal.truncated\", \"seq\": 2}\n\n"
                : "data: {\"type\": \"journal.caught-up\", \"seq\": 2}\n\n"
        }
        let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HeldStream.session())
        }
        let chat = SessionChatModel(key: "claude:s1", hub: hub)
        defer { chat.stop() }
        await chat.start()
        #expect(await watch.opened.reach(2))
        #expect(await watch.closed.reach(1))
        #expect(await observe(until: { chat.items.map(\.id) == ["m2"] }))
        #expect(await watch.closed.count == 1)
        chat.stop()
        #expect(await watch.closed.reach(2))
        #expect(await watch.opened.count == 2)
    }

    /// The placeholder a waiting approval opens with learns the pane's
    /// menu when the ask replays: options ride `approval.requested`.
    @Test func aWaitingApprovalLearnsItsMenu() async throws {
        let chat = try await opened(sessions: #"{"sessions": [{"key": "claude:s1", "status": "approval"}]}"#,
                                    inbox: #"{"items": [{"key": "claude:s1", "status": "approval", "request_id": "r1"}]}"#)
        let options = [HubApprovalOption(n: "1", label: "Yes"), HubApprovalOption(n: "8", label: "No")]
        chat.apply(event("approval.requested", seq: 2,
                         #"{"request_id": "r1", "prompt": "Run?", "options": [{"n": "1", "label": "Yes"}, {"n": "8", "label": "No"}]}"#))
        #expect(chat.items[1].kind == .approval(requestId: "r1", prompt: "Run?",
                                              options: options, picked: nil, state: .open))
    }
}
}

extension HubStubbed {
@MainActor
@Suite struct SessionChatSendTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }
    var dir: URL { scratch.url }

    /// A chat opened on an attached (or observed) session whose `POST
    /// .../message` answers `send`.
    func opened(ownership: String = "attached", send: (Int, String) = (202, #"{"ok": true, "waiting": false}"#),
                control: (Int, Data) = (200, Data(#"{"ok": true}"#.utf8)),
                approval: (Int, Data) = (200, Data(#"{"ok": true}"#.utf8)),
                attachable: Bool = true) async throws -> SessionChatModel {
        let store = HubDeviceStore(url: dir.appending(path: "app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        HubStub.requests = []
        HubStub.handler = { req in
            switch req.url?.path {
            case "/v1/sessions":
                return (200, Data(#"{"sessions": [{"key": "claude:s1", "status": "done", "ownership": "\#(ownership)", "attachable": \#(attachable)}]}"#.utf8))
            case "/v1/sessions/claude:s1/messages":
                return (200, Data(#"{"messages": [], "has_more": false}"#.utf8))
            case "/v1/sessions/claude:s1/message":
                return (send.0, Data(send.1.utf8))
            case "/v1/sessions/claude:s1/control":
                return control
            case "/v1/sessions/claude:s1/approval":
                return approval
            default:
                return (404, Data())
            }
        }
        let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HubStub.session())
        }
        let chat = SessionChatModel(key: "claude:s1", hub: hub)
        await chat.start()
        chat.stop()
        // The open's and close's reads land before a test reads the POSTs.
        await chat.readOnOpen?.value
        await chat.readFlush?.value
        return chat
    }

    func event(_ type: String, seq: Int? = nil, _ data: String) -> HubEnvelope {
        HubEnvelope(type: type, session: "claude:s1", seq: seq,
                    data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
    }

    @Test func aSendShowsAtOnceAndItsEchoSettlesIt() async throws {
        let chat = try await opened()
        await chat.send("  run the tests ", mode: "steer")
        #expect(chat.items.map(\.kind) == [.user("run the tests", send: .accepted(waiting: false))])
        #expect(chat.working)
        let post = try #require(HubStub.requests.last { $0.httpMethod == "POST" })
        let body = HubStub.body(post)
        #expect(body["text"] == "run the tests")
        #expect(body["mode"] == "steer")
        #expect(chat.items.first?.id == "send-\(body["client_id"] ?? "")")

        // The result usually beats the echo: the bubble waits for it.
        let cid = body["client_id"] ?? ""
        chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": true}"#))
        #expect(chat.items.map(\.kind) == [.user("run the tests", send: .delivered)])
        chat.apply(event("message.appended", seq: 5, #"{"kind": "user", "text": "run the tests"}"#))
        #expect(chat.items.map(\.kind) == [.user("run the tests", send: nil)])
        chat.apply(event("message.appended", seq: 6, #"{"kind": "assistant", "text": "On it."}"#))
        #expect(!chat.working)
    }

    @Test func aFailedResultMarksTheBubble() async throws {
        let chat = try await opened(send: (202, #"{"ok": true, "waiting": true}"#))
        await chat.send("later", mode: "queue")
        // Held for the turn's end: it waits in the tray, not the transcript.
        #expect(chat.items.isEmpty)
        let cid = try #require(chat.tray.sends.first?.clientId)
        chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": false, "note": "tmux gone"}"#))
        #expect(chat.items.first?.kind == .user("later", send: .failed("tmux gone")))
        #expect(!chat.working)
        // A late echo of other text doesn't touch it.
        chat.apply(event("message.appended", seq: 9, #"{"kind": "user", "text": "something else"}"#))
        #expect(chat.items.count == 2)
    }

    @Test func aDegradedSendKeepsItsNoteThroughTheEcho() async throws {
        let chat = try await opened()
        await chat.send("now please", mode: "steer")
        let cid = String(try #require(chat.items.first?.id).dropFirst("send-".count))
        chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": true, "degraded": true, "note": "Queued instead"}"#))
        chat.apply(event("message.appended", seq: 3, #"{"kind": "assistant", "text": "working"}"#))
        chat.apply(event("message.appended", seq: 4, #"{"kind": "user", "text": "@shot.png now please"}"#))
        #expect(chat.items.map(\.kind) == [.assistant("working"), .user("@shot.png now please", send: .degraded("Queued instead"))])
    }

    @Test func aRefusalSaysObservedAndAnObservedChatSendsNothing() async throws {
        let chat = try await opened(send: (409, #"{"error": {"code": "not_attached", "message": "observed"}}"#))
        await chat.send("hello", mode: "queue")
        #expect(chat.items.first?.kind == .user("hello", send: .failed("This session is read-only until you attach it.")))
        #expect(chat.row?.isAttached == false)
        #expect(!chat.canSend)
        let posts = { HubStub.requests.filter { $0.httpMethod == "POST" }.count }
        let before = posts()
        await chat.send("again", mode: "queue")
        await chat.interrupt()
        #expect(posts() == before)
    }

    @Test func stopPostsAnInterrupt() async throws {
        let chat = try await opened()
        await chat.interrupt()
        let post = try #require(HubStub.requests.last { $0.httpMethod == "POST" })
        #expect(post.url?.path == "/v1/sessions/claude:s1/control")
        #expect(HubStub.body(post) == ["op": "interrupt"])
        #expect(!chat.stopping)
    }

    @Test func aResultAfterTheEchoStillSettlesIt() async throws {
        let chat = try await opened()
        await chat.send("steer this", mode: "steer")
        let cid = String(try #require(chat.items.first?.id).dropFirst("send-".count))
        chat.apply(event("message.appended", seq: 2, #"{"kind": "assistant", "text": "hm"}"#))
        chat.apply(event("message.appended", seq: 3, #"{"kind": "user", "text": "steer this"}"#))
        #expect(chat.items.map(\.id) == ["m2", "m3"])
        // Headless injection reports after the whole turn.
        chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": true, "degraded": true, "note": "Ran as Queue"}"#))
        #expect(chat.items.last?.kind == .user("steer this", send: .degraded("Ran as Queue")))
    }

    @Test func aReloadTakesTheEchoFromHistory() async throws {
        let chat = try await opened()
        await chat.send("while offline", mode: "queue")
        let now = Date().timeIntervalSince1970
        chat.load(HubMessagesPage(messages: [
            event("message.appended", seq: 1, #"{"kind": "user", "text": "while offline", "ts": 1}"#),
            event("message.appended", seq: 7, #"{"kind": "user", "text": "while offline", "ts": \#(now)}"#),
        ], nextBefore: nil, hasMore: false))
        // The old identical message isn't taken for it; the new one is.
        #expect(chat.items.map(\.id) == ["m1", "m7"])
    }

    @Test func dotsWaitForEverySend() async throws {
        let chat = try await opened()
        await chat.send("one", mode: "queue")
        await chat.send("two", mode: "queue")
        let second = String(try #require(chat.items.last?.id).dropFirst("send-".count))
        chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(second)", "ok": false, "note": "x"}"#))
        #expect(chat.working)
        chat.apply(event("message.appended", seq: 4, #"{"kind": "assistant", "text": "reply"}"#))
        #expect(!chat.working)
    }

    @Test func echoesAllowOnlyAnAttachmentPrefix() {
        #expect(SessionChatModel.echoes("run", "run"))
        #expect(SessionChatModel.echoes("@a.png @b.txt run", "run"))
        #expect(!SessionChatModel.echoes("please run", "run"))
    }
}
}

extension HubStubbed {
@MainActor
@Suite struct SessionChatAttachTests {
    func opened(ownership: String = "observed", control: (Int, Data) = (200, Data(#"{"ok": true}"#.utf8)),
                approval: (Int, Data) = (200, Data(#"{"ok": true}"#.utf8))) async throws -> SessionChatModel {
        try await HubStubbed.SessionChatSendTests().opened(ownership: ownership, control: control, approval: approval)
    }

    func event(_ type: String, _ data: String) -> HubEnvelope {
        HubEnvelope(type: type, session: "claude:s1", data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
    }

    @Test func attachingOpensTheComposer() async throws {
        let chat = try await opened()
        #expect(chat.canAttach)
        #expect(!chat.canSend)
        await chat.attach()
        let post = try #require(HubStub.requests.last { $0.httpMethod == "POST" })
        #expect(HubStub.body(post) == ["op": "attach"])
        #expect(chat.canSend)
        #expect(!chat.canAttach)
    }

    @Test func aRefusedAttachSaysWhy() async throws {
        let chat = try await opened(control: (409, Data(#"{"error": {"code": "not_idle"}}"#.utf8)))
        await chat.attach()
        #expect(chat.items.last?.kind == .error("Attach failed: The session is mid-turn. Attach once it's idle."))
        #expect(!chat.canSend)
        #expect(!chat.attaching)
    }

    @Test func anAnswerSettlesTheApproval() async throws {
        let chat = try await opened(ownership: "attached")
        chat.apply(event("approval.requested", #"{"request_id": "r1", "prompt": "Run make?"}"#))
        await chat.decide("r1", "always")
        let post = try #require(HubStub.requests.last { $0.httpMethod == "POST" })
        #expect(HubStub.body(post) == ["request_id": "r1", "decision": "always"])
        #expect(chat.items.last?.kind == .approval(requestId: "r1", prompt: "Run make?",
                                                 options: [], picked: "always", state: .resolved("always")))
        // A resolved approval takes no second answer.
        let posts = HubStub.requests.count
        await chat.decide("r1", "deny")
        #expect(HubStub.requests.count == posts)
    }

    @Test func aSatellitesApprovalWaitsForItsResult() async throws {
        let chat = try await opened(ownership: "attached", approval: (202, Data(#"{"queued": true}"#.utf8)))
        chat.apply(event("approval.requested", #"{"request_id": "r2", "prompt": "Edit?"}"#))
        await chat.decide("r2", "allow")
        #expect(chat.items.last?.kind == .approval(requestId: "r2", prompt: "Edit?",
                                                 options: [], picked: "allow", state: .queued))
        // A reload rebuilds the card with its answer still on its way.
        chat.load(HubMessagesPage(messages: [], nextBefore: nil, hasMore: false))
        chat.apply(event("approval.requested", #"{"request_id": "r2", "prompt": "Edit?"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r2", prompt: "Edit?",
                                                 options: [], picked: "allow", state: .queued))
        chat.apply(event("command.result", #"{"op": "approval", "request_id": "r2", "ok": false, "note": "prompt closed"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r2", prompt: "Edit?", options: [],
                                                 picked: nil, state: .failed("Approval failed: prompt closed")))
        #expect(chat.items.count == 1)
    }

    @Test func aFailedAnswerCanBeTriedAgain() async throws {
        let chat = try await opened(ownership: "attached",
                                    approval: (409, Data(#"{"error": {"code": "gone", "message": "no such prompt"}}"#.utf8)))
        chat.apply(event("approval.requested", #"{"request_id": "r3", "prompt": "Push?"}"#))
        await chat.decide("r3", "deny")
        guard case .approval(_, _, _, _, let state) = chat.items.last?.kind else {
            Issue.record("no card")
            return
        }
        #expect(state == .failed("Approval failed: No such prompt"))
        #expect(state.answerable)
    }

    @Test func aSatellitesConfirmationKeepsTheChoice() async throws {
        let chat = try await opened(ownership: "attached", approval: (202, Data(#"{"queued": true}"#.utf8)))
        chat.apply(event("approval.requested", #"{"request_id": "r4", "prompt": "Run?"}"#))
        await chat.decide("r4", "deny")
        chat.apply(event("command.result", #"{"op": "approval", "request_id": "r4", "ok": true}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r4", prompt: "Run?",
                                                 options: [], picked: "deny", state: .resolved("deny")))
    }

    /// The pane's numbered menu rides `options`: the card keeps the rows
    /// and a tap posts the row's digit as the decision, verbatim.
    @Test func aMenusRowsPostTheirDigit() async throws {
        let chat = try await opened(ownership: "attached")
        let options = [HubApprovalOption(n: "1", label: "Yes, allow `sudo ls` commands"),
                       HubApprovalOption(n: "6", label: "Edit command"),
                       HubApprovalOption(n: "8", label: "No")]
        chat.apply(event("approval.requested", #"{"request_id": "r5", "prompt": "Allow it?", "options": [{"n": "1", "label": "Yes, allow `sudo ls` commands"}, {"n": "6", "label": "Edit command"}, {"n": "8", "label": "No"}]}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r5", prompt: "Allow it?",
                                                 options: options, picked: nil, state: .open))
        await chat.decide("r5", "6")
        let post = try #require(HubStub.requests.last { $0.httpMethod == "POST" })
        #expect(HubStub.body(post) == ["request_id": "r5", "decision": "6"])
        #expect(chat.items.last?.kind == .approval(requestId: "r5", prompt: "Allow it?",
                                                 options: options, picked: "6", state: .resolved("6")))
    }

    /// An `approval.resolved` event's decision wins over the row picked
    /// here: another client may have answered first.
    @Test func anEventDecisionWinsOverTheLocalPick() async throws {
        let chat = try await opened(ownership: "attached", approval: (202, Data(#"{"queued": true}"#.utf8)))
        let options = [HubApprovalOption(n: "1", label: "Yes"), HubApprovalOption(n: "8", label: "No")]
        chat.apply(event("approval.requested", #"{"request_id": "r6", "prompt": "Run?", "options": [{"n": "1", "label": "Yes"}, {"n": "8", "label": "No"}]}"#))
        await chat.decide("r6", "8")
        chat.apply(event("approval.resolved", #"{"request_id": "r6", "decision": "1"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r6", prompt: "Run?",
                                                 options: options, picked: "1", state: .resolved("1")))
    }

    /// A resolve carrying no decision falls back to the row picked here.
    @Test func aBareResolveFallsBackToTheLocalPick() async throws {
        let chat = try await opened(ownership: "attached", approval: (202, Data(#"{"queued": true}"#.utf8)))
        chat.apply(event("approval.requested", #"{"request_id": "r7", "prompt": "Run?"}"#))
        await chat.decide("r7", "deny")
        chat.apply(event("approval.resolved", #"{"request_id": "r7"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r7", prompt: "Run?",
                                                 options: [], picked: "deny", state: .resolved("deny")))
    }

    /// A `command.result` settles only an answer sent from here: a
    /// request another client answered resolves on approval.resolved.
    @Test func aResultForAnApprovalWeDidNotAnswerIsIgnored() async throws {
        let chat = try await opened(ownership: "attached")
        chat.apply(event("approval.requested", #"{"request_id": "r8", "prompt": "Run?"}"#))
        chat.apply(event("command.result", #"{"op": "approval", "request_id": "r8", "ok": true}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r8", prompt: "Run?",
                                                 options: [], picked: nil, state: .open))
        chat.apply(event("approval.resolved", #"{"request_id": "r8", "decision": "allow"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r8", prompt: "Run?",
                                                 options: [], picked: "allow", state: .resolved("allow")))
    }

    /// A queued answer the satellite rejects after the card resolved
    /// comes back answerable with the failure note (the web app unfolds
    /// the card), the stale pick gone.
    @Test func aRejectionAfterResolvingReopensTheCard() async throws {
        let chat = try await opened(ownership: "attached", approval: (202, Data(#"{"queued": true}"#.utf8)))
        chat.apply(event("approval.requested", #"{"request_id": "r9", "prompt": "Run?"}"#))
        await chat.decide("r9", "deny")
        chat.apply(event("approval.resolved", #"{"request_id": "r9", "decision": "allow"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r9", prompt: "Run?",
                                                 options: [], picked: "allow", state: .resolved("allow")))
        chat.apply(event("command.result", #"{"op": "approval", "request_id": "r9", "ok": false, "note": "stale"}"#))
        guard case .approval(_, _, _, let picked, let state) = chat.items.last?.kind else {
            Issue.record("no card")
            return
        }
        #expect(state == .failed("Approval failed: stale"))
        #expect(state.answerable)
        #expect(picked == nil)
    }

    /// A second settle for the same request refreshes the verdict
    /// (command.result, then approval.resolved for the same answer).
    @Test func aSecondSettleRefreshesTheVerdict() async throws {
        let chat = try await opened(ownership: "attached", approval: (202, Data(#"{"queued": true}"#.utf8)))
        chat.apply(event("approval.requested", #"{"request_id": "r10", "prompt": "Run?"}"#))
        await chat.decide("r10", "deny")
        chat.apply(event("command.result", #"{"op": "approval", "request_id": "r10", "ok": true}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r10", prompt: "Run?",
                                                 options: [], picked: "deny", state: .resolved("deny")))
        chat.apply(event("approval.resolved", #"{"request_id": "r10", "decision": "deny"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r10", prompt: "Run?",
                                                 options: [], picked: "deny", state: .resolved("deny")))
    }

    /// Another client wins while our answer is in flight: approval.resolved
    /// settles the card, then our POST is refused. The kept verdict keeps
    /// its pick too — the losing row must not light under it.
    @Test func aRacedResolveKeepsItsPickWhenThePostIsRefused() async throws {
        let chat = try await opened(ownership: "attached")
        chat.apply(event("approval.requested", #"{"request_id": "r11", "prompt": "Run?"}"#))
        // Hold the approval POST so the resolve can land first; the
        // semaphores make the order exact on any scheduler.
        let posted = DispatchSemaphore(value: 0)
        let released = DispatchSemaphore(value: 0)
        let base = HubStub.handler
        HubStub.handler = { req in
            if req.url?.path == "/v1/sessions/claude:s1/approval" {
                posted.signal()
                released.wait()
                return (409, Data(#"{"error": {"code": "gone", "message": "no such prompt"}}"#.utf8))
            }
            return base?(req) ?? (404, Data())
        }
        let answer = Task { await chat.decide("r11", "deny") }
        // Semaphore waits are off the cooperative pool (and the wait is
        // banned in async contexts outright on newer SDKs): park a GCD
        // thread on `posted` until the POST lands.
        _ = await offMain { posted.wait(timeout: .now() + 10) }
        chat.apply(event("approval.resolved", #"{"request_id": "r11", "decision": "allow"}"#))
        released.signal()
        await answer.value
        #expect(chat.items.last?.kind == .approval(requestId: "r11", prompt: "Run?",
                                                 options: [], picked: "allow", state: .resolved("allow")))
    }
}
}

/// Never answers: a request sits in flight until its task is cancelled.
final class SilentHubStub: URLProtocol, @unchecked Sendable {
    /// Counts requests that reached the stub, so a test waits for one.
    static let started = CountGate()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Task { await Self.started.bump() } }
    override func stopLoading() {}
}

@Suite struct HubClientCancelTests {
    /// A request cancelled with its task throws CancellationError, not
    /// "hub unreachable": URLSession wraps the cancel as URLError.cancelled.
    @Test(.timeLimit(.minutes(1))) func aCancelledRequestIsACancellation() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [SilentHubStub.self]
        let client = HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c",
                               session: URLSession(configuration: config))
        let before = await SilentHubStub.started.count
        let task = Task { try await client.sessions() }
        #expect(await SilentHubStub.started.reach(before + 1))
        task.cancel()
        do {
            _ = try await task.value
            Issue.record("a cancelled request must throw")
        } catch {
            #expect(error is CancellationError, "\(error)")
        }
    }
}
