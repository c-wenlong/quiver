import XCTest
@testable import SWERemote

/// A hub whose journal was wiped numbers its seqs from the start again
/// and says so with `journal.truncated` carrying the new head; a cursor
/// left above that head would never hear another event.
final class JournalHeadTests: XCTestCase {
    private func env(_ json: String) -> Envelope {
        try! Hub.decoder.decode(Envelope.self, from: Data(json.utf8))
    }

    func testTheStreamResumesFromATruncatedHeadEvenWhenLower() {
        XCTAssertEqual(EventStream.resume(after: env(#"{"type":"message.appended","seq":40}"#), from: 500), 500,
                       "an ordinary event never moves the cursor back")
        XCTAssertEqual(EventStream.resume(after: env(#"{"type":"message.appended","seq":501}"#), from: 500), 501)
        XCTAssertEqual(EventStream.resume(after: env(#"{"type":"journal.truncated","seq":12}"#), from: 500), 12)
        XCTAssertEqual(EventStream.resume(after: env(#"{"type":"journal.truncated"}"#), from: 500), 500,
                       "an older hub's truncated, with no head, leaves it")
    }

    @MainActor
    func testTheChatAdoptsTheHeadForItsNextStream() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"session.status","seq":500,"data":{"status":"active"}}"#))
        XCTAssertEqual(chat.cursor, 500)
        chat.adoptJournalHead(12)
        XCTAssertEqual(chat.cursor, 12)
        chat.adoptJournalHead(nil)
        XCTAssertEqual(chat.cursor, 12)
    }

    @MainActor
    func testAfterAWipeANewMessageReusingAnOldSeqStillShows() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"message.appended","seq":3,"data":{"kind":"assistant","text":"old"}}"#))
        chat.apply(env(#"{"type":"message.appended","seq":9,"data":{"kind":"assistant","text":"older still"}}"#))
        // The journal was wiped; its head is 2, and its next message is 3.
        chat.adoptJournalHead(2)
        chat.apply(env(#"{"type":"message.appended","seq":3,"data":{"kind":"assistant","text":"new"}}"#))
        XCTAssertEqual(chat.cursor, 3)
        XCTAssertEqual(chat.items.count, 3, "seq 3 of the new journal isn't the old journal's seq 3")
    }
}
