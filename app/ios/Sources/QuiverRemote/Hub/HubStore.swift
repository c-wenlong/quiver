import Foundation
import Security

/// What the app shares with its extensions (the share sheet, the
/// widget): an App Group for the hub list and a Keychain access group
/// for the hubs' credentials. Both are in the entitlements `project.yml`
/// writes.
enum SharedContainer {
    static let appGroup = "group.com.quiver.remote"
    /// The Info.plist key the build fills with
    /// `$(AppIdentifierPrefix)com.quiver.remote.shared`, so the team id
    /// comes from whoever signs the build instead of the source.
    static let keychainGroupKey = "QuiverKeychainGroup"

    static var keychainGroup: String? {
        guard let group = Bundle.main.object(forInfoDictionaryKey: keychainGroupKey) as? String,
              !group.isEmpty, !group.contains("$(") else { return nil }
        return group
    }

    static var defaults: UserDefaults { UserDefaults(suiteName: appGroup) ?? .standard }
}

/// Generic-password items of one Keychain service, by account. Tests
/// swap in one held in memory.
protocol KeychainItems {
    /// The item, and the access group it's in. A nil `group` reads from
    /// any group this app can open.
    func read(account: String, group: String?) -> (data: Data, group: String?)?
    /// Updates the item in place, or adds it. A nil `group` is the app's
    /// default group.
    func write(_ data: Data, account: String, group: String?) -> Bool
    /// A nil `group` deletes the item from every group this app can open.
    func delete(account: String, group: String?)
}

struct SystemKeychain: KeychainItems {
    var service: String

