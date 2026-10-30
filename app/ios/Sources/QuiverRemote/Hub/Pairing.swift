import Foundation
import Security

/// Where a hub is and the credential it issued this device.
struct PairedHub: Equatable, Identifiable {
    var baseURL: URL
    var credential: Credential

    var id: String { baseURL.absoluteString }

    /// The host, with the port when it isn't the hub's default, so two
    /// hubs on one machine read apart.
    var address: String { Self.address(of: baseURL) }

    static func address(of url: URL) -> String {
        let host = url.host ?? url.absoluteString
        guard let port = url.port, port != Hub.defaultPort else { return host }
        return "\(host):\(port)"
    }
}

/// Every hub this phone is paired with, oldest first, and the one it
/// shows. Pairing another hub adds it here instead of replacing one.
struct HubList: Equatable {
    var hubs: [PairedHub] = []
    var activeId: String?
    /// Each hub's machine name as it last reported it, by hub id, so the
    /// menu can name a hub that isn't the one on screen.
    var names: [String: String] = [:]

    var active: PairedHub? { hubs.first { $0.id == activeId } ?? hubs.first }

    /// Adds `hub`, or replaces the one at the same address (pairing the
    /// same hub again issues a new credential), and makes it active.
    mutating func adopt(_ hub: PairedHub) {
        if let i = hubs.firstIndex(where: { $0.id == hub.id }) {
            hubs[i] = hub
        } else {
            hubs.append(hub)
        }
        activeId = hub.id
    }

    /// Drops one hub; the next one left becomes active if it was.
    mutating func remove(_ id: String) {
        hubs.removeAll { $0.id == id }
        names[id] = nil
        if activeId == id || !hubs.contains(where: { $0.id == activeId }) { activeId = hubs.first?.id }
    }

    /// How menus name a hub: its machine name, else its host.
    func name(of hub: PairedHub) -> String {
        names[hub.id] ?? hub.address
    }
}

/// Where the hub list lives. Credentials go in the Keychain, one item
/// per hub; addresses, names and the active hub in UserDefaults.
protocol HubStore {
    func load() -> HubList
    /// Addresses, names and which hub is active.
    func saveIndex(_ list: HubList)
    /// True once the hub's credential is stored.
    @discardableResult func saveCredential(_ hub: PairedHub) -> Bool
    func deleteCredential(_ id: String)
}

/// Keeps the list in memory only, for tests and previews. Credentials
/// are kept by hub id, as the Keychain would, so a test can see what
/// was stored and what was deleted.
final class MemoryHubStore: HubStore {
    private(set) var list: HubList
    private(set) var credentials: [String: Credential]
    /// Stands in for a Keychain that refuses writes (tests).
    var refusesSaves = false

    init(_ list: HubList = HubList()) {
        self.list = list
        credentials = Dictionary(list.hubs.map { ($0.id, $0.credential) }, uniquingKeysWith: { _, new in new })
    }

