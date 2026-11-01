import Foundation
import Testing
@testable import SWECore

/// The hub's pins, names and fork flags laid over the Sessions rows swe
/// listed: the mapping by hub key, the merge, and the pin ledger.
@Suite struct SessionHubMetaTests {
    func row(_ id: String, _ harness: String = "claude", title: String? = nil) -> SessionsModel.Row {
        SessionsModel.Row(session: SessionInfo(id: id, harness: harness, agent: harness, cwd: "/c/q",
                                               title: title ?? id, titleSource: nil,
                                               lastActiveAt: .now, status: nil, resume: nil),
                          usage: nil)
    }

    @Test func aHubRowReadsAsMeta() {
        let renamed = HubSessionMeta(HubSessionRow(key: "claude:a", title: "Ship it", harnessTitle: "fix the ledger",
                                                   pinned: true, pinnedAt: 100, canFork: true))
        #expect(renamed.isRenamed)
        #expect(renamed.pinned && renamed.pinnedAt == 100 && renamed.canFork)
        #expect(renamed.title(over: "local") == "Ship it")

        let plain = HubSessionMeta(HubSessionRow(key: "codex:b", title: "fix", harnessTitle: "fix",
                                                 pinned: false, pinnedAt: 100))
        #expect(!plain.isRenamed)
        #expect(plain.pinnedAt == nil, "an unpinned row has no pin time")
        #expect(!plain.canFork, "no can_fork means no Fork")
        #expect(plain.title(over: "swe's title") == "swe's title")

        // A hub older than names sends no harness_title: nothing reads as
        // renamed, so swe's title stays.
        let older = HubSessionMeta(HubSessionRow(key: "claude:c", title: "hub title"))
        #expect(!older.isRenamed)
        #expect(older.title(over: "swe's title") == "swe's title")
    }

    @Test func rowsMatchTheHubByHarnessAndId() {
        let overlay = HubSessionOverlay([
            HubSessionRow(key: "claude:a", title: "Renamed", harnessTitle: "a", pinned: true, pinnedAt: 5),
            HubSessionRow(key: "codex:x", title: "x", harnessTitle: "x"),
        ])
        #expect(HubSessionOverlay.key(row("a")) == "claude:a")
        #expect(overlay.meta(for: row("a"))?.pinned == true)
        #expect(overlay.title(for: row("a")) == "Renamed")
        // Same id, another harness: a different session.
        #expect(overlay.meta(for: row("a", "codex")) == nil)
        // The hub doesn't list it: no meta, swe's title.
        #expect(overlay.meta(for: row("gone")) == nil)
        #expect(overlay.title(for: row("gone", title: "Listed")) == "Listed")
        // A cloud row never matches, whatever its id.
        let cloud = row("bc-1", "cursor")
        #expect(HubSessionOverlay.key(cloud) == nil)
        #expect(HubSessionOverlay([HubSessionRow(key: "cursor:bc-1", pinned: true)]).meta(for: cloud) == nil)
    }

    @Test func noHubMeansNoMeta() {
        let overlay = HubSessionOverlay()
        #expect(overlay.isEmpty)
        #expect(overlay.meta(for: row("a")) == nil)
        #expect(overlay.title(for: row("a", title: "Mine")) == "Mine")
    }

    @Test func aReplyRenamesUnpinsAndKeepsFork() {
        var meta = HubSessionMeta(title: "a", harnessTitle: "a", pinned: true, pinnedAt: 9, canFork: true)
        meta.apply(HubMetaReply(title: "Better", harnessTitle: "a", pinned: true, pinnedAt: 9))
        #expect(meta.isRenamed && meta.title == "Better")
        // "Use the original name": the hub answers its harness title.
        meta.apply(HubMetaReply(title: "a", harnessTitle: "a", pinned: true, pinnedAt: 9))
        #expect(!meta.isRenamed)
        meta.apply(HubMetaReply(title: "a", harnessTitle: "a", pinned: false, pinnedAt: 9))
        #expect(!meta.pinned && meta.pinnedAt == nil)
        #expect(meta.canFork, "no pin or rename reply carries can_fork")
        // An empty title means the hub knows no name: the harness's stands.
        meta.apply(HubMetaReply(title: ""))
        #expect(meta.title == "a")
    }

