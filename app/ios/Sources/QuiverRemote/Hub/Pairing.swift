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