    func load() -> HubList { list }
    func saveIndex(_ list: HubList) { self.list = list }
    func saveCredential(_ hub: PairedHub) -> Bool {
        guard !refusesSaves else { return false }
        credentials[hub.id] = hub.credential
        return true
    }
    func deleteCredential(_ id: String) { credentials[id] = nil }
}

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

    /// Tests pass their own Keychain service and defaults suite.
    var service = "com.quiver.remote"
    var defaults = UserDefaults.standard

    private struct IndexEntry: Codable {
        let url: String
        var name: String?
    }

    func load() -> HubList {
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

    /// A hub's stored credential. The hub a migration moved but couldn't
    /// store again still has its old copy, which is read (and moved, once
    /// that works) instead of losing the pairing.
    private func credential(for id: String) -> Credential? {
        if let raw = keychainRead(account: Self.account(for: id)),
           let cred = try? Hub.decoder.decode(Credential.self, from: raw) {
            return cred
        }
        guard defaults.string(forKey: Self.legacyHubKey) == id, let cred = legacyCredential(),
              let url = URL(string: id) else { return nil }
        if saveCredential(PairedHub(baseURL: url, credential: cred)) { dropLegacy() }
        return cred
    }

    func saveIndex(_ list: HubList) {
        let entries = list.hubs.map { IndexEntry(url: $0.id, name: list.names[$0.id]) }
        if let data = try? JSONEncoder().encode(entries) { defaults.set(data, forKey: Self.hubsKey) }
        defaults.set(list.activeId, forKey: Self.activeKey)
        if let host = list.active?.baseURL.host { defaults.set(host, forKey: Self.lastHostKey) }
    }

    @discardableResult
    func saveCredential(_ hub: PairedHub) -> Bool {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? encoder.encode(hub.credential) else { return false }
        return keychainWrite(data, account: Self.account(for: hub.id))
    }

    func deleteCredential(_ id: String) {
        SecItemDelete(query(account: Self.account(for: id)) as CFDictionary)
    }

    /// Last hub address paired, kept after an unpair so pairing again
    /// doesn't need it retyped.
    static var lastHost: String? {
        let defaults = UserDefaults.standard
        return defaults.string(forKey: lastHostKey)
            ?? defaults.string(forKey: legacyHubKey).flatMap { URL(string: $0)?.host }
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
        if let data = keychainRead(account: Self.legacyAccount),
           let cred = try? Hub.decoder.decode(Credential.self, from: data) {
            return cred
        }
        if let legacy = defaults.string(forKey: Self.legacyCredentialKey),
           let cred = try? Hub.decoder.decode(Credential.self, from: Data(legacy.utf8)) {
            return cred
        }
        return nil
    }

    private func dropLegacy() {
        SecItemDelete(query(account: Self.legacyAccount) as CFDictionary)
        defaults.removeObject(forKey: Self.legacyCredentialKey)
        defaults.removeObject(forKey: Self.legacyHubKey)
    }

    private static func account(for id: String) -> String { "device-credential \(id)" }

    private func query(account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private func keychainRead(account: String) -> Data? {
        var q = query(account: account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    /// Updates the item in place, or adds it when there is none. Never
    /// deletes first: a failed add after a delete would lose the
    /// credential that was there.
    private func keychainWrite(_ data: Data, account: String) -> Bool {
        let q = query(account: account)
        let fields: [String: Any] = [kSecValueData as String: data,
                                     kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let updated = SecItemUpdate(q as CFDictionary, fields as CFDictionary)
        guard updated == errSecItemNotFound else { return updated == errSecSuccess }
        return SecItemAdd(q.merging(fields) { _, new in new } as CFDictionary, nil) == errSecSuccess
    }

    /// Writes a pairing the way the app stored one before the hub list,
    /// for the migration tests.
    func writeLegacyPairing(_ hub: PairedHub) {
        defaults.set(hub.id, forKey: Self.legacyHubKey)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        if let data = try? encoder.encode(hub.credential) { _ = keychainWrite(data, account: Self.legacyAccount) }
    }

    /// Whether any copy of a pre-list pairing is still stored: the old
    /// Keychain item, the webview shell's JSON or the old address (tests).
    var legacyPairingLeft: Bool {
        keychainRead(account: Self.legacyAccount) != nil
            || defaults.object(forKey: Self.legacyCredentialKey) != nil
            || defaults.object(forKey: Self.legacyHubKey) != nil
    }

    /// Forgets everything this store holds (tests).
    func wipe() {
        for entry in (defaults.data(forKey: Self.hubsKey).flatMap { try? JSONDecoder().decode([IndexEntry].self, from: $0) } ?? []) {
            deleteCredential(entry.url)
        }
        SecItemDelete(query(account: Self.legacyAccount) as CFDictionary)
        for key in [Self.hubsKey, Self.activeKey, Self.lastHostKey, Self.legacyHubKey, Self.legacyCredentialKey] {
            defaults.removeObject(forKey: key)
        }
    }
}

/// Parses what a pair QR or link carries:
/// `quiver://pair?host=…&port=…&token=…[&scheme=https][&name=…]` or the browser form
/// `http://host:port/#pair=<token>[&name=…]` (older `/app/#pair=` links
/// parse the same: the fragment is all that matters).
struct PairLink: Equatable {
    let baseURL: URL
    let token: String
    let name: String?

    init?(_ url: URL) {
        if url.scheme == "quiver", url.host == "pair" {
            let q = Self.query(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems)
            guard let host = q["host"], !host.isEmpty, let token = q["token"], !token.isEmpty else { return nil }
            let port = q["port"].flatMap(Int.init) ?? Hub.defaultPort
            // A hub behind `tailscale serve` adds `scheme=https`; links
            // without it are plain http, as before.
            let scheme = q["scheme"] == "https" ? "https" : "http"
            guard let base = URL(string: "\(scheme)://\(host):\(port)") else { return nil }
            self.baseURL = base
            self.token = token
            self.name = q["name"]?.isEmpty == false ? q["name"] : nil
        } else if url.scheme == "http" || url.scheme == "https", let fragment = url.fragment, let host = url.host {
            var parts = URLComponents()
            parts.query = fragment
            let q = Self.query(parts.queryItems)
            guard let token = q["pair"], !token.isEmpty else { return nil }
            // No port on an https link is 443 (`tailscale serve`), not the hub's own.
            let port = url.port.map { ":\($0)" } ?? (url.scheme == "https" ? "" : ":\(Hub.defaultPort)")
            guard let base = URL(string: "\(url.scheme!)://\(host)\(port)") else { return nil }
            self.baseURL = base
            self.token = token
            self.name = q["name"]?.isEmpty == false ? q["name"] : nil
        } else {
            return nil
        }
    }

    private static func query(_ items: [URLQueryItem]?) -> [String: String] {
        Dictionary((items ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { first, _ in first })
    }

    /// The hub as the confirm sheet names it, the way the hub menu does.
    var address: String { PairedHub.address(of: baseURL) }
}

extension PairLink: Identifiable {
    var id: String { "\(baseURL.absoluteString) \(token)" }
}

/// Turns what someone types ("100.64.1.2", "mac.tailnet.ts.net:8477",
/// "http://mac:8477") into the hub's base URL.
func hubURL(from input: String) -> URL? {
    let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    let withScheme = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
    guard var parts = URLComponents(string: withScheme), let host = parts.host, !host.isEmpty else { return nil }
    // An https address with no port is 443, `tailscale serve` in front of
    // the hub; only plain http means the hub's own port.
    if parts.port == nil, parts.scheme != "https" { parts.port = Hub.defaultPort }
    parts.path = ""
    parts.query = nil
    parts.fragment = nil
    return parts.url
}