    @Test func renameRulesMatchTheHub() {
        #expect(SessionRename.clamp("one\ntwo") == "one two")
        #expect(SessionRename.normalized("  a \t b  ") == "a b")
        let long = String(repeating: "x", count: 130)
        #expect(SessionRename.length(SessionRename.clamp(long)) == 120)
        // Code points, as the hub counts: a flag is two.
        #expect(SessionRename.length("🇸🇬") == 2)
        #expect(!SessionRename.canSave("   ", current: "a"))
        #expect(!SessionRename.canSave(" a ", current: "a"), "the same name once normalized")
        #expect(SessionRename.canSave("b", current: "a"))
        #expect(SessionRename.canSave("b", current: nil))
    }

    // MARK: Pin ledger

    func overlay(pinned: Bool, at: Double? = nil) -> HubSessionOverlay {
        HubSessionOverlay([HubSessionRow(key: "claude:a", title: "a", harnessTitle: "a",
                                         pinned: pinned, pinnedAt: at)])
    }

    @Test func aListReadBeforeTheAnswerKeepsTheChoice() {
        var ledger = SessionPinLedger()
        let mark = ledger.mark
        let version = ledger.choose("claude:a", pinned: true, now: 50, base: .init(pinned: false, pinnedAt: nil))
        // A read that left before the pin was answered says unpinned.
        var stale = overlay(pinned: false)
        ledger.overlay(&stale, fetchedFrom: mark)
        #expect(stale["claude:a"]?.pinned == true)
        #expect(stale["claude:a"]?.pinnedAt == 50)

        let took = ledger.answered("claude:a", version: version, reply: .init(pinned: true, pinnedAt: 51))
        #expect(took)
        // A read that left after the answer is the truth, and the choice goes.
        var fresh = overlay(pinned: true, at: 51)
        ledger.overlay(&fresh, fetchedFrom: ledger.mark)
        #expect(fresh["claude:a"]?.pinnedAt == 51)
        #expect(ledger.held("claude:a") == nil)
        var later = overlay(pinned: false)
        ledger.overlay(&later, fetchedFrom: ledger.mark)
        #expect(later["claude:a"]?.pinned == false, "an unpin from another device shows")
    }

    @Test func anOldReadArrivingAfterTheAnswerKeepsTheConfirmedPin() {
        var ledger = SessionPinLedger()
        let mark = ledger.mark
        let version = ledger.choose("claude:a", pinned: true, now: 50, base: .init(pinned: false, pinnedAt: nil))
        let took = ledger.answered("claude:a", version: version, reply: .init(pinned: true, pinnedAt: 51))
        #expect(took)
        // A read that left before the answer lands now: it keeps the
        // choice, but at the hub's confirmed time, not the estimate, so
        // closely timed pins sort the way the hub has them.
        var stale = overlay(pinned: false)
        ledger.overlay(&stale, fetchedFrom: mark)
        #expect(stale["claude:a"]?.pinned == true)
        #expect(stale["claude:a"]?.pinnedAt == 51)
    }

    @Test func anOpenChatsReadRefreshesAStaleEntry() {
        var overlay = HubSessionOverlay([HubSessionRow(key: "claude:a", title: "old", harnessTitle: "old")])
        overlay.adopt(HubSessionRow(key: "claude:a", title: "new", harnessTitle: "old",
                                    pinned: true, pinnedAt: 7))
        #expect(overlay["claude:a"]?.title == "new")
        #expect(overlay["claude:a"]?.pinnedAt == 7)
    }

