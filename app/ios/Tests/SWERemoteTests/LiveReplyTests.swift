import XCTest
@testable import SWERemote

/// Live reply text (`message.delta`, #1444): a streaming bubble per
/// reply, replaced by the final message, which the reveal queue still
/// paces. `ReplyStreams` is the Mac's file word for word; its own rules
/// are tested there and in the web chat's port.
@MainActor
final class LiveReplyTests: XCTestCase {
    private func env(_ json: String) -> Envelope {
        try! Hub.decoder.decode(Envelope.self, from: Data(json.utf8))
    }

    private func delta(_ id: String, _ text: String, _ seq: Int, done: Bool = false) -> Envelope {
        env(#"{"type":"message.delta","session":"claude:1","data":{"id":"\#(id)","kind":"assistant","text":"\#(text)","seq":\#(seq),"done":\#(done)}}"#)
    }

    private func chat() -> ChatModel {
        let chat = ChatModel(key: "claude:1", app: AppModel(hub: nil))
        chat.streamSleep = { _ in }
        return chat
    }

    /// Lets queued tasks (the repaint, the reveal, the idle sweep) run.
    private func settle(_ chat: ChatModel, until done: () -> Bool) async {
        for _ in 0..<50 where !done() { await Task.yield() }
    }

    func testDeltasRepaintThrottledOutsideTheRevealQueue() async {
        let chat = chat()
        chat.receive(delta("x", "Hel", 1))
        chat.receive(delta("x", "lo", 2))
        XCTAssertTrue(chat.streaming.isEmpty, "the repaint waits for the throttle")
        XCTAssertFalse(chat.working, "deltas never sit in the reveal queue")
        await chat.repaintTask?.value
        XCTAssertEqual(chat.streaming, [ChatItem(id: "live-x", kind: .assistant("Hello"))])
    }

    func testTheFinalKeepsTheBubbleUntilItsRevealThenTakesItsPlace() async {
        let chat = chat()
        var gate: CheckedContinuation<Void, Never>?
        chat.pause = { _ in await withCheckedContinuation { gate = $0 } }
        chat.receive(env(#"{"type":"message.appended","seq":1,"data":{"kind":"tool_use","tool":{"name":"Bash","call_id":"c"}}}"#))
        chat.receive(delta("x", "Done.", 1, done: true))
        chat.repaintStreams()
        // The tool card is shown and the reveal pauses after it, holding
        // the final: the bubble stays meanwhile.
        chat.receive(env(#"{"type":"message.appended","seq":2,"data":{"kind":"assistant","text":"Done."}}"#))
        await settle(chat) { gate != nil }
        XCTAssertEqual(chat.items.map(\.id), ["m1"])
        XCTAssertEqual(chat.streaming.map(\.id), ["live-x"])
        // A delta for the claimed id is late: ignored.
        chat.receive(delta("x", " more", 2))
        await chat.repaintTask?.value
        XCTAssertEqual(chat.streaming.first.map(\.kind), .assistant("Done."))
        gate?.resume()
        await settle(chat) { chat.streaming.isEmpty }
        XCTAssertEqual(chat.items.map(\.id), ["m1", "m2"])
        XCTAssertTrue(chat.streaming.isEmpty)
    }

    func testAGapDropsTheBubbleButItsFinalStillMatchesIt() async {
        let chat = chat()
        chat.receive(delta("a", "one", 1))
        chat.receive(delta("b", "two", 1))
        chat.receive(delta("a", "three", 3))
        chat.repaintStreams()
        XCTAssertEqual(chat.streaming.map(\.id), ["live-b"])
        chat.receive(env(#"{"type":"message.appended","seq":4,"data":{"kind":"assistant","text":"a's final"}}"#))
        await settle(chat) { !chat.items.isEmpty }
        XCTAssertEqual(chat.streaming.map(\.id), ["live-b"], "b's bubble waits for its own final")
    }

    func testIdleSweepsABubbleWhoseFinalNeverCame() async {
        let chat = chat()
        chat.receive(delta("x", "cut off", 1))
        chat.repaintStreams()
        chat.receive(env(#"{"type":"session.status","seq":5,"data":{"status":"idle"}}"#))
        await settle(chat) { chat.streaming.isEmpty }
        XCTAssertTrue(chat.streaming.isEmpty)
    }

    func testTheSweepTakesOnlyBubblesOpenWhenTheStatusArrived() async {
        let chat = chat()
        var gate: CheckedContinuation<Void, Never>?
        chat.pause = { _ in await withCheckedContinuation { gate = $0 } }
        chat.receive(delta("old", "cut off", 1))
        // The reveal holds the idle status behind a tool card while the
        // next turn's reply starts streaming.
        chat.receive(env(#"{"type":"message.appended","seq":1,"data":{"kind":"tool_use","tool":{"name":"Bash","call_id":"c"}}}"#))
        chat.receive(env(#"{"type":"session.status","seq":2,"data":{"status":"idle"}}"#))
        chat.receive(delta("new", "next turn", 1))
        await settle(chat) { gate != nil && chat.streaming.map(\.id) == ["live-new"] }
        chat.repaintStreams()
        XCTAssertEqual(chat.streaming.map(\.id), ["live-new"])
        gate?.resume()
    }

    func testStopClearsTheBubbles() {
        let chat = chat()
        chat.receive(delta("x", "half", 1))
        chat.repaintStreams()
        chat.stop()
        XCTAssertTrue(chat.streaming.isEmpty)
    }
}
