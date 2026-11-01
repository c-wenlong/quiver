import XCTest
@testable import SWERemote

/// A Keychain held in memory, with access groups. Several views can
/// share one set of items, the way the app and its extension share the
/// shared group, each seeing only the groups it's entitled to.
final class MemoryKeychain: KeychainItems {
    final class Items {
        var byKey: [String: Data] = [:]
    }

    static let appGroup = "TEAM.com.swe.remote"
    static let sharedGroup = "TEAM.com.swe.remote.shared"

    let items: Items
    /// Where a write with no group lands.
    let defaultGroup: String
    let entitled: [String]

    init(items: Items = Items(), defaultGroup: String = appGroup,
         entitled: [String] = [appGroup, sharedGroup]) {
        self.items = items
        self.defaultGroup = defaultGroup
        self.entitled = entitled
    }

    private func key(_ account: String, _ group: String) -> String { "\(group)/\(account)" }

    func read(account: String, group: String?) -> (data: Data, group: String?)? {
        for g in group.map({ [$0] }) ?? entitled where entitled.contains(g) {
            if let data = items.byKey[key(account, g)] { return (data, g) }
        }
        return nil
    }

    func write(_ data: Data, account: String, group: String?) -> Bool {
        let g = group ?? defaultGroup
        guard entitled.contains(g) else { return false }
        items.byKey[key(account, g)] = data
        return true
    }

    func delete(account: String, group: String?) {
        for g in group.map({ [$0] }) ?? entitled where entitled.contains(g) {
            items.byKey[key(account, g)] = nil
        }
    }

    func groups(holding account: String) -> [String] {
        [Self.appGroup, Self.sharedGroup].filter { items.byKey[key(account, $0)] != nil }
    }
}

/// Moving pairings into the App Group and the shared Keychain group,
/// against an in-memory Keychain so it runs on unsigned builds.
final class SharedHubStoreTests: XCTestCase {
    private var suites: [String] = []

    override func tearDown() {
        for suite in suites { UserDefaults().removePersistentDomain(forName: suite) }
    }

    private func freshDefaults() -> UserDefaults {
        let suite = "quiver.tests.\(UUID().uuidString)"
        suites.append(suite)
        return UserDefaults(suiteName: suite)!
    }

    private func hub(_ host: String, _ cred: String = "c") -> PairedHub {
        PairedHub(baseURL: URL(string: "http://\(host):8477")!,
                  credential: Credential(deviceId: "d-\(host)", credential: cred, name: "phone"))
    }

    private func account(_ hub: PairedHub) -> String { "device-credential \(hub.id)" }

    /// The app as it stored a hub before the shared group: the index in
    /// its own defaults, the credential in its own Keychain group.
    private func oldApp(_ keychain: MemoryKeychain, _ defaults: UserDefaults) -> KeychainHubStore {
        KeychainHubStore(keychain: keychain, sharedGroup: nil, defaults: defaults, legacyDefaults: nil)
    }

    private func newApp(_ keychain: MemoryKeychain, group: UserDefaults, own: UserDefaults) -> KeychainHubStore {
        KeychainHubStore(keychain: keychain, sharedGroup: MemoryKeychain.sharedGroup, defaults: group, legacyDefaults: own)
    }

    private func pairedBefore(_ keychain: MemoryKeychain, _ own: UserDefaults) -> HubList {
        let old = oldApp(keychain, own)
        var list = HubList()
        list.adopt(hub("server", "c2"))
        list.adopt(hub("mac", "c1"))
        list.names[hub("mac").id] = "dev-macbook-pro.local"
        for h in list.hubs { XCTAssertTrue(old.saveCredential(h)) }
        old.saveIndex(list)
        return list
    }

    func testAnUpdatedAppKeepsItsPairingsAndMovesThemIntoTheGroups() {
        let keychain = MemoryKeychain()
        let own = freshDefaults(), group = freshDefaults()
        let list = pairedBefore(keychain, own)
        XCTAssertEqual(keychain.groups(holding: account(hub("mac"))), [MemoryKeychain.appGroup])

        let store = newApp(keychain, group: group, own: own)
        XCTAssertEqual(store.load(), list, "same hubs, names and active hub")
        for h in list.hubs {
            XCTAssertEqual(keychain.groups(holding: account(h)), [MemoryKeychain.sharedGroup], "moved, old copy dropped")
        }
        XCTAssertNotNil(group.data(forKey: "quiver.hubs"))
        XCTAssertEqual(group.string(forKey: "quiver.lastHost"), "mac")
        XCTAssertNil(own.object(forKey: "quiver.hubs"), "the old index goes once copied")
        XCTAssertEqual(store.load(), list, "the next load reads the new places")
    }

    func testACopyTheKeychainRefusesKeepsReadingTheOldItem() {
        // A build whose signature lacks the shared group: writes there fail.
        let items = MemoryKeychain.Items()
        let own = freshDefaults(), group = freshDefaults()
        let list = pairedBefore(MemoryKeychain(items: items), own)
        let unentitled = MemoryKeychain(items: items, entitled: [MemoryKeychain.appGroup])

        let store = newApp(unentitled, group: group, own: own)
        XCTAssertEqual(store.load(), list)
        XCTAssertEqual(unentitled.groups(holding: account(hub("mac"))), [MemoryKeychain.appGroup], "the old item stays")
        XCTAssertEqual(store.load(), list, "and is read again")

        // Once the group is there, the next load moves it.
        let entitled = MemoryKeychain(items: items)
        XCTAssertEqual(newApp(entitled, group: group, own: own).load(), list)
        XCTAssertEqual(entitled.groups(holding: account(hub("mac"))), [MemoryKeychain.sharedGroup])
    }