    @Test func onlyTheLatestChoicesAnswerLands() {
        var ledger = SessionPinLedger()
        let pin = ledger.choose("claude:a", pinned: true, now: 1, base: .init(pinned: false, pinnedAt: nil))
        let unpin = ledger.choose("claude:a", pinned: false, now: 2, base: .init(pinned: true, pinnedAt: 1))
        let stale = ledger.answered("claude:a", version: pin, reply: .init(pinned: true, pinnedAt: 1))
        #expect(!stale)
        #expect(ledger.held("claude:a") == .init(pinned: false, pinnedAt: nil))
        // The pin reached the hub, so a refused unpin goes back to pinned.
        let back = ledger.refused("claude:a", version: unpin)
        #expect(back == .init(pinned: true, pinnedAt: 1))
        let again = ledger.refused("claude:a", version: unpin)
        #expect(again == nil, "settled once")
    }

    @Test func aRefusalGoesBackToWhatTheHubLastConfirmed() {
        var ledger = SessionPinLedger()
        _ = ledger.choose("claude:a", pinned: true, now: 1, base: .init(pinned: false, pinnedAt: nil))
        // A second click over the first, before either answered: the row
        // shows the first's optimistic pin, but the hub never confirmed it.
        let second = ledger.choose("claude:a", pinned: false, now: 2, base: .init(pinned: true, pinnedAt: 1))
        let back = ledger.refused("claude:a", version: second)
        #expect(back == .init(pinned: false, pinnedAt: nil))
    }
}

extension SessionsModelTests {
    func pins(_ rows: [HubSessionRow]) -> HubSessionOverlay { HubSessionOverlay(rows) }

    @Test func pinnedRowsLeadInPinOrderOutOfTheirDays() async {
        let model = await model([
            session("a", "claude", cwd: "/c/q", at: 1_791_699_000),
            session("b", "claude", cwd: "/c/q", at: 1_791_698_000),
            session("c", "claude", cwd: "/c/q", at: 1_791_697_000),
        ], [usage("a", "claude", models: [], cost: 1), usage("b", "claude", models: [], cost: 1),
            usage("c", "claude", models: [], cost: 1)])
        await model.load()
        #expect(model.pinnedRows(calendar: calendar).isEmpty, "no hub, no Pinned group")

        model.hubOverlay = pins([
            HubSessionRow(key: "claude:c", pinned: true, pinnedAt: 10),
            HubSessionRow(key: "claude:a", pinned: true, pinnedAt: 20),
            HubSessionRow(key: "claude:b", pinned: false),
        ])
        #expect(model.pinnedRows(calendar: calendar).map(\.id) == ["c", "a"])
        #expect(model.groupedByDay(calendar: calendar).flatMap { $0.rows.map(\.id) } == ["b"])
        #expect(model.summary.sessions == 3, "pinned rows still count")

        // Unpinned: back in its day.
        model.hubOverlay = pins([HubSessionRow(key: "claude:a", pinned: true, pinnedAt: 20)])
        #expect(model.pinnedRows(calendar: calendar).map(\.id) == ["a"])
        #expect(model.groupedByDay(calendar: calendar).flatMap { $0.rows.map(\.id) } == ["b", "c"])
    }

    @Test func aHubWithNoMatchChangesNothing() async {
        let model = await model([session("a", "claude", cwd: "/c/q", at: 1_791_699_000)],
                                [usage("a", "claude", models: [], cost: 1)])
        await model.load()
        model.hubOverlay = pins([HubSessionRow(key: "claude:elsewhere", pinned: true, pinnedAt: 1),
                                 HubSessionRow(key: "codex:a", pinned: true, pinnedAt: 1)])
        #expect(model.pinnedRows(calendar: calendar).isEmpty)
        #expect(model.groupedByDay(calendar: calendar).flatMap { $0.rows.map(\.id) } == ["a"])
        #expect(model.hubOverlay.meta(for: model.rows[0]) == nil)
    }

