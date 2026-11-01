import Foundation
import Testing
@testable import QuiverCore

/// Per-session unread dots (#1439): the hub's flag on its rows, laid over
/// the Sessions list, hidden by the chat on screen and the reads posted
/// here until the hub catches up.
@Suite struct UnreadTrackerTests {
    @Test func theHubsFlagDecodesAndAnOlderHubReadsAsRead() throws {
        let reply = try HubWire.decoder.decode(HubSessionsResponse.self, from: Data(#"""
        {"sessions": [
          {"key": "claude:a", "last_active_at": 100, "unread": true},
          {"key": "claude:b", "unread": false},
          {"key": "claude:c"}
        ]}
        """#.utf8))
        #expect(reply.sessions.map(\.unread) == [true, false, nil])
        let overlay = HubSessionOverlay(reply.sessions)
        #expect(overlay["claude:a"]?.unread == true)
        #expect(overlay["claude:a"]?.lastActiveAt == 100)
        #expect(overlay["claude:b"]?.unread == false)
        #expect(overlay["claude:c"]?.unread == false, "no field: no dot")
    }

    @Test func theOverlayCarriesTheFlagToLocalAndOtherMachineRows() {
        let overlay = HubSessionOverlay([
            HubSessionRow(key: "claude:a", lastActiveAt: 10, unread: true),
            HubSessionRow(key: "codex:b@studio", machineId: "studio", lastActiveAt: 20, remote: true, unread: true),
        ])
        let local = SessionsModel.Row(session: SessionInfo(id: "a", harness: "claude", agent: "claude", cwd: nil,
                                                           title: "a", titleSource: nil, lastActiveAt: .now,
                                                           status: nil, resume: nil), usage: nil)
        #expect(overlay.meta(for: local)?.unread == true)
        #expect(overlay["codex:b@studio"]?.unread == true)
        #expect(overlay["codex:b@studio"]?.lastActiveAt == 20)
        // A pin or rename never touches the read state.
        var meta = overlay["claude:a"]!
        meta.apply(HubMetaReply(title: "x", harnessTitle: "a", pinned: true, pinnedAt: 1))
        #expect(meta.unread && meta.lastActiveAt == 10)
    }

    @Test func onlyAnUnreadRowOffScreenAndPastItsMarkShowsADot() {
        var tracker = UnreadTracker()
        #expect(tracker.isUnread("k", serverUnread: true, lastActiveAt: 50))
        #expect(!tracker.isUnread("k", serverUnread: false, lastActiveAt: 50))

        // Open: hidden whatever the hub says, and the read marks it.
        tracker.open("k", activity: 50, now: 40)
        #expect(tracker.openKey == "k")
        #expect(!tracker.isUnread("k", serverUnread: true, lastActiveAt: 900))
        #expect(tracker.marks["k"] == 50, "the later of the activity and now")

        // Closed after seeing up to 60 at 70: still read for what it covered.
        tracker.close("k", activity: 60, now: 70)
        #expect(tracker.openKey == nil)
        #expect(tracker.marks["k"] == 70)
        #expect(!tracker.isUnread("k", serverUnread: true, lastActiveAt: 70))
        // New activity past the mark shows the dot again.
        #expect(tracker.isUnread("k", serverUnread: true, lastActiveAt: 71))
        // An unknown activity time counts as covered.
        #expect(!tracker.isUnread("k", serverUnread: true, lastActiveAt: nil))
    }

    @Test func aMarkNeverMovesBackAndAStaleCloseLeavesTheOpenChat() {
        var tracker = UnreadTracker()
        tracker.open("a", activity: nil, now: 100)
        tracker.open("b", activity: nil, now: 110)
        // The rail switched chats: a's close comes after b opened.
        tracker.close("a", activity: 5, now: 90)
        #expect(tracker.openKey == "b")
        #expect(tracker.marks["a"] == 100)
    }

    @Test func aListReadDropsMarksTheHubHasCaughtUpWithOrTheRowMovedPast() {
        var tracker = UnreadTracker()
        for key in ["gone", "read", "moved", "pending", "unknown"] {
            tracker.close(key, activity: nil, now: 100)
        }
        tracker.reconcile([
            HubSessionRow(key: "read", lastActiveAt: 90, unread: false),
            HubSessionRow(key: "moved", lastActiveAt: 120, unread: true),
            HubSessionRow(key: "pending", lastActiveAt: 90, unread: true),
            HubSessionRow(key: "unknown", unread: true),
        ])
        // The hub's answer for "pending" left before the read landed.
        #expect(Set(tracker.marks.keys) == ["pending", "unknown"])
    }
}

extension HubStubbed {
@MainActor
@Suite struct UnreadDotsHubTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }
    var store: HubDeviceStore { HubDeviceStore(url: scratch.url.appending(path: "remote/app-device.json")) }

    func paired() throws -> LocalHub {
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        return LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session(), streamSession: HubStub.session())
        }
    }

    nonisolated static func reads() -> [URLRequest] {
        HubStub.requests.filter { $0.httpMethod == "POST" && $0.url?.path.hasSuffix("/read") == true }
    }

    nonisolated static func list(unread: Bool, at: Double) -> String {
        #"""
        {"sessions": [
          {"key": "claude:s1", "status": "done", "ownership": "attached", "last_active_at": \#(at), "unread": \#(unread)},
          {"key": "codex:x@studio", "machine_id": "studio", "remote": true, "last_active_at": \#(at), "unread": \#(unread)}
        ]}
        """#
    }

    @Test func aReadPostsAnEmptyBodyToTheEncodedKey() async throws {
        let hub = try paired()
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path.hasSuffix("/read") == true ? (200, Data(#"{"ok": true, "at": 1700000000.5}"#.utf8)) : (404, Data())
        }
        let reply = try await hub.client().markRead("codex:x@studio")
        #expect(reply == HubReadReply(ok: true, at: 1_700_000_000.5))
        let post = try #require(Self.reads().last)
        #expect(post.url?.absoluteString == "http://127.0.0.1:8477/v1/sessions/codex%3Ax%40studio/read")
        #expect(post.value(forHTTPHeaderField: "Authorization") == "Bearer secret")
        let body = try JSONSerialization.jsonObject(with: HubStub.bodyData(post)) as? [String: Any]
        #expect(body?.isEmpty == true, "no `at`: the hub stamps now")

        // An older hub, a gone session or a down hub: nothing to report.
        HubStub.handler = { _ in (404, Data(#"{"error": {"code": "unknown_session"}}"#.utf8)) }
        await hub.postRead("claude:gone")
        #expect(Self.reads().count == 2)
    }

    @Test func localAndOtherMachineRowsShowTheDotUntilOpened() async throws {
        let hub = try paired()
        HubStub.handler = { req in
            req.url?.path == "/v1/sessions" ? (200, Data(Self.list(unread: true, at: 100).utf8)) : (404, Data())
        }
        await hub.refreshKeys()
        #expect(hub.isUnread("claude:s1"))
        #expect(hub.isUnread("codex:x@studio"))
        #expect(!hub.isUnread("claude:unlisted"), "a row the hub doesn't list has no dot")

        hub.chatOpened("codex:x@studio", now: 150)
        #expect(!hub.isUnread("codex:x@studio"))
        hub.chatClosed("codex:x@studio", activity: 120, now: 160)
        // The hub hasn't caught up: the read posted here still hides it.
        await hub.refreshKeys()
        #expect(!hub.isUnread("codex:x@studio"))
        #expect(hub.isUnread("claude:s1"))

        // Once the hub says read, the mark goes; activity after that
        // brings the dot back.
        HubStub.handler = { req in
            req.url?.path == "/v1/sessions" ? (200, Data(Self.list(unread: false, at: 100).utf8)) : (404, Data())
        }
        await hub.refreshKeys()
        #expect(hub.unread.marks.isEmpty)
        HubStub.handler = { req in
            req.url?.path == "/v1/sessions" ? (200, Data(Self.list(unread: true, at: 200).utf8)) : (404, Data())
        }
        await hub.refreshKeys()
        #expect(hub.isUnread("codex:x@studio"))

        // A down hub: no rows, no dots.
        HubStub.handler = { _ in (503, Data()) }
        await hub.refreshKeys()
        #expect(!hub.isUnread("claude:s1"))
    }

    /// A chat on `claude:s1`, open (not stopped), with its read waits
    /// held by `gate`.
    func openChat(_ gate: HoldGate) async throws -> (LocalHub, SessionChatModel) {
        let hub = try paired()
        HubStub.requests = []
        HubStub.handler = { req in
            switch req.url?.path {
            case "/v1/sessions": return (200, Data(Self.list(unread: true, at: 100).utf8))
            case "/v1/sessions/claude:s1/messages":
                return (200, Data(#"{"messages": [{"type": "message.appended", "seq": 1, "data": {"kind": "assistant", "text": "old", "ts": 90}}], "has_more": false}"#.utf8))
            case "/v1/sessions/claude:s1/read": return (200, Data(#"{"ok": true, "at": 100}"#.utf8))
            default: return (404, Data())
            }
        }
        await hub.refreshKeys()
        let chat = SessionChatModel(key: "claude:s1", hub: hub)
        chat.readSleep = { _ in try? await gate.pass() }
        await chat.start()
        await chat.readOnOpen?.value
        return (hub, chat)
    }

    func message(_ seq: Int, ts: Double) -> HubEnvelope {
        HubEnvelope(type: "message.appended", session: "claude:s1", seq: seq,
                    data: try! HubWire.decoder.decode(HubJSON.self, from: Data(
                        #"{"kind": "assistant", "text": "m\#(seq)", "ts": \#(ts)}"#.utf8)))
    }

    @Test func openingPostsAtOnceAndABurstPostsOnce() async throws {
        let gate = HoldGate()
        let (hub, chat) = try await openChat(gate)
        #expect(Self.reads().count == 1, "the open's read")
        #expect(!hub.isUnread("claude:s1"))
        #expect(chat.readTask == nil, "history is not live activity")

        chat.apply(message(2, ts: 101))
        chat.apply(message(3, ts: 102))
        chat.apply(message(4, ts: 103))
        let pending = try #require(chat.readTask)
        #expect(await gate.arrived())
        #expect(Self.reads().count == 1, "nothing until the wait ends")
        gate.open()
        await pending.value
        #expect(Self.reads().count == 2, "three messages, one read")
        #expect(chat.readTask == nil)
        chat.stop()
        await chat.settleReads()
    }

    @Test func closingFlushesADueReadAndMarksTheNewestActivity() async throws {
        let gate = HoldGate()
        let (hub, chat) = try await openChat(gate)
        chat.apply(message(2, ts: 5_000_000_000))
        #expect(chat.readTask != nil)
        #expect(await gate.arrived())
        chat.stop()
        #expect(chat.readTask == nil)
        await chat.readFlush?.value
        #expect(Self.reads().count == 2, "the open's read and the flushed one")
        #expect(hub.unread.openKey == nil)
        // The newest message seen sets the mark, even ahead of the clock.
        #expect(hub.unread.marks["claude:s1"] == 5_000_000_000)

        // Closed: a late message schedules nothing.
        chat.apply(message(3, ts: 5_000_000_001))
        #expect(chat.readTask == nil)
        gate.open()
    }

    @Test func closingPostsOnceEvenWithNothingDue() async throws {
        let gate = HoldGate()
        let (_, chat) = try await openChat(gate)
        chat.stop()
        #expect(chat.readOnOpen == nil, "the opening read can't outlive the chat")
        await chat.readFlush?.value
        #expect(Self.reads().count == 2, "the close covers history shown after the opening read")
        // A second stop sends nothing more.
        chat.stop()
        #expect(Self.reads().count == 2)
        gate.open()
    }
}
}
