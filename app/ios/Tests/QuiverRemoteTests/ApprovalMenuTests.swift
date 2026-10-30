import XCTest
@testable import QuiverRemote

final class ApprovalMenuTests: XCTestCase {
    private func env(_ json: String) -> Envelope {
        try! Hub.decoder.decode(Envelope.self, from: Data(json.utf8))
    }

    @MainActor func testTheEventsMenuRowsReachTheCard() {
        let chat = ChatModel(key: "devin:1", app: AppModel(hub: nil))
        chat.apply(env(#"""
        {"type":"approval.requested","seq":3,"data":{"request_id":"r1","prompt":"Run rm -rf build?",
         "options":[{"n":"1","label":"Approve once"},{"n":"2","label":"Always allow in this project"},
                    {"n":"3","label":"Deny"}]}}
        """#))
        XCTAssertEqual(chat.approvalOptions["r1"]?.map(\.n), ["1", "2", "3"])
        XCTAssertEqual(chat.approvalOptions["r1"]?[1].label, "Always allow in this project")
        // The card itself is the same approval item as before.
        XCTAssertEqual(chat.items.first?.kind, .approval(requestId: "r1", prompt: "Run rm -rf build?", state: .pending))
    }

    @MainActor func testAClosingEventKeepsTheRowAnswered() {
        let chat = ChatModel(key: "devin:1", app: AppModel(hub: nil))
        chat.apply(env(#"""
        {"type":"approval.requested","seq":3,"data":{"request_id":"r1","prompt":"Run it?",
         "options":[{"n":"1","label":"Approve once"},{"n":"2","label":"Always allow"}]}}
        """#))
        chat.apply(env(#"{"type":"approval.resolved","seq":4,"data":{"request_id":"r1","decision":"2"}}"#))
        // The menu leaves the pane: the tailer says so without a decision.
        chat.apply(env(#"{"type":"approval.resolved","seq":5,"data":{"request_id":"r1","source":"pane"}}"#))
        XCTAssertEqual(chat.items.first?.kind, .approval(requestId: "r1", prompt: "Run it?", state: .resolved("2")))
        XCTAssertEqual(ApprovalMenu.title(.resolved("2"), options: chat.approvalOptions["r1"] ?? []),
                       "Answered: Always allow")
    }

    @MainActor func testAnEventWithoutRowsKeepsTheThreeButtons() {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.apply(env(#"{"type":"approval.requested","seq":3,"data":{"request_id":"r1","prompt":"Run npm test?"}}"#))
        chat.apply(env(#"{"type":"approval.requested","seq":4,"data":{"request_id":"r2","prompt":"Edit?","options":[]}}"#))
        XCTAssertNil(chat.approvalOptions["r1"])
        XCTAssertNil(chat.approvalOptions["r2"])
    }

    func testRowsKeepWhatTheHubAccepts() throws {
        let options = try Hub.decoder.decode([ApprovalOption].self, from: Data("""
        [{"n": "1", "label": "Yes"}, {"n": 2, "label": "Yes, and don't ask again"},
         {"n": "3", "label": "  "}, {"n": "123", "label": "Too long a number"},
         {"n": "x", "label": "Not a number"}, {"n": "4"}]
        """.utf8))
        XCTAssertEqual(ApprovalMenu.rows(options).map(\.n), ["1", "2"])
        XCTAssertEqual(ApprovalMenu.rows(nil), [])
        XCTAssertEqual(ApprovalMenu.buttonTitle(options[1]), "2. Yes, and don't ask again")
    }

    func testTheHeadingNamesTheRowAnswered() {
        let rows = [ApprovalOption(n: "1", label: "Approve once"), ApprovalOption(n: "2", label: "Always allow")]
        XCTAssertEqual(ApprovalMenu.title(.pending, options: rows), "Approval needed")
        XCTAssertEqual(ApprovalMenu.title(.resolved("2"), options: rows), "Answered: Always allow")
        XCTAssertEqual(ApprovalMenu.title(.resolved("7"), options: rows), "Answered")
        XCTAssertEqual(ApprovalMenu.title(.resolved("deny"), options: []), "Denied")
        XCTAssertEqual(ApprovalMenu.title(.resolved("always"), options: []), "Always allowed")
        XCTAssertEqual(ApprovalMenu.title(.resolved("allow"), options: rows), "Allowed")
        XCTAssertEqual(ApprovalMenu.title(.resolved(nil), options: rows), "Answered")
        XCTAssertEqual(ApprovalMenu.title(.queued, options: rows), "Queued, waiting for the machine")
    }
}