    @Test func aPinnedRowIgnoresTheWindowAndTheShortSessionRule() async {
        let model = await model([
            session("new", "claude", cwd: "/c/q", at: 1_791_699_000),
            // A year old, past the default 90-day window.
            session("old", "claude", cwd: "/c/q", at: 1_760_000_000),
            // No ledger events: trivial once the ledger has scanned.
            session("empty", "claude", cwd: "/c/q", at: 1_791_698_000),
        ], [usage("new", "claude", models: [], cost: 1), usage("old", "claude", models: [], cost: 1)])
        await model.load()
        await model.refreshUsage(ledgerScanned: true)
        #expect(model.visible.map(\.id) == ["new"])

        model.hubOverlay = pins([HubSessionRow(key: "claude:old", pinned: true, pinnedAt: 1),
                                 HubSessionRow(key: "claude:empty", pinned: true, pinnedAt: 2)])
        #expect(model.pinnedRows(calendar: calendar).map(\.id) == ["old", "empty"])
        #expect(model.hiddenTrivialCount == 0)
        // Other filters still apply to pinned rows.
        model.filter.harness = "codex"
        #expect(model.pinnedRows(calendar: calendar).isEmpty)
    }

    @Test func aNameSetOnTheHubIsSearchable() async {
        let model = await model([session("a", "claude", cwd: "/c/q", at: 1_791_699_000, title: "fix ledger"),
                                 session("b", "claude", cwd: "/c/q", at: 1_791_698_000, title: "docs")],
                                [usage("a", "claude", models: [], cost: 1), usage("b", "claude", models: [], cost: 1)])
        await model.load()
        model.filter.search = "release"
        #expect(model.visible.isEmpty)
        model.hubOverlay = pins([HubSessionRow(key: "claude:b", title: "Release notes", harnessTitle: "docs")])
        #expect(model.visible.map(\.id) == ["b"])
        #expect(model.hubOverlay.title(for: model.visible[0]) == "Release notes")
    }
}

