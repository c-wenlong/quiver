import Foundation
import Security

/// Where the hub is and the credential it issued this device. The
/// credential lives in the Keychain; the hub address in UserDefaults.
struct PairedHub: Equatable {
    var baseURL: URL
    var credential: Credential
}

enum PairingStore {
    private static let hubKey = "quiver.hub"
    /// The WKWebView shell kept the `/v1/pair` JSON here; read once so a
    /// phone paired under the old shell stays paired.
    private static let legacyCredentialKey = "quiver.credentialJSON"
    private static let service = "com.quiver.remote"
    private static let account = "device-credential"

    static func load() -> PairedHub? {
        let defaults = UserDefaults.standard
        guard let raw = defaults.string(forKey: hubKey), let url = URL(string: raw) else { return nil }
        if let data = keychainRead(), let cred = try? Hub.decoder.decode(Credential.self, from: data) {
            return PairedHub(baseURL: url, credential: cred)
        }
        if let legacy = defaults.string(forKey: legacyCredentialKey),
           let cred = try? Hub.decoder.decode(Credential.self, from: Data(legacy.utf8)) {
            let hub = PairedHub(baseURL: url, credential: cred)
            // Drop the old copy only once the Keychain holds the new one.
            if save(hub) { defaults.removeObject(forKey: legacyCredentialKey) }
            return hub
        }
        return nil
    }

    /// True once the credential is in the Keychain.
    @discardableResult
    static func save(_ hub: PairedHub) -> Bool {
        UserDefaults.standard.set(hub.baseURL.absoluteString, forKey: hubKey)
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        guard let data = try? encoder.encode(hub.credential) else { return false }
        return keychainWrite(data)
    }

    static func clear() {
        SecItemDelete(query() as CFDictionary)
    }

    /// Last hub address typed or paired, kept after an unpair so pairing
    /// again doesn't need it retyped.
    static var lastHost: String? { UserDefaults.standard.string(forKey: hubKey).flatMap { URL(string: $0)?.host } }

    private static func query() -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func keychainRead() -> Data? {
        var q = query()
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    private static func keychainWrite(_ data: Data) -> Bool {
        SecItemDelete(query() as CFDictionary)
        var q = query()
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess
    }
}

/// Parses what a pair QR or link carries:
/// `quiver://pair?host=…&port=…&token=…[&name=…]` or the browser form
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
            guard let base = URL(string: "http://\(host):\(port)") else { return nil }
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
