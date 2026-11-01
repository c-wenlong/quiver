import Foundation
import Security
import Testing
@testable import SWECore

@Suite struct KeychainCLITests {
    @Test func asksSecurityForThePasswordOfOneServiceAndAccount() async {
        let runner = FakeRunner(.init(status: 0, stdout: Data("{\"claudeAiOauth\":{}}\n".utf8), stderr: ""))
        let value = await KeychainCLI(runner: runner).genericPassword(service: "Claude Code-credentials", account: "dev")
        #expect(runner.calls == [["find-generic-password", "-s", "Claude Code-credentials", "-a", "dev", "-w"]])
        #expect(value == "{\"claudeAiOauth\":{}}")
    }

    @Test func aMissingItemIsNil() async {
        // security exits 44 when the item does not exist.
        let runner = FakeRunner(.init(status: 44, stdout: Data(), stderr: "The specified item could not be found"))
        #expect(await KeychainCLI(runner: runner).genericPassword(service: "s", account: "a") == nil)
    }

    @Test func anEmptyAnswerOrAFailedLaunchIsNil() async {
        let empty = FakeRunner(.init(status: 0, stdout: Data("\n".utf8), stderr: ""))
        #expect(await KeychainCLI(runner: empty).genericPassword(service: "s", account: "a") == nil)
        #expect(await KeychainCLI(runner: ThrowingRunner()).genericPassword(service: "s", account: "a") == nil)
    }

    @Test func aHangingPromptGivesUpAfterTheTimeout() async {
        let started = Date()
        let value = await KeychainCLI(runner: HangingRunner(), timeout: .milliseconds(100))
            .genericPassword(service: "s", account: "a")
        #expect(value == nil)
        // The bound catches a hang, not latency: it sits under the
        // runner's 60s sleep, so a broken timeout still fails while a
        // starved CI runner cannot trip it.
        #expect(Date().timeIntervalSince(started) < 30)
    }

    @Test func theRealToolRunsSecurity() {
        #expect(KeychainCLI.securityPath == "/usr/bin/security")
    }
}

/// SecItemKeychain's OSStatus mapping: the thrown error is a
/// commandFailed carrying Security's message for the code, never a
/// stored value. The keychain itself stays out of tests.
@Suite struct SecItemKeychainTests {
    @Test func aStatusBecomesCommandFailedWithSecuritysMessage() {
        let error = SecItemKeychain.error(errSecAuthFailed)
        guard case .commandFailed(let status, let message) = error else {
            Issue.record("expected commandFailed, got \(error)")
            return
        }
        #expect(status == errSecAuthFailed)
        #expect(!message.isEmpty)
    }

    @Test func everyStatusMapsToSomeMessage() {
        // SecCopyErrorMessageString knows the common codes; the fallback
        // covers the rest, so a failure is never a blank error.
        for status in [errSecSuccess, errSecItemNotFound, errSecNotAvailable] {
            guard case .commandFailed(_, let message) = SecItemKeychain.error(status) else {
                Issue.record("expected commandFailed for \(status)")
                continue
            }
            #expect(!message.isEmpty)
        }
    }
}

extension SecItemKeychainTests {
    /// A keychain in memory: the SecItemCalls seam answers from `items`
    /// keyed by (service, account), and records every call.
    final class FakeKeychain: @unchecked Sendable {
        var items: [String: Data] = [:]
        private(set) var calls: [String] = []
        /// Reads must run with prompts off; writes may prompt.
        private(set) var promptsForbidden = true
        private(set) var writesMayPrompt = true
        /// The prompt switch as the call in flight sees it; nil outside one.
        private var promptsAllowed: Bool?

