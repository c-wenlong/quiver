import XCTest
@testable import QuiverRemote

private func session(_ key: String, status: String?, at: Double) -> SessionRow {
    SessionRow(key: key, harness: "claude", agent: "Claude Code", cwd: "/w", title: "t-\(key)",
               lastActiveAt: at, status: status, machineId: "m1", ownership: nil, attachable: true)
}

private func inbox(_ key: String, status: String, at: Double, github: Bool = false) -> InboxItem {
    InboxItem(key: key, machineId: "m1", harness: "codex", agent: nil, title: "i-\(key)", cwd: "/w",
              status: status, lastActiveAt: at, requestId: nil, kind: github ? "github" : nil)
}

private func hub(_ host: String = "mac.tail.ts.net") -> PairedHub {
    PairedHub(baseURL: URL(string: "http://\(host):8477")!,
              credential: Credential(deviceId: "d", credential: "c", name: nil))
}

private func tempStore() -> WidgetSnapshotStore {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("widget-\(UUID().uuidString)")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    return WidgetSnapshotStore(directory: dir)
}

private let now = Date(timeIntervalSince1970: 1_800_000_000)
private let today = WidgetSummary.day(now)

private func snapshot(hubId: String = hub().id, spend: Double? = 1, day: String? = today, at: Date = now) -> WidgetSnapshot {
    WidgetSummary.make(hubId: hubId, hubName: "mac", sessions: [session("a", status: "active", at: 1)],
                       inbox: [], spendUsd: spend, spendDay: day, at: at)
}

final class WidgetSummaryTests: XCTestCase {
    func testWaitingComesFromTheInboxAndFlaggedSessionsWorkingFromActiveOnes() {
        let s = WidgetSummary.make(
            hubId: "h", hubName: "mac",
            sessions: [session("busy", status: "active", at: 5), session("old", status: "active", at: 1),
                       session("ask", status: "approval", at: 3), session("waiting", status: "active", at: 9),
                       session("done", status: "done", at: 8)],
            inbox: [inbox("waiting", status: "followup", at: 9), inbox("err", status: "error", at: 7),
                    inbox("fin", status: "done", at: 6), inbox("gh:1", status: "github", at: 10, github: true)],
            spendUsd: 2.5, spendDay: "2026-11-01", at: now)
        XCTAssertEqual(s.needsYou.map(\.key), ["waiting", "ask"])
        XCTAssertEqual(s.needsYouCount, 2)
        // A session waiting on you isn't also counted as working.
        XCTAssertEqual(s.working.map(\.key), ["busy", "old"])
        XCTAssertEqual(s.workingCount, 2)
        XCTAssertEqual(s.needsYou.first?.title, "i-waiting")
        XCTAssertTrue(s.needsYou[1].needsApproval)
        XCTAssertEqual(s.rows.map(\.key), ["waiting", "ask", "busy", "old"])
    }

    func testListsAreCappedButCountsCoverEverySession() {
        let many = (0..<9).map { session("s\($0)", status: "active", at: Double($0)) }
        let s = WidgetSummary.make(hubId: "h", hubName: "mac", sessions: many, inbox: [],
                                   spendUsd: nil, spendDay: nil, at: now)
        XCTAssertEqual(s.working.count, WidgetSnapshot.rowLimit)
        XCTAssertEqual(s.working.first?.key, "s8")
        XCTAssertEqual(s.workingCount, 9)
        XCTAssertEqual(s.needsYouCount, 0)
    }

    func testSpendShowsOnlyOnItsOwnDay() {
        let utc = TimeZone(identifier: "UTC")!
        var s = snapshot(spend: 3)
        s.spendDay = WidgetSummary.day(now, timeZone: utc)
        XCTAssertEqual(s.spendToday(now: now, timeZone: utc), 3)
        XCTAssertNil(s.spendToday(now: now.addingTimeInterval(86_400), timeZone: utc))
        XCTAssertEqual(WidgetSummary.day(Date(timeIntervalSince1970: 0), timeZone: utc), "1970-01-01")
    }

    func testSameContentIgnoresWhenItWasFetched() {
        let a = snapshot(at: now), b = snapshot(at: now.addingTimeInterval(60))
        XCTAssertTrue(a.sameContent(as: b))
        XCTAssertFalse(a.sameContent(as: snapshot(spend: 2)))
    }
}

