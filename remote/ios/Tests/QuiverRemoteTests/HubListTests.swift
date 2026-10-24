import XCTest
@testable import QuiverRemote

final class HubListTests: XCTestCase {
    private func hub(_ host: String, _ cred: String = "c") -> PairedHub {
        PairedHub(baseURL: URL(string: "http://\(host):8477")!,
                  credential: Credential(deviceId: "d-\(host)", credential: cred, name: nil))
    }

    func testPairingAnotherHubAddsItAndShowsIt() {
        var list = HubList()
        list.adopt(hub("mac"))
        list.adopt(hub("server"))
        XCTAssertEqual(list.hubs.map(\.baseURL.host), ["mac", "server"])
        XCTAssertEqual(list.active?.baseURL.host, "server")
    }

    func testPairingTheSameHubAgainReplacesItsCredential() {
        var list = HubList()
        list.adopt(hub("mac", "old"))
        list.adopt(hub("server"))
        list.adopt(hub("mac", "new"))
        XCTAssertEqual(list.hubs.count, 2)
        XCTAssertEqual(list.hubs[0].credential.credential, "new", "keeps its place")
        XCTAssertEqual(list.active?.baseURL.host, "mac")
    }

    func testRemovingTheActiveHubShowsTheNextOne() {
        var list = HubList()
        list.adopt(hub("mac"))
        list.adopt(hub("server"))
        list.names[hub("server").id] = "kai-home-server"
        list.remove(hub("server").id)
        XCTAssertEqual(list.active?.baseURL.host, "mac")
        XCTAssertNil(list.names[hub("server").id])
        list.remove(hub("mac").id)
        XCTAssertNil(list.active)
    }

    func testAHubIsNamedByItsMachineElseItsHost() {
        var list = HubList()
        let h = hub("100.66.100.47")
        list.adopt(h)
        XCTAssertEqual(list.name(of: h), "100.66.100.47")
        list.names[h.id] = "kais-macbook-pro.local"
        XCTAssertEqual(list.name(of: h), "kais-macbook-pro.local")
    }

    @MainActor
    func testSwitchingHubsKeepsBothAndClearsWhatWasShown() {
        var start = HubList()
        start.adopt(hub("mac"))
        start.adopt(hub("server"))
        let store = MemoryHubStore(start)
        let app = AppModel(store: store)
        XCTAssertEqual(app.hub?.baseURL.host, "server")
        app.switchHub(to: hub("mac").id)
        app.stopRefreshing()
        XCTAssertEqual(app.hub?.baseURL.host, "mac")
        XCTAssertEqual(app.hubs.hubs.count, 2)
        XCTAssertEqual(store.list.activeId, hub("mac").id, "the choice is saved")
        XCTAssertTrue(app.sessions.isEmpty)
        XCTAssertFalse(app.loadedOnce)
    }

    @MainActor
    func testUnpairingDropsOnlyTheHubOnScreen() {
        var start = HubList()
        start.adopt(hub("mac"))
        start.adopt(hub("server"))
        let store = MemoryHubStore(start)
        let app = AppModel(store: store)
        app.unpair()
        app.stopRefreshing()
        XCTAssertEqual(app.hubs.hubs.map(\.baseURL.host), ["mac"])
        XCTAssertEqual(app.hub?.baseURL.host, "mac")
        app.unpair()
        XCTAssertNil(app.hub)
        XCTAssertTrue(store.list.hubs.isEmpty)
    }

    @MainActor
    func testARefusalFromAHubLeftBehindUnpairsNothing() {
        var start = HubList()
        start.adopt(hub("mac"))
        start.adopt(hub("server"))
        let app = AppModel(store: MemoryHubStore(start))
        let oldClient = HubClient(baseURL: hub("server").baseURL, credential: "c")
        app.switchHub(to: hub("mac").id)
        app.stopRefreshing()
        app.unpair(from: oldClient)
        XCTAssertEqual(app.hubs.hubs.count, 2, "the 401 came from the hub it switched away from")
        app.unpair(from: HubClient(baseURL: hub("mac").baseURL, credential: "c"))
        XCTAssertEqual(app.hubs.hubs.map(\.baseURL.host), ["server"])
    }

    @MainActor
    func testAdoptingAddsAHubWithoutUnpairingTheOthers() {
        let app = AppModel(hub: hub("mac"))
        app.adopt(hub("server"))
        app.stopRefreshing()
        XCTAssertEqual(app.hubs.hubs.map(\.baseURL.host), ["mac", "server"])
        XCTAssertEqual(app.hub?.baseURL.host, "server")
    }
}
