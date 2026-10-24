import Foundation
import Security

/// Where a hub is and the credential it issued this device.
struct PairedHub: Equatable, Identifiable {
    var baseURL: URL
    var credential: Credential

    var id: String { baseURL.absoluteString }
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
        names[hub.id] ?? hub.baseURL.host ?? hub.id
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

/// Keeps the list in memory only, for tests and previews.
final class MemoryHubStore: HubStore {
    private(set) var list: HubList

    init(_ list: HubList = HubList()) { self.list = list }

    func load() -> HubList { list }
    func saveIndex(_ list: HubList) { self.list = list }
    func saveCredential(_ hub: PairedHub) -> Bool { true }
    func deleteCredential(_ id: String) {}
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
    private static let service = "com.quiver.remote"

    private struct IndexEntry: Codable {
        let url: String
        var name: String?
    }

    func load() -> HubList {
        let defaults = UserDefaults.standard
        guard let data = defaults.data(forKey: Self.hubsKey),
              let entries = try? JSONDecoder().decode([IndexEntry].self, from: data) else {
            return migrate()
        }
        var list = HubList()
        for entry in entries {
            guard let url = URL(string: entry.url),
                  let raw = Self.keychainRead(account: Self.account(for: entry.url)),
                  let cred = try? Hub.decoder.decode(Credential.self, from: raw) else { continue }
            list.hubs.append(PairedHub(baseURL: url, credential: cred))
            if let name = entry.name { list.names[entry.url] = name }
        }
        list.activeId = defaults.string(forKey: Self.activeKey)
        if list.active?.id != list.activeId { list.activeId = list.active?.id }
        return list
    }

    func saveIndex(_ list: HubList) {
        let defaults = UserDefaults.standard
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
        return Self.keychainWrite(data, account: Self.account(for: hub.id))
    }

    func deleteCredential(_ id: String) {
        SecItemDelete(Self.query(account: Self.account(for: id)) as CFDictionary)
    }

    /// Last hub address paired, kept after an unpair so pairing again
    /// doesn't need it retyped.
    static var lastHost: String? {
        let defaults = UserDefaults.standard
        return defaults.string(forKey: lastHostKey)
            ?? defaults.string(forKey: legacyHubKey).flatMap { URL(string: $0)?.host }
    }

    /// The one hub a phone paired before this list existed, from its old
    /// Keychain item (or the WKWebView shell's copy), moved into the list.
    /// The old copies go only once the new ones are stored.
    private func migrate() -> HubList {
        let defaults = UserDefaults.standard
        guard let raw = defaults.string(forKey: Self.legacyHubKey), let url = URL(string: raw) else { return HubList() }
        let cred: Credential
        if let data = Self.keychainRead(account: Self.legacyAccount),
           let c = try? Hub.decoder.decode(Credential.self, from: data) {
            cred = c
        } else if let legacy = defaults.string(forKey: Self.legacyCredentialKey),
                  let c = try? Hub.decoder.decode(Credential.self, from: Data(legacy.utf8)) {
            cred = c
        } else {
            return HubList()
        }
        var list = HubList()
        list.adopt(PairedHub(baseURL: url, credential: cred))
        guard saveCredential(list.hubs[0]) else { return list }
        saveIndex(list)
        SecItemDelete(Self.query(account: Self.legacyAccount) as CFDictionary)
        defaults.removeObject(forKey: Self.legacyCredentialKey)
        defaults.removeObject(forKey: Self.legacyHubKey)
        return list
    }

    private static func account(for id: String) -> String { "device-credential \(id)" }

    private static func query(account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func keychainRead(account: String) -> Data? {
        var q = query(account: account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    private static func keychainWrite(_ data: Data, account: String) -> Bool {
        SecItemDelete(query(account: account) as CFDictionary)
        var q = query(account: account)
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }
}

/// Parses what a pair QR or link carries:
/// `quiver://pair?host=…&port=…&token=…[&scheme=https][&name=…]` or the browser form
/// `http://host:port/#pair=<token>[&name=…]`.
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
            guard let base = URL(string: "\(url.scheme!)://\(host):\(url.port ?? Hub.defaultPort)") else { return nil }
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
}

/// Turns what someone types ("100.64.1.2", "mac.tailnet.ts.net:8477",
/// "http://mac:8477") into the hub's base URL.
func hubURL(from input: String) -> URL? {
    let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }
    let withScheme = trimmed.contains("://") ? trimmed : "http://\(trimmed)"
    guard var parts = URLComponents(string: withScheme), let host = parts.host, !host.isEmpty else { return nil }
    if parts.port == nil { parts.port = Hub.defaultPort }
    parts.path = ""
    parts.query = nil
    parts.fragment = nil
    return parts.url
}
