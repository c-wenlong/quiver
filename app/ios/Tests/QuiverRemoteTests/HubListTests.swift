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
    func testPairingAndUnpairingStoreAndDeleteTheCredential() {
        let store = MemoryHubStore()
        let app = AppModel(store: store)
        app.adopt(hub("mac", "m"))
        app.adopt(hub("server", "s"))
        app.stopRefreshing()
        XCTAssertEqual(store.credentials[hub("mac").id]?.credential, "m")
        XCTAssertEqual(store.credentials[hub("server").id]?.credential, "s")
        app.unpair(from: HubClient(baseURL: hub("server").baseURL, credential: "s"))
        app.stopRefreshing()
        XCTAssertNil(store.credentials[hub("server").id], "the unpaired hub's credential is gone")
        XCTAssertEqual(store.credentials[hub("mac").id]?.credential, "m", "the other hub's stays")
    }

    @MainActor
    func testARefusedCredentialIsDroppedWithoutTellingTheHub() async {
        let app = AppModel(hub: hub("mac"))
        // The hub already refused it; there's nothing to sign out. The
        // revoke runs in a Task, so wait long enough for one to land.
        let told = expectation(description: "no revoke")
        told.isInverted = true
        app.revokeOnHub = { _, _ in told.fulfill() }
        app.unpair(from: HubClient(baseURL: hub("mac").baseURL, credential: "c"))
        XCTAssertNil(app.hub)
        await fulfillment(of: [told], timeout: 0.5)
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

    @MainActor
    func testAPairLinkFromOutsideWaitsForAYes() async {
        let app = AppModel(hub: nil)
        let url = URL(string: "quiver://pair?host=box.example&port=9000&token=t")!
        await app.handle(url)
        XCTAssertEqual(app.pairOffer, PairLink(url))
        XCTAssertEqual(app.pairOffer?.address, "box.example:9000", "the sheet names the hub")
        XCTAssertFalse(app.pairing.busy, "nothing went to the hub")
        XCTAssertNil(app.hub)
        app.declinePairOffer()
        XCTAssertNil(app.pairOffer)
        XCTAssertNil(app.hub)
    }

    @MainActor
    func testASecondPairWhileOneIsOutIsDropped() async {
        let app = AppModel(hub: nil)
        app.pairing.busy = true
        let paired = await app.pair(with: PairLink(URL(string: "quiver://pair?host=mac&token=t")!)!)
        XCTAssertFalse(paired)
        XCTAssertTrue(app.pairing.busy, "the pair already out still owns the spinner")
        XCTAssertNil(app.pairing.error)
    }

    @MainActor
    func testACancelledRefreshDoesntReadAsOffline() async {
        let app = AppModel(hub: hub("mac"))
        let refresh = Task { await app.refresh() }
        refresh.cancel()
        await refresh.value
        XCTAssertEqual(app.connection, .connecting, "a cancel isn't the hub being unreachable")
        XCTAssertNil(app.loadError)
    }

    @MainActor
    func testAHubWhoseCredentialWontSaveIsntListed() {
        let store = MemoryHubStore()
        store.refusesSaves = true
        let app = AppModel(store: store)
        XCTAssertFalse(app.adopt(hub("mac")))
        XCTAssertNil(app.hub, "listed without its credential, it would be gone at the next launch")
        XCTAssertTrue(store.list.hubs.isEmpty)
        XCTAssertNotNil(app.pairing.error)
        XCTAssertTrue(app.pairing.codeSpent, "the hub took the code; Pair can't send it again")
    }

    @MainActor
    func testASpentPairLinkIsntSentAgain() async {
        let app = AppModel(hub: nil)
        await app.handle(URL(string: "quiver://pair?host=box.example&token=t")!)
        app.pairing.codeSpent = true
        await app.acceptPairOffer()
        XCTAssertFalse(app.pairing.busy, "nothing went to the hub")
        XCTAssertNotNil(app.pairOffer)
        // A new link is a new code.
        await app.handle(URL(string: "quiver://pair?host=box.example&token=t2")!)
        XCTAssertFalse(app.pairing.codeSpent)
    }

    func testATokenTheHubTurnsDownCantBeSentAgain() {
        // What the hub answers for an expired or used pair token.
        let refused = HubClient.error(status: 403, body: Data(#"{"error":{"code":"pair_failed","message":"unknown or expired pair token"}}"#.utf8))
        XCTAssertTrue(PairingFlow.spendsCode(refused), "Pair would only get the same refusal; the sheet offers Close")
        // A retry can work after these.
        XCTAssertFalse(PairingFlow.spendsCode(HubClient.error(status: 429, body: Data(#"{"error":{"code":"rate_limited"}}"#.utf8))))
        XCTAssertFalse(PairingFlow.spendsCode(URLError(.cannotConnectToHost)))
    }

    @MainActor
    func testAnEarlierLinksRefusalLeavesAFreshLinksPair() async {
        let app = AppModel(hub: nil)
        let refused = HubClient.error(status: 403, body: Data(#"{"error":{"code":"pair_failed"}}"#.utf8))
        let old = PairLink(URL(string: "quiver://pair?host=box.example&token=old")!)!
        await app.handle(URL(string: "quiver://pair?host=box.example&token=new")!)
        // The old link's pair was still out when the new link arrived.
        app.pairFailed(refused, link: old)
        XCTAssertFalse(app.pairing.codeSpent, "the new token was never sent; Pair must stay")
        app.pairFailed(refused, link: app.pairOffer!)
        XCTAssertTrue(app.pairing.codeSpent)
    }
}
