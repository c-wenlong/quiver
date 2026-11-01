import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// The chat's Queued tray on its own: hold, merge, take, the row
/// actions and editing, against a fake `act`.
@Suite @MainActor struct QueuedTrayTests {
    /// A tray whose hub calls are recorded and answered by `answer`.
    final class Calls {
        var made: [(cid: String, op: String, text: String?)] = []
        var answer: (String) throws -> Void = { _ in }
    }

    func tray(_ calls: Calls) -> QueuedTray {
        let tray = QueuedTray()
        tray.act = { cid, op, text in
            calls.made.append((cid, op, text))
            try calls.answer(op)
        }
        return tray
    }

    static let notWaiting = HubError(status: 409, code: "not_waiting", message: "That message already went out")

    @Test func holdKeepsOrderAndIgnoresRepeatsAndSettledSends() {
        let tray = QueuedTray()
        tray.hold("a", text: "one")
        tray.hold("b", text: "two")
        tray.hold("a", text: "one again")
        #expect(tray.sends.map(\.text) == ["one", "two"])
        tray.take("a")
        // A late `waiting` answer for a send whose result came first.
        tray.hold("a", text: "one")
        #expect(tray.sends.map(\.clientId) == ["b"])
        #expect(QueuedTray.countLabel(1) == "1 message queued")
        #expect(QueuedTray.countLabel(3) == "3 messages queued")
    }

    @Test func theHubsListWins() {
        let tray = QueuedTray()
        tray.hold("a", text: "mine")
        tray.hold("gone", text: "left while closed")
        tray.take("done")
        tray.merge([HubWaitingSend(clientId: "b", text: "from the phone"),
                    HubWaitingSend(clientId: "a", text: "server text"),
                    HubWaitingSend(clientId: "shown", text: "echoed already")],
                   known: ["shown"])
        // The hub's order and text (another client may have edited it).
        #expect(tray.sends.map(\.clientId) == ["b", "a"])
        #expect(tray.sends.map(\.text) == ["from the phone", "server text"])
    }

    @Test func aListAskedForBeforeASendWasHeldKeepsIt() {
        let tray = QueuedTray()
        tray.hold("old", text: "gone from the hub")
        let asked = tray.holds
        // The send's `waiting` answer lands while the list is out.
        tray.hold("new", text: "just queued")
        tray.merge([], asked: asked)
        #expect(tray.sends.map(\.clientId) == ["new"])
        // A list asked for after it was held is trusted.
        tray.merge([])
        #expect(tray.sends.isEmpty)
    }

    @Test func anEchoMarksARowButOnlyItsResultTakesIt() {
        let tray = QueuedTray()
        tray.hold("a", text: "run the tests")
        tray.hold("b", text: "run the tests")
        #expect(!tray.noteEcho("something else", echoes: SessionChatModel.echoes))
        #expect(tray.noteEcho("@shot.png run the tests", echoes: SessionChatModel.echoes))
        // Text alone can't say which send went out: both rows stay.
        #expect(tray.sends.map(\.clientId) == ["a", "b"])
        // A second echo claims the next row, not the same one again.
        #expect(tray.noteEcho("run the tests", echoes: SessionChatModel.echoes))
        #expect(!tray.noteEcho("run the tests", echoes: SessionChatModel.echoes))
        #expect(tray.take("b")?.echoed == true)
        #expect(tray.take("a")?.echoed == true)
        #expect(tray.take("a") == nil)
    }

