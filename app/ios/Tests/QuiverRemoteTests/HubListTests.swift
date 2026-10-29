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
        list.names[hub("server").id] = "home-server"
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
        list.names[h.id] = "dev-macbook-pro.local"
        XCTAssertEqual(list.name(of: h), "dev-macbook-pro.local")
    }

    func testAnAddressShowsItsPortOnlyWhenItIsntTheDefault() {
        XCTAssertEqual(hub("mac").address, "mac")
        let other = PairedHub(baseURL: URL(string: "https://mac.tail.ts.net:9443")!,
                              credential: Credential(deviceId: "d", credential: "c", name: nil))
        XCTAssertEqual(other.address, "mac.tail.ts.net:9443")
        var list = HubList()
        list.adopt(other)
        XCTAssertEqual(list.name(of: other), "mac.tail.ts.net:9443", "unnamed hubs on one machine read apart")
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
    func testUnpairingDropsOnlyTheHubOnScreen() async {
        var start = HubList()
        start.adopt(hub("mac"))
        start.adopt(hub("server"))
        let store = MemoryHubStore(start)
        let suite = "quiver.tests.\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suite) }
        let targets = SSHTargetStore(defaults: UserDefaults(suiteName: suite)!)
        let target = SSHTarget(host: "server", port: 22, user: "u", hostKeys: ["ssh-ed25519 AAAA"])
        targets.save(target, for: hub("server").id)
        targets.save(target, for: hub("mac").id)
        let app = AppModel(store: store, sshTargets: targets)
        let told = expectation(description: "the hub is told")
        var revoked: [String] = []
        app.revokeOnHub = { client, deviceId in
            revoked.append("\(client.baseURL.host ?? "") \(deviceId) \(client.credential ?? "")")
            told.fulfill()
        }
        app.unpair()
        app.stopRefreshing()
        XCTAssertEqual(app.hubs.hubs.map(\.baseURL.host), ["mac"])
        XCTAssertEqual(app.hub?.baseURL.host, "mac")
        await fulfillment(of: [told], timeout: 2)
        XCTAssertEqual(revoked, ["server d-server c"], "signed out with the credential it had")
        XCTAssertNil(targets.target(for: hub("server").id), "its console target goes with it")
        XCTAssertEqual(targets.target(for: hub("mac").id), target)
        app.revokeOnHub = { _, _ in }
        app.unpair()
        XCTAssertNil(app.hub)
        XCTAssertTrue(store.list.hubs.isEmpty)
    }

    @MainActor
    func testARefusedCredentialIsDroppedWithoutTellingTheHub() {
        let app = AppModel(hub: hub("mac"))
        var told = false
        app.revokeOnHub = { _, _ in told = true }
        app.unpair(from: HubClient(baseURL: hub("mac").baseURL, credential: "c"))
        XCTAssertNil(app.hub)
        XCTAssertFalse(told, "the hub already refused it; there's nothing to sign out")
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
