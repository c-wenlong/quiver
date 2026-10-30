import XCTest
@testable import QuiverRemote

/// The real store, on a Keychain service and defaults suite of its own.
final class KeychainHubStoreTests: XCTestCase {
    private var suite = ""
    private var store = KeychainHubStore()

    override func setUp() {
        suite = "quiver.tests.\(UUID().uuidString)"
        store = KeychainHubStore(service: "com.quiver.remote.tests.\(UUID().uuidString)",
                                 defaults: UserDefaults(suiteName: suite)!)
    }

    override func tearDown() {
        store.wipe()
        UserDefaults().removePersistentDomain(forName: suite)
    }

    private func hub(_ host: String, _ cred: String = "c") -> PairedHub {
        PairedHub(baseURL: URL(string: "http://\(host):8477")!,
                  credential: Credential(deviceId: "d-\(host)", credential: cred, name: "phone"))
    }

    func testTwoHubsRoundTripWithNamesAndTheActiveOne() {
        var list = HubList()
        list.adopt(hub("mac", "c1"))
        list.adopt(hub("server", "c2"))
        list.names[hub("mac").id] = "dev-macbook-pro.local"
        list.activeId = hub("mac").id
        for h in list.hubs { XCTAssertTrue(store.saveCredential(h)) }
        store.saveIndex(list)

        let loaded = store.load()
        XCTAssertEqual(loaded, list)
        XCTAssertEqual(loaded.hubs.map(\.credential.credential), ["c1", "c2"], "each hub keeps its own credential")
    }

    func testSavingAgainReplacesTheCredentialInPlace() {
        XCTAssertTrue(store.saveCredential(hub("mac", "old")))
        XCTAssertTrue(store.saveCredential(hub("mac", "new")))
        var list = HubList()
        list.adopt(hub("mac"))
        store.saveIndex(list)
        XCTAssertEqual(store.load().hubs.map(\.credential.credential), ["new"])
    }

    func testDeletingOneHubKeepsTheOther() {
        var list = HubList()
        list.adopt(hub("mac"))
        list.adopt(hub("server"))
        for h in list.hubs { store.saveCredential(h) }
        store.deleteCredential(hub("server").id)
        list.remove(hub("server").id)
        store.saveIndex(list)

        XCTAssertEqual(store.load().hubs.map(\.baseURL.host), ["mac"])
    }

    func testAPhonePairedBeforeTheListKeepsItsHub() {
        store.writeLegacyPairing(hub("mac", "old-single"))

        let first = store.load()
        XCTAssertEqual(first.hubs.map(\.credential.credential), ["old-single"])
        XCTAssertEqual(first.active?.baseURL.host, "mac")
        XCTAssertFalse(store.legacyPairingLeft, "the old copies go once the new one is stored")
        // Moved: the next load reads the list, not the old copy.
        XCTAssertEqual(store.load(), first)
    }

    func testAMigrationWhoseNewCopyIsMissingStillFindsTheOldOne() {
        // What a failed Keychain write leaves: the list names the hub, its
        // new item is missing, the old copy is still there.
        store.writeLegacyPairing(hub("mac", "old-single"))
        var list = HubList()
        list.adopt(hub("mac", "old-single"))
        store.saveIndex(list)

        XCTAssertTrue(store.legacyPairingLeft)
        XCTAssertEqual(store.load().hubs.map(\.credential.credential), ["old-single"])
        // That load moved it and dropped the old copy, so this one reads
        // the new item.
        XCTAssertFalse(store.legacyPairingLeft)
        XCTAssertEqual(store.load().hubs.map(\.credential.credential), ["old-single"])
    }
}