    @Test func sendNowSteersAndAFailureComesBackWithANote() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "x")
        calls.answer = { _ in throw HubError(status: 409, code: "not_attached", message: "") }
        await tray.sendNow("a")
        #expect(calls.made.map(\.op) == ["steer"])
        #expect(tray.sends.first?.phase == .waiting)
        #expect(tray.sends.first?.note == "Attach to send now")
        // Already gone on the hub: the row says so and only clears.
        calls.answer = { _ in throw Self.notWaiting }
        await tray.sendNow("a")
        #expect(tray.sends.first?.phase == .left)
        #expect(tray.sends.first?.note == "Already sent")
        calls.answer = { _ in }
        await tray.sendNow("a")
        #expect(calls.made.count == 2)
    }

    @Test func aSuccessfulSendNowWaitsForItsResult() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "x")
        await tray.sendNow("a")
        #expect(tray.sends.first?.phase == .sendingNow)
        // Remove and Edit can't act on it while it goes.
        await tray.remove("a")
        #expect(!tray.beginEdit("a"))
        #expect(calls.made.map(\.op) == ["steer"])
        #expect(tray.take("a")?.send.text == "x")
    }

    @Test func removeHidesAtOnceAndComesBackIfRefused() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "x")
        let gate = HoldGate()
        calls.answer = { _ in throw Self.notWaiting }
        tray.act = { cid, op, text in
            calls.made.append((cid, op, text))
            try await gate.pass()
            try calls.answer(op)
        }
        let removing = Task { await tray.remove("a") }
        #expect(await gate.arrived())
        #expect(tray.sends.isEmpty)
        #expect(tray.queued.first?.phase == .removing)
        gate.open()
        await removing.value
        #expect(tray.sends.first?.phase == .left)
        #expect(tray.sends.first?.note == "Already sent")
        // A left send clears without asking the hub.
        await tray.remove("a")
        #expect(tray.sends.isEmpty)
        #expect(calls.made.count == 1)
    }

    @Test func removeDropsWhatTheHubForgotAndRemoveAllTakesEach() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "x")
        calls.answer = { _ in throw HubError(status: 404, code: "unknown_send", message: "") }
        await tray.remove("a")
        #expect(tray.sends.isEmpty)
        calls.answer = { _ in }
        tray.hold("b", text: "y")
        tray.hold("c", text: "z")
        await tray.removeAll()
        #expect(tray.sends.isEmpty)
        #expect(Set(calls.made.dropFirst().map(\.cid)) == ["b", "c"])
        #expect(calls.made.allSatisfy { $0.op == "cancel" })
    }

    @Test func editSwapsTheBoxAndSaveWritesTheNewText() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "first draft")
        tray.draft = "half typed"
        #expect(tray.beginEdit("a"))
        #expect(tray.draft == "first draft")
        tray.draft = "  second draft "
        await tray.saveEdit()
        #expect(calls.made.last?.op == "edit")
        #expect(calls.made.last?.text == "second draft")
        #expect(tray.sends.first?.text == "second draft")
        // What was typed before comes back.
        #expect(tray.editingId == nil)
        #expect(tray.draft == "half typed")
    }

    @Test func cancelPutsTheDraftBackAndAFailedSaveKeepsEditing() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "q")
        tray.draft = "mine"
        tray.beginEdit("a")
        tray.cancelEdit()
        #expect(tray.draft == "mine")
        tray.beginEdit("a")
        tray.draft = "q2"
        calls.answer = { _ in throw HubError(status: 0, code: "network", message: "") }
        await tray.saveEdit()
        #expect(tray.editingId == "a")
        #expect(tray.editNote == "Can't reach the hub")
        #expect(tray.draft == "q2")
    }

    @Test func typingDuringASaveIsKept() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "q")
        tray.draft = "mine"
        tray.beginEdit("a")
        tray.draft = "q2"
        let gate = HoldGate()
        tray.act = { _, _, _ in try await gate.pass() }
        let saving = Task { await tray.saveEdit() }
        #expect(await gate.arrived())
        tray.draft = "q2 and more"
        gate.open()
        await saving.value
        #expect(tray.sends.first?.text == "q2")
        #expect(tray.editingId == nil)
        #expect(tray.draft == "q2 and more")

        // A late "already went out" keeps it too.
        tray.hold("b", text: "r")
        tray.beginEdit("b")
        tray.draft = "r2"
        let late = HoldGate()
        tray.act = { _, _, _ in
            try await late.pass()
            throw Self.notWaiting
        }
        let refused = Task { await tray.saveEdit() }
        #expect(await late.arrived())
        tray.draft = "r2 typed on"
        late.open()
        await refused.value
        #expect(tray.draft == "r2 typed on")
        #expect(tray.editNote == "That message already went out")
    }

    @Test func tooLateToEditKeepsTheNewTextToSend() async {
        let calls = Calls()
        let tray = tray(calls)
        tray.hold("a", text: "q")
        tray.draft = "mine"
        tray.beginEdit("a")
        tray.draft = "q2"
        calls.answer = { _ in throw Self.notWaiting }
        await tray.saveEdit()
        #expect(tray.editingId == nil)
        #expect(tray.draft == "q2")
        #expect(tray.editNote == "That message already went out")
        #expect(tray.sends.first?.phase == .left)
        tray.clearEditNote()
        #expect(tray.editNote == nil)
    }

    @Test func aSendThatGoesOutMidEditLeavesTheTextInTheBox() {
        let tray = QueuedTray()
        tray.hold("a", text: "q")
        tray.draft = "mine"
        tray.beginEdit("a")
        tray.draft = "q edited"
        tray.take("a")
        #expect(tray.editingId == nil)
        #expect(tray.draft == "q edited")
        #expect(tray.editNote == "That message already went out")
    }

    @Test func refusalsBecomeShortNotes() {
        #expect(QueuedTray.note(for: HubError(status: 409, code: "not_waiting", message: "That message was already removed"))
            == "Already removed")
        #expect(QueuedTray.note(for: HubError(status: 409, code: "not_waiting", message: "That message already failed"))
            == "Already failed")
        #expect(QueuedTray.note(for: HubError(status: 429, code: "backlog_full", message: "")) == "Too many waiting. Try again shortly")
        #expect(QueuedTray.note(for: HubError(status: 409, code: "queue_unsupported", message: "")) == "Not available on this machine")
        #expect(QueuedTray.note(for: CancellationError()) == "Something went wrong")
    }

    @Test func theQueueListDecodes() throws {
        let json = #"{"sends": [{"client_id": "c1", "text": "later", "attachments": [], "queued_at": 1793000000.5}]}"#
        let body = try HubWire.decoder.decode(HubQueueResponse.self, from: Data(json.utf8))
        #expect(body.sends == [HubWaitingSend(clientId: "c1", text: "later", queuedAt: 1793000000.5)])
    }
}

