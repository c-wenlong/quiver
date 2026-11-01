import XCTest
@testable import SWERemote

final class RelativeTimeTests: XCTestCase {
    private let now = 1_800_000_000.0

    func testAgoReadsJustNowForTheFirstMinute() {
        // A pair request is listed seconds after it's made: "now ago"
        // was the first thing an approver read.
        XCTAssertEqual(RelativeTime.ago(now - 5, now: now), "just now")
        XCTAssertEqual(RelativeTime.ago(now - 59, now: now), "just now")
    }

    func testAgoAddsAgoToEveryOtherAge() {
        XCTAssertEqual(RelativeTime.ago(now - 60, now: now), "1m ago")
        XCTAssertEqual(RelativeTime.ago(now - 3 * 3600, now: now), "3h ago")
        XCTAssertEqual(RelativeTime.ago(now - 2 * 86_400, now: now), "2d ago")
    }

    func testAgoIsEmptyWithoutATime() {
        XCTAssertEqual(RelativeTime.ago(nil, now: now), "")
        XCTAssertEqual(RelativeTime.ago(0, now: now), "")
    }
}