    private func query(account: String, group: String?) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        if let group { q[kSecAttrAccessGroup as String] = group }
        return q
    }

    func read(account: String, group: String?) -> (data: Data, group: String?)? {
        var q = query(account: account, group: group)
        q[kSecReturnData as String] = true
        q[kSecReturnAttributes as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let item = out as? [String: Any], let data = item[kSecValueData as String] as? Data else { return nil }
        return (data, item[kSecAttrAccessGroup as String] as? String)
    }

    /// Never deletes first: a failed add after a delete would lose the
    /// credential that was there.
    func write(_ data: Data, account: String, group: String?) -> Bool {
        let q = query(account: account, group: group)
        let fields: [String: Any] = [kSecValueData as String: data,
                                     kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let updated = SecItemUpdate(q as CFDictionary, fields as CFDictionary)
        guard updated == errSecItemNotFound else { return updated == errSecSuccess }
        return SecItemAdd(q.merging(fields) { _, new in new } as CFDictionary, nil) == errSecSuccess
    }

    func delete(account: String, group: String?) {
        SecItemDelete(query(account: account, group: group) as CFDictionary)
    }
}

/// The hub list on the phone. Credentials go in the Keychain, one item
/// per hub, in the shared access group; addresses, names and the active
/// hub in the App Group's defaults. The share extension reads the same
/// store.
///
/// Pairings made before the shared group move on first load: the index
/// is copied from the app's own defaults, and each credential is copied
/// into the shared group, then dropped from the old one. A copy that
/// fails leaves the old item, which keeps being read until a copy works.
struct KeychainHubStore: HubStore {
    private static let hubsKey = "quiver.hubs"
    private static let activeKey = "quiver.activeHub"
    private static let lastHostKey = "quiver.lastHost"
    /// Before more than one hub: its address, and the Keychain account
    /// that held its credential. Moved into the list on first load.
    private static let legacyHubKey = "quiver.hub"
    private static let legacyAccount = "device-credential"
    /// The WKWebView shell kept the `/v1/pair` JSON here; read once so a
    /// phone paired under the old shell stays paired.
    private static let legacyCredentialKey = "quiver.credentialJSON"
    /// Every defaults key the store owns, moved together into the group.
    private static let indexKeys = [hubsKey, activeKey, lastHostKey, legacyHubKey, legacyCredentialKey]

    /// Tests pass their own Keychain, group and defaults suites.
    var keychain: KeychainItems = SystemKeychain(service: "com.quiver.remote")
    /// Where credentials go so extensions can read them; nil keeps them
    /// in the app's own group (a build without the entitlement).
    var sharedGroup: String? = SharedContainer.keychainGroup
    var defaults: UserDefaults = SharedContainer.defaults
    /// Where the index lived before the App Group.
    var legacyDefaults: UserDefaults? = .standard

    private struct IndexEntry: Codable {
        let url: String
        var name: String?
    }

    func load() -> HubList {
        moveIndexIntoGroup()
        guard let data = defaults.data(forKey: Self.hubsKey),
              let entries = try? JSONDecoder().decode([IndexEntry].self, from: data) else {
            return migrate()
        }
        var list = HubList()
        for entry in entries {
            guard let url = URL(string: entry.url), let cred = credential(for: entry.url) else { continue }
            list.hubs.append(PairedHub(baseURL: url, credential: cred))
            if let name = entry.name { list.names[entry.url] = name }
        }
        list.activeId = defaults.string(forKey: Self.activeKey)
        if list.active?.id != list.activeId { list.activeId = list.active?.id }
        return list
    }

    /// Copies the index from the app's own defaults into the group's, then
    /// drops the old copy. A list already in the group is newer than one
    /// left behind, so it's kept.
    private func moveIndexIntoGroup() {
        guard let old = legacyDefaults, old !== defaults else { return }
        let keys = Self.indexKeys.filter { old.object(forKey: $0) != nil }
        guard !keys.isEmpty else { return }
        if defaults.object(forKey: Self.hubsKey) == nil {
            for key in keys { defaults.set(old.object(forKey: key), forKey: key) }
        }
        for key in keys { old.removeObject(forKey: key) }
    }

    /// A hub's stored credential. The hub a migration moved but couldn't
    /// store again still has its old copy, which is read (and moved, once
    /// that works) instead of losing the pairing.
    private func credential(for id: String) -> Credential? {
        if let cred = stored(account: Self.account(for: id)) { return cred }
        guard defaults.string(forKey: Self.legacyHubKey) == id, let cred = legacyCredential(),
              let url = URL(string: id) else { return nil }
        if saveCredential(PairedHub(baseURL: url, credential: cred)) { dropLegacy() }
        return cred
    }

    /// Reads the shared group first, then any group: an item from before
    /// the shared group is copied there and dropped where it was. If the
    /// copy fails, the old item stays and is read again next time.
    private func stored(account: String) -> Credential? {
        if let group = sharedGroup, let item = keychain.read(account: account, group: group) {
            return try? Hub.decoder.decode(Credential.self, from: item.data)
        }
        guard let item = keychain.read(account: account, group: nil),
              let cred = try? Hub.decoder.decode(Credential.self, from: item.data) else { return nil }
        // Without the old item's group a delete would hit the new copy too.
        if let group = sharedGroup, let oldGroup = item.group, oldGroup != group,
           keychain.write(item.data, account: account, group: group) {
            keychain.delete(account: account, group: oldGroup)
        }
        return cred
    }

    func saveIndex(_ list: HubList) {
        let entries = list.hubs.map { IndexEntry(url: $0.id, name: list.names[$0.id]) }
        if let data = try? JSONEncoder().encode(entries) { defaults.set(data, forKey: Self.hubsKey) }
        defaults.set(list.activeId, forKey: Self.activeKey)
        if let host = list.active?.baseURL.host { defaults.set(host, forKey: Self.lastHostKey) }
    }

    /// Stores in the shared group, or the app's own when the build can't
    /// reach it, so pairing still works there.
    @discardableResult
    func saveCredential(_ hub: PairedHub) -> Bool {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? encoder.encode(hub.credential) else { return false }
        let account = Self.account(for: hub.id)
        if let group = sharedGroup, keychain.write(data, account: account, group: group) { return true }
        return keychain.write(data, account: account, group: nil)
    }

    func deleteCredential(_ id: String) {
        keychain.delete(account: Self.account(for: id), group: nil)
    }

    /// Last hub address paired, kept after an unpair so pairing again
    /// doesn't need it retyped. Read before the first load can move it,
    /// so the app's own defaults are a fallback.
    static var lastHost: String? {
        for defaults in [SharedContainer.defaults, .standard] {
            if let host = defaults.string(forKey: lastHostKey)
                ?? defaults.string(forKey: legacyHubKey).flatMap({ URL(string: $0)?.host }) {
                return host
            }
        }
        return nil
    }

    /// The one hub a phone paired before this list existed, moved into
    /// the list. The old copies go only once the new one is stored; until
    /// then `credential(for:)` keeps reading them.
    private func migrate() -> HubList {
        guard let raw = defaults.string(forKey: Self.legacyHubKey), let url = URL(string: raw),
              let cred = legacyCredential() else { return HubList() }
        var list = HubList()
        list.adopt(PairedHub(baseURL: url, credential: cred))
        saveIndex(list)
        if saveCredential(list.hubs[0]) { dropLegacy() }
        return list
    }

    private func legacyCredential() -> Credential? {
        if let item = keychain.read(account: Self.legacyAccount, group: nil),
           let cred = try? Hub.decoder.decode(Credential.self, from: item.data) {
            return cred
        }
        if let legacy = defaults.string(forKey: Self.legacyCredentialKey),
           let cred = try? Hub.decoder.decode(Credential.self, from: Data(legacy.utf8)) {
            return cred
        }
        return nil
    }

    private func dropLegacy() {
        keychain.delete(account: Self.legacyAccount, group: nil)
        defaults.removeObject(forKey: Self.legacyCredentialKey)
        defaults.removeObject(forKey: Self.legacyHubKey)
    }

    private static func account(for id: String) -> String { "device-credential \(id)" }

    /// Writes a pairing the way the app stored one before the hub list,
    /// for the migration tests.
    func writeLegacyPairing(_ hub: PairedHub, in defaults: UserDefaults? = nil) {
        (defaults ?? self.defaults).set(hub.id, forKey: Self.legacyHubKey)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        if let data = try? encoder.encode(hub.credential) {
            _ = keychain.write(data, account: Self.legacyAccount, group: nil)
        }
    }

    /// Whether any copy of a pre-list pairing is still stored: the old
    /// Keychain item, the webview shell's JSON or the old address (tests).
    var legacyPairingLeft: Bool {
        keychain.read(account: Self.legacyAccount, group: nil) != nil
            || defaults.object(forKey: Self.legacyCredentialKey) != nil
            || defaults.object(forKey: Self.legacyHubKey) != nil
    }

    /// Forgets everything this store holds (tests).
    func wipe() {
        for entry in (defaults.data(forKey: Self.hubsKey).flatMap { try? JSONDecoder().decode([IndexEntry].self, from: $0) } ?? []) {
            deleteCredential(entry.url)
        }
        keychain.delete(account: Self.legacyAccount, group: nil)
        for key in Self.indexKeys {
            defaults.removeObject(forKey: key)
            legacyDefaults?.removeObject(forKey: key)
        }
    }
}