final class WidgetSnapshotStoreTests: XCTestCase {
    func testRoundTripAndChangeDetection() {
        let store = tempStore()
        XCTAssertNil(store.read())
        XCTAssertTrue(store.write(snapshot()))
        XCTAssertEqual(store.read(), snapshot())
        // Only the time moved: written, but no redraw needed.
        XCTAssertFalse(store.write(snapshot(at: now.addingTimeInterval(15))))
        XCTAssertEqual(store.read()?.fetchedAt, now.addingTimeInterval(15))
        XCTAssertTrue(store.write(snapshot(spend: 9)))
    }

    func testClearDropsOnlyTheNamedHubsSnapshot() {
        let store = tempStore()
        store.write(snapshot(hubId: "a"))
        XCTAssertFalse(store.clear(hubId: "b"))
        XCTAssertNotNil(store.read())
        XCTAssertTrue(store.clear(hubId: "a"))
        XCTAssertNil(store.read())
    }

    func testNoContainerKeepsNothing() {
        let store = WidgetSnapshotStore(directory: nil)
        XCTAssertFalse(store.write(snapshot()))
        XCTAssertNil(store.read())
    }

    func testPublisherReloadsOnlyOnChangeAndCarriesTheSpendOver() {
        let store = tempStore()
        var reloads = 0
        let publisher = WidgetPublisher(store: store) { reloads += 1 }
        let sessions = [session("a", status: "active", at: 1)]
        publisher.publish(hubId: "h", hubName: "mac", sessions: sessions, inbox: [], at: now)
        publisher.publish(hubId: "h", hubName: "mac", sessions: sessions, inbox: [], at: now.addingTimeInterval(15))
        XCTAssertEqual(reloads, 1)
        publisher.publishSpend(hubId: "h", usd: 4, at: now)
        XCTAssertEqual(reloads, 2)
        publisher.publish(hubId: "h", hubName: "mac", sessions: [], inbox: [], at: now)
        XCTAssertEqual(reloads, 3)
        XCTAssertEqual(store.read()?.spendUsd, 4)
        // Another hub's snapshot doesn't inherit this one's spend.
        publisher.publish(hubId: "other", hubName: "box", sessions: [], inbox: [], at: now)
        XCTAssertNil(store.read()?.spendUsd)
        publisher.publishSpend(hubId: "h", usd: 7, at: now)
        XCTAssertNil(store.read()?.spendUsd)
        publisher.hubChanged(dropping: "other")
        XCTAssertNil(store.read())
        XCTAssertEqual(reloads, 5)
    }
}

final class WidgetLoaderTests: XCTestCase {
    private func fetched(_ sessions: [SessionRow] = [session("a", status: "active", at: 1)],
                         spend: Double? = 1.5) -> WidgetLoader.Fetched {
        WidgetLoader.Fetched(sessions: SessionsResponse(sessions: sessions, machines: nil, serverTime: nil),
                             inbox: InboxResponse(items: [inbox("q", status: "approval", at: 2)]), spendUsd: spend)
    }

    private func paired(_ hubs: PairedHub...) -> HubList {
        var list = HubList()
        for h in hubs { list.adopt(h) }
        return list
    }

    func testNotPairedWithoutAHub() async {
        let loader = WidgetLoader(store: tempStore()) { _ in XCTFail("no fetch"); return self.fetched() }
        let content = await loader.load(hubs: HubList(), now: now)
        XCTAssertEqual(content, .notPaired)
    }

    func testAFetchIsDrawnAndKept() async {
        let store = tempStore()
        let loader = WidgetLoader(store: store) { _ in self.fetched() }
        guard case .summary(let s, let stale) = await loader.load(hubs: paired(hub()), now: now) else {
            return XCTFail("expected a summary")
        }
        XCTAssertFalse(stale)
        XCTAssertEqual(s.needsYouCount, 1)
        XCTAssertEqual(s.workingCount, 1)
        XCTAssertEqual(s.spendToday(now: now), 1.5)
        XCTAssertEqual(s.hubName, "mac.tail.ts.net")
        XCTAssertEqual(store.read(), s)
    }

