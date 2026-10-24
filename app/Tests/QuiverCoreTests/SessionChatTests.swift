import Foundation
import Testing
@testable import QuiverCore

/// Answers the hub client's requests inside URLSession, so the real
/// transport runs without a hub.
final class HubStub: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) -> (Int, Data))?
    nonisolated(unsafe) static var requests: [URLRequest] = []

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        Self.requests.append(request)
        let (status, data) = Self.handler?(request) ?? (404, Data())
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

    /// The body a POST carried: URLSession hands a protocol the stream.
    static func body(_ request: URLRequest) -> [String: String] {
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
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: String] ?? [:]
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

    @Test func baseURLsComeFromSwesAddress() {
        #expect(LocalHub.baseURL("127.0.0.1:8477")?.absoluteString == "http://127.0.0.1:8477")
        #expect(LocalHub.baseURL("0.0.0.0:8477")?.absoluteString == "http://127.0.0.1:8477")
        #expect(LocalHub.baseURL("::1:9000")?.absoluteString == "http://[::1]:9000")
        #expect(LocalHub.baseURL("nonsense") == nil)
    }
}

@MainActor
@Suite(.serialized) struct LocalHubTests {
    let dir = FileManager.default.temporaryDirectory.appending(path: "hub-\(UUID().uuidString)")
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

    @Test func aDownHubOpensDetailsInstead() async {
        let local = hub(FakeLocator(address: nil))
        await local.refreshKeys()
        #expect(local.keys == .unavailable)
        #expect(!local.opensChat("claude:x"))
        await #expect(throws: HubError.self) { try await local.client() }
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
        #expect(chat.items.last?.kind == .approval(requestId: "r1", prompt: "Run rm?", decision: nil, resolved: false))
        chat.apply(event("approval.resolved", #"{"request_id": "r1", "decision": "deny"}"#))
        #expect(chat.items.last?.kind == .approval(requestId: "r1", prompt: "Run rm?", decision: "deny", resolved: true))
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
}