        func make() -> SecItemCalls {
            SecItemCalls(
                copyMatching: { [self] query, result in
                    calls.append("read")
                    if promptsAllowed != false {
                        promptsForbidden = false
                    }
                    guard let q = query as? [String: Any],
                          let data = items[key(q)] else {
                        result.pointee = nil
                        return errSecItemNotFound
                    }
                    result.pointee = data as CFTypeRef
                    return errSecSuccess
                },
                update: { [self] query, attributes in
                    calls.append("update")
                    if promptsAllowed != true {
                        writesMayPrompt = false
                    }
                    guard let q = query as? [String: Any],
                          let attrs = attributes as? [String: Any],
                          let data = attrs[kSecValueData as String] as? Data,
                          items[key(q)] != nil else { return errSecItemNotFound }
                    items[key(q)] = data
                    return errSecSuccess
                },
                add: { [self] query, _ in
                    calls.append("add")
                    if promptsAllowed != true {
                        writesMayPrompt = false
                    }
                    guard let q = query as? [String: Any],
                          let data = q[kSecValueData as String] as? Data else {
                        return errSecParam
                    }
                    items[key(q)] = data
                    return errSecSuccess
                },
                delete: { [self] query in
                    calls.append("delete")
                    if promptsAllowed != true {
                        writesMayPrompt = false
                    }
                    guard let q = query as? [String: Any],
                          items.removeValue(forKey: key(q)) != nil else {
                        return errSecItemNotFound
                    }
                    return errSecSuccess
                },
                interaction: { [self] allowed, call in
                    promptsAllowed = allowed
                    defer { promptsAllowed = nil }
                    return call()
                })
        }

        private func key(_ query: [String: Any]) -> String {
            let service = query[kSecAttrService as String] as? String ?? ""
            let account = query[kSecAttrAccount as String] as? String ?? ""
            return service + "\u{1F}" + account
        }
    }

    @Test func aReadReturnsTheStoredValue() async {
        let keychain = FakeKeychain()
        keychain.items["svc\u{1F}acct"] = Data("sekrit".utf8)
        let store = SecItemKeychain(calls: keychain.make())
        #expect(await store.genericPassword(service: "svc", account: "acct") == "sekrit")
    }

    @Test func aReadThatWouldPromptIsNilNotAHang() async {
        let keychain = FakeKeychain()
        var calls = keychain.make()
        calls.copyMatching = { _, _ in errSecInteractionNotAllowed }
        let store = SecItemKeychain(calls: calls)
        #expect(await store.genericPassword(service: "svc", account: "acct") == nil)
    }

    @Test func aWriteUpdatesAnExistingItem() async throws {
        let keychain = FakeKeychain()
        keychain.items["svc\u{1F}acct"] = Data("old".utf8)
        let store = SecItemKeychain(calls: keychain.make())
        try await store.setGenericPassword(service: "svc", account: "acct", value: "new")
        #expect(keychain.items["svc\u{1F}acct"] == Data("new".utf8))
        #expect(keychain.calls == ["update"])
    }

    @Test func aWriteAddsWhenThereIsNothingToUpdate() async throws {
        let keychain = FakeKeychain()
        let store = SecItemKeychain(calls: keychain.make())
        try await store.setGenericPassword(service: "svc", account: "acct", value: "new")
        #expect(keychain.items["svc\u{1F}acct"] == Data("new".utf8))
        #expect(keychain.calls == ["update", "add"])
    }

    @Test func aFailedWriteThrowsItsStatus() async {
        let keychain = FakeKeychain()
        var calls = keychain.make()
        calls.update = { _, _ in errSecAuthFailed }
        let store = SecItemKeychain(calls: calls)
        await #expect(throws: SweError.self) {
            try await store.setGenericPassword(service: "svc", account: "acct", value: "v")
        }
    }

    @Test func deletingAMissingItemIsNotAnError() async throws {
        let keychain = FakeKeychain()
        let store = SecItemKeychain(calls: keychain.make())
        try await store.deleteGenericPassword(service: "svc", account: "acct")
        keychain.items["svc\u{1F}acct"] = Data("v".utf8)
        try await store.deleteGenericPassword(service: "svc", account: "acct")
        #expect(keychain.items["svc\u{1F}acct"] == nil)
    }

    @Test func readsNeverPromptButWritesMay() async throws {
        // A consent prompt on a read can wait forever under a page load;
        // a save the user just clicked may prompt once, which is how keys
        // written by `security` before the switch become readable.
        let keychain = FakeKeychain()
        let store = SecItemKeychain(calls: keychain.make())
        _ = await store.genericPassword(service: "svc", account: "acct")
        try await store.setGenericPassword(service: "svc", account: "acct", value: "v")
        try await store.setGenericPassword(service: "svc", account: "acct", value: "w")
        try await store.deleteGenericPassword(service: "svc", account: "acct")
        #expect(keychain.calls == ["read", "update", "add", "update", "delete"])
        #expect(keychain.promptsForbidden)
        #expect(keychain.writesMayPrompt)
    }

    @Test func theLegacySwitchIsOffForTheCallThenPutBack() {
        // The real process-wide switch, never an item: this macOS still
        // has it, the call sees it off, and it reads as before afterwards.
        guard let before = LegacyKeychainPrompts.isAllowed() else {
            Issue.record("SecKeychainSetUserInteractionAllowed did not resolve")
            return
        }
        var during: Bool?
        let status = LegacyKeychainPrompts.run(allowed: false) {
            during = Self.peekSwitch()
            return errSecSuccess
        }
        #expect(status == errSecSuccess)
        #expect(during == false)
        #expect(LegacyKeychainPrompts.isAllowed() == before)
    }

    /// The switch read straight from Security, since `isAllowed` takes
    /// the lock `run` is holding.
    private static func peekSwitch() -> Bool? {
        typealias Get = @convention(c) (UnsafeMutablePointer<DarwinBoolean>) -> OSStatus
        guard let handle = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_LAZY),
              let get = dlsym(handle, "SecKeychainGetUserInteractionAllowed") else { return nil }
        var state = DarwinBoolean(true)
        return unsafeBitCast(get, to: Get.self)(&state) == errSecSuccess ? state.boolValue : nil
    }
}