extension HubStubbed {
@MainActor
@Suite struct LocalHubMetaTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }
    var store: HubDeviceStore { HubDeviceStore(url: scratch.url.appending(path: "remote/app-device.json")) }

    func paired() throws -> LocalHub {
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        return LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session())
        }
    }

    nonisolated static let rows = #"""
    {"sessions": [
      {"key": "claude:a", "title": "Ship it", "harness_title": "fix", "pinned": true, "pinned_at": 7, "can_fork": true},
      {"key": "codex:b", "title": "b", "harness_title": "b", "pinned": false, "pinned_at": null, "can_fork": false}
    ]}
    """#

    @Test func theListsReadFillsTheOverlayAndADownHubEmptiesIt() async throws {
        let hub = try paired()
        HubStub.handler = { req in req.url?.path == "/v1/sessions" ? (200, Data(Self.rows.utf8)) : (404, Data()) }
        await hub.refreshKeys()
        #expect(hub.meta(for: "claude:a") == HubSessionMeta(title: "Ship it", harnessTitle: "fix",
                                                            pinned: true, pinnedAt: 7, canFork: true))
        #expect(hub.meta(for: "codex:b")?.canFork == false)
        #expect(hub.meta(for: "claude:unlisted") == nil)

        // The hub stops answering: no meta, so no hub actions anywhere.
        HubStub.handler = { _ in (503, Data()) }
        await hub.refreshKeys()
        #expect(hub.overlay.isEmpty)
    }

    @Test func noHubNoOverlay() async {
        let hub = LocalHub(locator: FakeLocator(address: nil), store: store)
        await hub.refreshKeys()
        #expect(hub.overlay.isEmpty)
        #expect(await hub.setPinned("claude:a", true) == nil, "nothing to pin")
    }

    @Test func unpinShowsAtOnceAndTakesTheHubsAnswer() async throws {
        let hub = try paired()
        HubStub.handler = { req in req.url?.path == "/v1/sessions" ? (200, Data(Self.rows.utf8)) : (404, Data()) }
        await hub.refreshKeys()
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/sessions/claude%3Aa/pin" || req.url?.path == "/v1/sessions/claude:a/pin"
                ? (200, Data(#"{"ok": true, "title": "Ship it", "harness_title": "fix", "pinned": false, "pinned_at": null}"#.utf8))
                : (404, Data())
        }
        #expect(await hub.setPinned("claude:a", false) == nil)
        #expect(hub.meta(for: "claude:a")?.pinned == false)
        #expect(hub.meta(for: "claude:a")?.pinnedAt == nil)
        let sent = try #require(HubStub.requests.last)
        #expect(sent.httpMethod == "POST")
        let body = try JSONSerialization.jsonObject(with: sent.httpBody ?? Self.read(sent)) as? [String: Bool]
        #expect(body == ["pinned": false])
    }

    @Test func aRefusedPinGoesBackWithANote() async throws {
        let hub = try paired()
        HubStub.handler = { req in req.url?.path == "/v1/sessions" ? (200, Data(Self.rows.utf8)) : (404, Data()) }
        await hub.refreshKeys()
        HubStub.handler = { _ in
            (409, Data(#"{"error": {"code": "too_many_pins", "message": "too many pinned sessions"}}"#.utf8))
        }
        let note = await hub.setPinned("codex:b", true)
        #expect(note == "Couldn't pin it. Too many pinned sessions")
        #expect(hub.meta(for: "codex:b")?.pinned == false)
    }

    @Test func renameTakesTheHubsNameAndCanGoBack() async throws {
        let hub = try paired()
        HubStub.handler = { req in req.url?.path == "/v1/sessions" ? (200, Data(Self.rows.utf8)) : (404, Data()) }
        await hub.refreshKeys()
        HubStub.handler = { req in
            let title = (try? JSONSerialization.jsonObject(with: req.httpBody ?? Self.read(req)) as? [String: String])?["title"] ?? "?"
            let reply = #"{"ok": true, "title": "\#(title.isEmpty ? "b" : title)", "harness_title": "b", "pinned": false, "pinned_at": null}"#
            return req.url?.path.hasSuffix("/title") == true ? (200, Data(reply.utf8)) : (404, Data())
        }
        try await hub.rename("codex:b", title: "Docs pass")
        #expect(hub.meta(for: "codex:b")?.title == "Docs pass")
        #expect(hub.meta(for: "codex:b")?.isRenamed == true)
        try await hub.rename("codex:b", title: "")
        #expect(hub.meta(for: "codex:b")?.isRenamed == false)
    }

    @Test func aRenameFromAnotherDeviceReachesTheOverlay() async throws {
        let hub = try paired()
        HubStub.handler = { req in req.url?.path == "/v1/sessions" ? (200, Data(Self.rows.utf8)) : (404, Data()) }
        await hub.refreshKeys()
        let chat = SessionChatModel(key: "claude:a", hub: hub)
        let data = try HubWire.decoder.decode(HubJSON.self, from: Data(
            #"{"title": "From the phone", "harness_title": "fix", "pinned": true, "pinned_at": 7}"#.utf8))
        chat.apply(HubEnvelope(type: "session.meta", session: "claude:a", seq: 4, data: data))
        #expect(hub.meta(for: "claude:a")?.title == "From the phone")
        #expect(hub.meta(for: "claude:a")?.canFork == true)
    }

    @Test func forkPostsThePromptAndAnswersTheLaunch() async throws {
        let hub = try paired()
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path.hasSuffix("/fork") == true
                ? (202, Data(#"{"ok": true, "launch_id": "L1", "state": "pending", "note": "booting"}"#.utf8))
                : (404, Data())
        }
        let launch = try await hub.fork("claude:a", prompt: "try the other fix")
        #expect(launch.launchId == "L1")
        #expect(HubStub.body(try #require(HubStub.requests.last)) == ["prompt": "try the other fix"])
    }

    /// The body URLSession handed the protocol as a stream.
    nonisolated static func read(_ request: URLRequest) -> Data {
        guard let stream = request.httpBodyStream else { return Data() }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buffer, maxLength: buffer.count)
            guard n > 0 else { break }
            data.append(buffer, count: n)
        }
        return data
    }
}
}
