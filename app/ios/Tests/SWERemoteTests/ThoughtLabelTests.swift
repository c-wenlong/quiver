import XCTest
@testable import SWERemote

/// The thinking card's title (#1440). The same cases as the Mac's
/// `ThoughtLabelTests` and the web app's `Fmt.thought` test.
@MainActor
final class ThoughtLabelTests: XCTestCase {
    func testReadsTheDurationTheHubSent() {
        XCTAssertEqual(ThoughtLabel.text(12), "Thought for 12s")
        XCTAssertEqual(ThoughtLabel.text(12.4), "Thought for 12s")
        XCTAssertEqual(ThoughtLabel.text(59.6), "Thought for 1m")
        XCTAssertEqual(ThoughtLabel.text(65), "Thought for 1m 5s")
        XCTAssertEqual(ThoughtLabel.text(3600), "Thought for 1h")
        XCTAssertEqual(ThoughtLabel.text(3725), "Thought for 1h 2m")
    }

    func testNeverReadsZeroSeconds() {
        XCTAssertEqual(ThoughtLabel.text(0), "Thought for 1s")
        XCTAssertEqual(ThoughtLabel.text(0.3), "Thought for 1s")
    }

    func testPlainWhenUnknown() {
        XCTAssertEqual(ThoughtLabel.text(nil), "Thought")
        XCTAssertEqual(ThoughtLabel.text(-1), "Thought")
        XCTAssertEqual(ThoughtLabel.text(.nan), "Thought")
        XCTAssertEqual(ThoughtLabel.text(.infinity), "Thought")
    }

    func testThinkingItemCarriesTheDuration() throws {
        let timed = try Hub.decoder.decode(
            MessageData.self, from: Data(#"{"kind":"thinking","text":"hmm","ts":1,"duration_s":12.5}"#.utf8))
        let untimed = try Hub.decoder.decode(
            MessageData.self, from: Data(#"{"kind":"thinking","text":"hmm","ts":2}"#.utf8))
        var items: [ChatItem] = []
        var cards: [String: String] = [:]
        var orphans: [String: String] = [:]
        ChatModel.add(timed, id: "m1", to: &items, cards: &cards, orphans: &orphans)
        ChatModel.add(untimed, id: "m2", to: &items, cards: &cards, orphans: &orphans)
        XCTAssertEqual(items.map(\.kind), [.thinking("hmm", seconds: 12.5), .thinking("hmm", seconds: nil)])
    }
}