    func testUnreachableFallsBackToTheLastSnapshotAsStale() async {
        let store = tempStore()
        let old = snapshot(at: now.addingTimeInterval(-3600))
        store.write(old)
        let loader = WidgetLoader(store: store) { _ in throw HubError.network(URLError(.timedOut)) }
        let content = await loader.load(hubs: paired(hub()), now: now)
        XCTAssertEqual(content, .summary(old, stale: true))
    }

    func testUnreachableWithNothingCachedSaysSo() async {
        let store = tempStore()
        // Another hub's snapshot is never shown for this one.
        store.write(snapshot(hubId: "http://other:8477"))
        let loader = WidgetLoader(store: store) { _ in throw HubError.network(URLError(.timedOut)) }
        let content = await loader.load(hubs: paired(hub()), now: now)
        XCTAssertEqual(content, .unreachable(hubName: "mac.tail.ts.net"))
    }

    func testSignedOutReadsAsNotPaired() async {
        let store = tempStore()
        store.write(snapshot())
        let loader = WidgetLoader(store: store) { _ in
            throw HubError(status: 401, code: "unauthorized", message: "no")
        }
        let content = await loader.load(hubs: paired(hub()), now: now)
        XCTAssertEqual(content, .notPaired)
        XCTAssertNil(store.read())
    }

    func testAFreshSnapshotWithTodaysSpendSkipsTheFetch() async {
        let store = tempStore()
        store.write(snapshot(at: now.addingTimeInterval(-30)))
        var fetches = 0
        let loader = WidgetLoader(store: store) { _ in fetches += 1; return self.fetched() }
        _ = await loader.load(hubs: paired(hub()), now: now)
        XCTAssertEqual(fetches, 0)
        // Without today's spend it asks the hub, which fills it in.
        store.write(snapshot(spend: nil, day: nil, at: now.addingTimeInterval(-30)))
        _ = await loader.load(hubs: paired(hub()), now: now)
        XCTAssertEqual(fetches, 1)
        XCTAssertFalse(WidgetLoader.canReuse(snapshot(at: now.addingTimeInterval(-600)), now: now))
    }

    func testAFailedOverviewKeepsTodaysSpend() async {
        let store = tempStore()
        store.write(snapshot(spend: 6, at: now.addingTimeInterval(-3600)))
        let loader = WidgetLoader(store: store) { _ in self.fetched(spend: nil) }
        guard case .summary(let s, _) = await loader.load(hubs: paired(hub()), now: now) else {
            return XCTFail("expected a summary")
        }
        XCTAssertEqual(s.spendToday(now: now), 6)
    }
}

final class SessionLinkTests: XCTestCase {
    func testRoundTripsKeysWithReservedCharacters() {
        let link = SessionLink(key: "claude:abc@m1/x y", hubId: "http://mac.tail.ts.net:8477")
        let url = link.url!
        XCTAssertEqual(url.scheme, "quiver")
        XCTAssertEqual(url.host, "session")
        XCTAssertEqual(SessionLink(url), link)
        XCTAssertEqual(SessionLink(URL(string: "quiver://session/codex%3A1")!), SessionLink(key: "codex:1", hubId: nil))
    }

    func testOtherLinksAreNotSessions() {
        XCTAssertNil(SessionLink(URL(string: "quiver://session/")!))
        XCTAssertNil(SessionLink(URL(string: "quiver://issue/o/r/1")!))
        XCTAssertNil(SessionLink(URL(string: "https://session/x")!))
    }

    @MainActor
    func testTheAppOpensTheChatOnTheNamedHub() async {
        let a = hub("a"), b = hub("b")
        var list = HubList()
        list.adopt(a)
        list.adopt(b)
        let app = AppModel(store: MemoryHubStore(list))
        XCTAssertEqual(app.hub, b)
        await app.handle(SessionLink(key: "k:1", hubId: a.id).url!)
        XCTAssertEqual(app.hub, a)
        XCTAssertEqual(app.pendingSession, "k:1")
        app.pendingSession = nil
        // A hub no longer paired: nothing opens.
        await app.handle(SessionLink(key: "k:2", hubId: "http://gone:8477").url!)
        XCTAssertNil(app.pendingSession)
        XCTAssertEqual(app.hub, a)
        app.stopRefreshing()
    }
}
