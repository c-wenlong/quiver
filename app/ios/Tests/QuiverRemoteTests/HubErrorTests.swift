import XCTest
@testable import QuiverRemote

final class HubErrorTests: XCTestCase {
    /// Error sites show `error.localizedDescription` for any error: a
    /// HubError's must be its phone-worded message, not Foundation's
    /// "The operation couldn't be completed".
    func testAHubErrorDescribesItselfWithItsMessage() {
        let error: Error = HubError(status: 409, code: "not_idle", message: "The session is mid-turn. Attach once it's idle.")
        XCTAssertEqual(error.localizedDescription, "The session is mid-turn. Attach once it's idle.")
        XCTAssertEqual(HubError.network(URLError(.timedOut)).localizedDescription,
                       "Can't reach the hub. Check that Tailscale is on.")
    }

    /// Under its "Can't reach the hub" title the offline card said
    /// "Can't reach the hub" again; it now says only what to do.
    func testTheOfflineCardDoesNotRepeatItsTitle() {
        let offline = HubError.network(URLError(.cannotConnectToHost)).message
        XCTAssertEqual(HubError.detail(underUnreachableTitle: offline), "Check that Tailscale is on, then try again.")
        XCTAssertEqual(StateCard.unreachable(offline) {}.message, "Check that Tailscale is on, then try again.")
        // A hub that answered keeps its own words.
        XCTAssertEqual(HubError.detail(underUnreachableTitle: "The hub answered 500."), "The hub answered 500.")
    }
}

/// Never answers: a request sits in flight until its task is cancelled.
final class SilentHubStub: URLProtocol {
    private static let lock = NSLock()
    private static var _started = 0
    static var started: Int { lock.withLock { _started } }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Self.lock.withLock { Self._started += 1 } }
    override func stopLoading() {}

    /// A client whose every request goes to this stub.
    static func client() -> HubClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [SilentHubStub.self]
        return HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "c",
                         session: URLSession(configuration: config))
    }

    /// Waits until a request reaches the stub, so a cancel lands
    /// mid-flight. False after 5 s without one, so a call that never
    /// sends fails the test instead of hanging it.
    static func waitForRequest(after before: Int) async throws -> Bool {
        for _ in 0..<500 where started == before { try await Task.sleep(nanoseconds: 10_000_000) }
        return started != before
    }
}

final class HubCancelTests: XCTestCase {
    /// URLSession reports a cancelled task as URLError.cancelled; the
    /// client hands it on as CancellationError, not "Can't reach the hub".
    func testACancelledRequestIsACancellation() async throws {
        let client = SilentHubStub.client()
        let before = SilentHubStub.started
        let task = Task { try await client.sessions() }
        let sent = try await SilentHubStub.waitForRequest(after: before)
        task.cancel()
        guard sent else { return XCTFail("the request never reached the stub") }
        do {
            _ = try await task.value
            XCTFail("a cancelled request must throw")
        } catch {
            XCTAssertTrue(error is CancellationError, "\(error)")
        }
    }

    /// Leaving the tasks page mid-fetch cancels its load: the page must
    /// not come back saying the hub can't be reached.
    @MainActor
    func testACancelledTasksLoadShowsNoError() async throws {
        let model = TasksModel()
        let before = SilentHubStub.started
        let load = Task { await model.load(client: SilentHubStub.client()) }
        let sent = try await SilentHubStub.waitForRequest(after: before)
        load.cancel()
        await load.value
        guard sent else { return XCTFail("the load never reached the stub") }
        XCTAssertNil(model.error)
    }
}