    func testANewPairingIsStoredWhereTheExtensionReadsIt() {
        let items = MemoryKeychain.Items()
        let group = freshDefaults()
        let app = newApp(MemoryKeychain(items: items), group: group, own: freshDefaults())
        var list = app.load()
        list.adopt(hub("mac", "fresh"))
        XCTAssertTrue(app.saveCredential(list.hubs[0]))
        app.saveIndex(list)

        // The extension sees only the shared group, and has defaults of
        // its own with nothing in them.
        let extensionKeychain = MemoryKeychain(items: items, defaultGroup: "TEAM.com.swe.remote.share",
                                               entitled: [MemoryKeychain.sharedGroup])
        let ext = newApp(extensionKeychain, group: group, own: freshDefaults())
        XCTAssertEqual(ext.load().active?.credential.credential, "fresh")
    }

    func testTheExtensionSeesAnUnmovedPairingAsNone() {
        // A credential the app couldn't move (a build without the shared
        // group) stays in the app's own group: the extension finds no hub.
        let items = MemoryKeychain.Items()
        let group = freshDefaults()
        _ = pairedBefore(MemoryKeychain(items: items), group)
        let ext = KeychainHubStore(keychain: MemoryKeychain(items: items, entitled: [MemoryKeychain.sharedGroup]),
                                   sharedGroup: MemoryKeychain.sharedGroup, defaults: group, legacyDefaults: nil)
        XCTAssertNil(ext.load().active)
    }

    func testAPhonePairedBeforeTheListEndsUpInTheSharedGroup() {
        let keychain = MemoryKeychain()
        let own = freshDefaults(), group = freshDefaults()
        oldApp(keychain, own).writeLegacyPairing(hub("mac", "old-single"))

        let store = newApp(keychain, group: group, own: own)
        XCTAssertEqual(store.load().hubs.map(\.credential.credential), ["old-single"])
        XCTAssertFalse(store.legacyPairingLeft)
        XCTAssertEqual(keychain.groups(holding: account(hub("mac"))), [MemoryKeychain.sharedGroup])
        XCTAssertEqual(keychain.groups(holding: "device-credential"), [])
    }

    func testAListAlreadyInTheGroupWinsOverOneLeftBehind() {
        let keychain = MemoryKeychain()
        let own = freshDefaults(), group = freshDefaults()
        _ = pairedBefore(keychain, own)
        let store = newApp(keychain, group: group, own: own)
        var list = HubList()
        list.adopt(hub("mac", "c1"))
        store.saveIndex(list)
        // An older index reappearing in the app's own defaults is dropped.
        _ = pairedBefore(keychain, own)
        XCTAssertEqual(store.load().hubs.map(\.baseURL.host), ["mac"])
        XCTAssertNil(own.object(forKey: "quiver.hubs"))
    }

    func testAReplacementSavedWithoutTheGroupIsReadOnReturn() {
        // Pair on a shared-capable build: the credential is in the group.
        let items = MemoryKeychain.Items()
        let own = freshDefaults(), group = freshDefaults()
        let entitled = MemoryKeychain(items: items)
        let app = newApp(entitled, group: group, own: own)
        var list = app.load()
        list.adopt(hub("mac", "v1"))
        XCTAssertTrue(app.saveCredential(list.hubs[0]))
        app.saveIndex(list)

        // A build without the entitlement pairs the same hub again. Its
        // "App Group" defaults resolve to its own suite, and the shared
        // write fails, so the copy lands in the app's own group.
        let unentitled = MemoryKeychain(items: items, entitled: [MemoryKeychain.appGroup])
        let downgraded = KeychainHubStore(keychain: unentitled, sharedGroup: MemoryKeychain.sharedGroup,
                                        defaults: own, legacyDefaults: nil)
        XCTAssertTrue(downgraded.saveCredential(hub("mac", "v2")))
        XCTAssertEqual(entitled.groups(holding: account(hub("mac"))),
                       [MemoryKeychain.appGroup, MemoryKeychain.sharedGroup])

        // Back on a shared-capable build the newer copy is read and
        // moved into the group, not ignored behind the stale one.
        XCTAssertEqual(newApp(entitled, group: group, own: own).load()
                        .hubs.map(\.credential.credential), ["v2"])
        XCTAssertEqual(entitled.groups(holding: account(hub("mac"))), [MemoryKeychain.sharedGroup])
        XCTAssertEqual(newApp(entitled, group: group, own: own).load()
                        .hubs.map(\.credential.credential), ["v2"], "the next load reads the moved copy")
    }

    func testUnpairDeletesTheCredentialFromEveryGroup() {
        let items = MemoryKeychain.Items()
        let own = freshDefaults(), group = freshDefaults()
        _ = pairedBefore(MemoryKeychain(items: items), own)
        // A copy left in both groups, as a refused delete would.
        _ = MemoryKeychain(items: items).write(Data("x".utf8), account: account(hub("mac")), group: MemoryKeychain.sharedGroup)
        let keychain = MemoryKeychain(items: items)
        newApp(keychain, group: group, own: own).deleteCredential(hub("mac").id)
        XCTAssertEqual(keychain.groups(holding: account(hub("mac"))), [])
    }
}
