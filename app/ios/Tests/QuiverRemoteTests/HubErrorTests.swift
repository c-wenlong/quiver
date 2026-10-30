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
}
