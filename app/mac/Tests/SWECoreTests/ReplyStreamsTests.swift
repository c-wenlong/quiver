import Foundation
import Testing
@testable import SWECore

/// `message.delta` (#1444): the streaming bubbles' rules, shared word for
/// word with the iPhone's copy and ported to the web chat.
@Suite struct ReplyStreamsTests {
    func delta(_ id: String, _ text: String, _ seq: Int, done: Bool = false, kind: String = "assistant") -> ReplyDelta {
        ReplyDelta(id: id, kind: kind, text: text, seq: seq, done: done)
    }

    @Test func deltasAppendPerIdInOrder() {
        var r = ReplyStreams()
        let v1 = r.apply(delta("a", "Hel", 1))
        #expect(v1)
        let v2 = r.apply(delta("b", "Other", 1))
        #expect(v2)
        let v3 = r.apply(delta("a", "lo", 2, done: true))
        #expect(v3)
        #expect(r.shown.map(\.text) == ["Hello", "Other"])
        #expect(r.shown[0].done)
    }

    @Test func aGapStopsDrawingButKeepsTheBubblesPlaceInLine() {
        var r = ReplyStreams()
        r.apply(delta("a", "one", 1))
        r.apply(delta("b", "two", 1))
        let v4 = r.apply(delta("a", " lost", 3))
        #expect(v4, "the bubble goes")
        #expect(r.shown.map(\.id) == ["b"])
        let v5 = r.apply(delta("a", "more", 4))
        #expect(!v5, "nothing more is drawn for it")
        // Its final still takes it, not b's bubble.
        let v6 = r.claim(kind: "assistant")
        #expect(v6 == "a")
        let v7 = r.claim(kind: "assistant")
        #expect(v7 == "b")
    }

    @Test func aStreamFirstHeardMidwayIsNotDrawnButMatched() {
        var r = ReplyStreams()
        let v8 = r.apply(delta("a", "tail", 5))
        #expect(!v8)
        r.apply(delta("b", "fresh", 1))
        #expect(r.shown.map(\.id) == ["b"])
        let v9 = r.claim(kind: nil)
        #expect(v9 == "a")
    }

    @Test func theFinalTakesTheOldestOpenBubbleOfItsKindAndLaterDeltasAreDropped() {
        var r = ReplyStreams()
        r.apply(delta("t", "thought", 1, kind: "thinking"))
        r.apply(delta("a", "first", 1))
        r.apply(delta("b", "second", 1))
        #expect(r.shown.map(\.id) == ["a", "b"], "only assistant text is drawn")
        let v10 = r.claim(kind: "tool_use")
        #expect(v10 == nil)
        let v11 = r.claim(kind: "assistant")
        #expect(v11 == "a")
        // Claimed but still drawn until its final is on screen.
        #expect(r.shown.map(\.id) == ["a", "b"])
        let v12 = r.apply(delta("a", " late", 2))
        #expect(!v12)
        #expect(r.shown[0].text == "first")
        let v13 = r.release("a")
        #expect(v13)
        #expect(r.shown.map(\.id) == ["b"])
        let v14 = r.apply(delta("a", "again", 1))
        #expect(!v14, "a released id never comes back")
        let v15 = r.claim(kind: "assistant")
        #expect(v15 == "b")
        let v16 = r.claim(kind: "assistant")
        #expect(v16 == nil)
    }

    @Test func idleSweepsOnlyTheUnclaimedBubblesOpenWhenItWent() {
        var r = ReplyStreams()
        r.apply(delta("a", "claimed", 1, done: true))
        r.apply(delta("b", "orphan", 1, done: true))
        let open = r.openIds
        _ = r.claim(kind: "assistant")
        r.apply(delta("c", "next turn", 1))
        let v17 = r.sweep(open)
        #expect(v17)
        #expect(r.shown.map(\.id) == ["a", "c"])
        let v18 = r.apply(delta("b", "x", 2))
        #expect(!v18)
    }

    @Test func clearDropsEverythingAndItsLateDeltas() {
        var r = ReplyStreams()
        r.apply(delta("a", "x", 1))
        let v19 = r.clear()
        #expect(v19)
        #expect(r.shown.isEmpty)
        let v20 = r.apply(delta("a", "y", 2))
        #expect(!v20)
        let v21 = r.clear()
        #expect(!v21)
    }

    @Test func theWirePayloadDecodes() throws {
        let json = #"{"id":"m:0","kind":"assistant","text":"Hi","seq":1,"done":false}"#
        let d = try HubWire.decoder.decode(ReplyDelta.self, from: Data(json.utf8))
        #expect(d == ReplyDelta(id: "m:0", text: "Hi", seq: 1))
    }
}

@MainActor
@Suite struct SessionChatStreamingTests {
    func model() -> SessionChatModel {
        let chat = SessionChatModel(key: "claude:s1", hub: LocalHub(locator: FakeLocator(address: nil),
                                                                   store: HubDeviceStore(url: URL(filePath: "/dev/null"))))
        chat.streamSleep = { _ in }
        return chat
    }

    func env(_ type: String, _ data: String, seq: Int? = nil) -> HubEnvelope {
        HubEnvelope(type: type, session: "claude:s1", seq: seq,
                    data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
    }

    func delta(_ id: String, _ text: String, _ seq: Int, done: Bool = false) -> HubEnvelope {
        env("message.delta", #"{"id":"\#(id)","kind":"assistant","text":"\#(text)","seq":\#(seq),"done":\#(done)}"#)
    }

    @Test func deltasRepaintThrottledAndTheFinalReplacesTheBubble() async {
        let chat = model()
        chat.apply(delta("x", "Hel", 1))
        chat.apply(delta("x", "lo", 2))
        #expect(chat.streaming.isEmpty, "the repaint waits for the throttle")
        await chat.repaintTask?.value
        #expect(chat.streaming == [HubChatItem(id: "live-x", kind: .assistant("Hello"))])
        chat.apply(delta("x", "!", 3, done: true))
        chat.apply(env("message.appended", #"{"kind":"assistant","text":"Hello!"}"#, seq: 7))
        #expect(chat.streaming.isEmpty, "replaced in the same update as the final lands")
        #expect(chat.items.map(\.id) == ["m7"])
        chat.apply(delta("x", "late", 4))
        await chat.repaintTask?.value
        #expect(chat.streaming.isEmpty)
    }

    @Test func idleSweepsAnOrphanAndAHistoryReloadClears() async {
        let chat = model()
        chat.apply(delta("x", "never finished", 1))
        chat.repaintStreams()
        #expect(chat.streaming.count == 1)
        chat.apply(env("session.status", #"{"status":"idle"}"#, seq: 3))
        for _ in 0..<20 where !chat.streaming.isEmpty { await Task.yield() }
        #expect(chat.streaming.isEmpty)

        chat.apply(delta("y", "mid", 1))
        chat.repaintStreams()
        #expect(chat.streaming.count == 1)
        chat.load(HubMessagesPage(messages: []))
        #expect(chat.streaming.isEmpty, "a history reload starts over")
    }

    @Test func historyNeverClaimsABubble() {
        let chat = model()
        chat.apply(delta("x", "live", 1))
        chat.repaintStreams()
        chat.load(HubMessagesPage(messages: [env("message.appended", #"{"kind":"assistant","text":"old"}"#, seq: 1)]))
        #expect(chat.items.map(\.id) == ["m1"])
        #expect(chat.streaming.isEmpty)
    }
}
