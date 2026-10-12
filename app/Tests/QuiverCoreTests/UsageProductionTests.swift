import Foundation
import Testing
@testable import QuiverCore

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
        // The bound catches a hang, not latency: the runner sleeps 60s,
        // so ten seconds still fails a broken timeout while a starved
        // CI runner cannot trip it.
        #expect(Date().timeIntervalSince(started) < 10)
    }

    @Test func theRealToolRunsSecurity() {
        #expect(KeychainCLI.securityPath == "/usr/bin/security")
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