extension HubStubbed {
    /// The tray inside the chat: the hub's `waiting` answer, its list on
    /// open, results and echoes, and the queue route's requests.
    @MainActor
    @Suite struct SessionChatTrayTests {
        let scratch: TempDir
        init() throws { scratch = try TempDir() }

        func opened(queue: String = #"{"sends": []}"#) async throws -> SessionChatModel {
            let store = HubDeviceStore(url: scratch.url.appending(path: "app-device.json"))
            try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
            HubStub.requests = []
            HubStub.handler = { req in
                switch (req.httpMethod, req.url?.path) {
                case (_, "/v1/sessions"):
                    return (200, Data(#"{"sessions": [{"key": "claude:s1", "status": "active", "ownership": "attached"}]}"#.utf8))
                case (_, "/v1/sessions/claude:s1/messages"):
                    return (200, Data(#"{"messages": [], "has_more": false}"#.utf8))
                case (_, "/v1/sessions/claude:s1/message"):
                    return (202, Data(#"{"ok": true, "waiting": true}"#.utf8))
                case ("GET", "/v1/sessions/claude:s1/queue"):
                    return (200, Data(queue.utf8))
                case ("POST", "/v1/sessions/claude:s1/queue"):
                    return (200, Data(#"{"ok": true}"#.utf8))
                default:
                    return (404, Data())
                }
            }
            let hub = LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
                HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HubStub.session())
            }
            let chat = SessionChatModel(key: "claude:s1", hub: hub)
            await chat.start()
            chat.stop()
            await chat.settleReads()
            return chat
        }

        func event(_ type: String, seq: Int? = nil, _ data: String) -> HubEnvelope {
            HubEnvelope(type: type, session: "claude:s1", seq: seq,
                        data: try! HubWire.decoder.decode(HubJSON.self, from: Data(data.utf8)))
        }

        @Test func openingShowsWhatTheHubHolds() async throws {
            let chat = try await opened(queue: #"{"sends": [{"client_id": "p1", "text": "from the phone"}]}"#)
            #expect(chat.tray.sends.map(\.text) == ["from the phone"])
            #expect(chat.items.isEmpty)
        }

        @Test func aHeldSendWaitsInTheTrayThenItsResultMovesIt() async throws {
            let chat = try await opened()
            await chat.send("after this turn", mode: "queue")
            #expect(chat.items.isEmpty)
            let cid = try #require(chat.tray.sends.first?.clientId)
            chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": true}"#))
            #expect(chat.tray.sends.isEmpty)
            #expect(chat.items.map(\.kind) == [.user("after this turn", send: .delivered)])
            // Its echo still replaces the bubble.
            chat.apply(event("message.appended", seq: 4, #"{"kind": "user", "text": "after this turn"}"#))
            #expect(chat.items.map(\.kind) == [.user("after this turn", send: nil)])
        }

        @Test func anEchoBeforeTheResultLeavesOneEntry() async throws {
            let chat = try await opened()
            await chat.send("ping", mode: "queue")
            let cid = try #require(chat.tray.sends.first?.clientId)
            chat.apply(event("message.appended", seq: 4, #"{"kind": "user", "text": "ping"}"#))
            // Only its own result can say it went out.
            #expect(chat.tray.sends.map(\.clientId) == [cid])
            chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": true}"#))
            #expect(chat.tray.sends.isEmpty)
            #expect(chat.items.map(\.kind) == [.user("ping", send: nil)])
        }

        @Test func aRemovedSendLeavesNoTrace() async throws {
            let chat = try await opened()
            await chat.send("never mind", mode: "queue")
            let cid = try #require(chat.tray.sends.first?.clientId)
            // Removed from another device: its result says cancelled.
            chat.apply(event("command.result", #"{"op": "message", "client_id": "\#(cid)", "ok": false, "cancelled": true}"#))
            #expect(chat.tray.sends.isEmpty)
            #expect(chat.items.isEmpty)
        }

        @Test func rowActionsPostToTheQueueRoute() async throws {
            let chat = try await opened()
            await chat.send("q", mode: "queue")
            let cid = try #require(chat.tray.sends.first?.clientId)
            chat.tray.beginEdit(cid)
            chat.tray.draft = "q2"
            await chat.tray.saveEdit()
            await chat.tray.sendNow(cid)
            let posts = HubStub.requests.filter { $0.httpMethod == "POST" && $0.url?.path.hasSuffix("/queue") == true }
            #expect(posts.map { HubStub.body($0) } == [
                ["client_id": cid, "op": "edit", "text": "q2"],
                ["client_id": cid, "op": "steer"],
            ])
            #expect(chat.tray.sends.first?.text == "q2")
            #expect(chat.tray.sends.first?.phase == .sendingNow)
        }
    }
}