struct ThrowingRunner: CommandRunning {
    func run(_ arguments: [String]) async throws -> CommandResult { throw SweError.notInstalled("security") }
}

struct HangingRunner: CommandRunning {
    func run(_ arguments: [String]) async throws -> CommandResult {
        try await Task.sleep(for: .seconds(60))
        return CommandResult(status: 0, stdout: Data("late".utf8), stderr: "")
    }
}

@Suite struct LocalFilesTests {
    @Test func readsAFileOrNilWhenMissing() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("quiver-localfiles-\(UUID().uuidString).json")
        try Data("{}".utf8).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        #expect(LocalFiles().read(url.path) == Data("{}".utf8))
        #expect(LocalFiles().read(url.path + ".missing") == nil)
    }
}

/// Answers inside URLSession, so the real transport runs without a network.
final class StubProtocol: URLProtocol, @unchecked Sendable {
    nonisolated(unsafe) static var handler: (@Sendable (URLRequest) -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        guard let handler = Self.handler else { return }
        let (response, data) = handler(request)
        if let location = response.value(forHTTPHeaderField: "Location"), (300..<400).contains(response.statusCode),
           let target = URL(string: location) {
            client?.urlProtocol(self, wasRedirectedTo: URLRequest(url: target), redirectResponse: response)
        }
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
}

@Suite(.serialized) struct URLSessionTransportTests {
    func transport() -> URLSessionTransport {
        let configuration = URLSessionTransport.defaultConfiguration()
        configuration.protocolClasses = [StubProtocol.self]
        return URLSessionTransport(configuration: configuration)
    }

    @Test func returnsTheBodyAndStatus() async throws {
        StubProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: 429, httpVersion: nil, headerFields: ["Retry-After": "60"])!,
             Data("{}".utf8))
        }
        let (data, response) = try await transport().send(URLRequest(url: URL(string: "https://example.invalid/usage")!))
        #expect(response.statusCode == 429)
        #expect(response.value(forHTTPHeaderField: "Retry-After") == "60")
        #expect(data == Data("{}".utf8))
    }

    @Test func doesNotFollowARedirectWithTheBearerToken() async throws {
        let seen = SeenURLs()
        StubProtocol.handler = { request in
            seen.append(request.url!.absoluteString)
            return (HTTPURLResponse(url: request.url!, statusCode: 302, httpVersion: nil,
                                    headerFields: ["Location": "https://elsewhere.invalid/steal"])!, Data())
        }
        var request = URLRequest(url: URL(string: "https://example.invalid/usage")!)
        request.setValue("Bearer SECRET", forHTTPHeaderField: "Authorization")
        let (_, response) = try await transport().send(request)
        #expect(response.statusCode == 302)
        #expect(seen.all == ["https://example.invalid/usage"])
    }

    @Test func storesNoCookiesAndNoResponseCache() {
        let configuration = URLSessionTransport.defaultConfiguration()
        #expect(configuration.urlCache == nil)
        #expect(configuration.httpCookieStorage == nil)
        #expect(configuration.httpShouldSetCookies == false)
        #expect(configuration.urlCredentialStorage == nil)
    }
}

final class SeenURLs: @unchecked Sendable {
    private let lock = NSLock()
    private var urls: [String] = []
    func append(_ url: String) { lock.withLock { urls.append(url) } }
    var all: [String] { lock.withLock { urls } }
}
