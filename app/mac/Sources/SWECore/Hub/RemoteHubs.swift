import CryptoKit
import Foundation
import Observation

// The sidebar's machine switcher (#681): "This Mac" shows the native pages;
// another machine's hub shows that hub's web app (served at `/`, #943) in a
// web view inside the window. The hub lets the owner's own tailnet devices
// in without pairing (#653), so the web view is signed in the same way a
// browser on this Mac is; any other device gets the web app's own pairing
// card, whose credential stays in that hub's web data store.

/// A hub's origin: scheme, host and port, nothing else. Everything the web
/// view may load lives under it.
public struct HubOrigin: Hashable, Sendable, Identifiable {
    public let scheme: String
    /// Lowercased; an IPv6 literal without brackets.
    public let host: String
    /// Nil for the scheme's default port.
    public let port: Int?

    /// "box.tailnet.ts.net:8477", "box.tailnet.ts.net:8477/app/" or a
    /// whole `#pair=` link: only the origin is kept. A bare host means
    /// https, which `tailscale serve` provides. Nil for anything that isn't
    /// an http(s) URL with a host, or that carries a user name or password.
    public init?(_ text: String) {
        var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "https://" + text }
        guard let parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil else { return nil }
        self.init(scheme: scheme, host: host, port: parts.port)
    }

    /// The origin `url` belongs to, or nil when it has none (about:,
    /// file:, data:, ...).
    public init?(of url: URL) {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host(), !host.isEmpty else { return nil }
        self.init(scheme: scheme, host: host, port: url.port)
    }

    private init(scheme: String, host: String, port: Int?) {
        self.scheme = scheme
        var host = host.lowercased()
        if host.hasPrefix("["), host.hasSuffix("]") { host = String(host.dropFirst().dropLast()) }
        self.host = host
        let standard = scheme == "https" ? 443 : 80
        self.port = port == standard ? nil : port
    }

    /// "https://box.tailnet.ts.net:8477".
    public var string: String {
        let name = host.contains(":") ? "[\(host)]" : host
        return "\(scheme)://\(name)" + (port.map { ":\($0)" } ?? "")
    }

    public var id: String { string }

    /// The web app's address: the hub's root.
    public var url: URL { URL(string: string + "/")! }

    /// "box.tailnet.ts.net:8477": what the window shows it is looking at.
    public var address: String {
        let name = host.contains(":") ? "[\(host)]" : host
        return name + (port.map { ":\($0)" } ?? "")
    }

    /// "box" for a MagicDNS name; an IP address stays whole.
    public var name: String {
        let isIP = host.contains(":") || host.split(separator: ".").allSatisfy { Int($0) != nil }
        guard !isIP, let first = host.split(separator: ".").first else { return host }
        return String(first)
    }

    /// Whether `url` is on this hub: same scheme, host and port.
    public func contains(_ url: URL) -> Bool { HubOrigin(of: url) == self }

    /// The web view's data store for this hub: the same UUID every launch,
    /// so a hub's cookies and pairing credential outlive a restart, and no
    /// two hubs share one.
    public var dataStoreID: UUID {
        var bytes = Array(SHA256.hash(data: Data("quiver.hub:\(string)".utf8)).prefix(16))
        bytes[6] = (bytes[6] & 0x0F) | 0x50  // version 5 shape (name-based)
        bytes[8] = (bytes[8] & 0x3F) | 0x80  // RFC 4122 variant
        return UUID(uuid: (bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
                           bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]))
    }
}

/// What the hub's web view does with a navigation.
public enum HubNavigation: Equatable, Sendable {
    /// Load it in the web view.
    case allow
    /// Hand it to the default browser (or mail app) and leave the view.
    case openExternally
    /// Drop it.
    case cancel

    /// Only the hub's own origin loads inside the view. A link the user
    /// clicked (or a new-window request, which WebKit only allows from a
    /// click) to another http(s) site or a mailto: goes to the default
    /// app; anything else off-origin is dropped, so a redirect or script
    /// can't move the view elsewhere or open a browser on its own.
    /// `about:blank` / `about:srcdoc` (the chat's file previews) and the
    /// hub's own `blob:` URLs stay. `file:` and every other scheme never
    /// load.
    public static func decide(_ url: URL?, hub: HubOrigin, userInitiated: Bool) -> HubNavigation {
        guard let url, let scheme = url.scheme?.lowercased() else { return .cancel }
        switch scheme {
        case "http", "https":
            if hub.contains(url) { return .allow }
            return userInitiated ? .openExternally : .cancel
        case "about":
            return ["about:blank", "about:srcdoc"].contains(url.absoluteString.lowercased()) ? .allow : .cancel
        case "blob":
            // "blob:https://box:8477/<uuid>": the part after "blob:" names
            // the origin that made it.
            let inner = URL(string: String(url.absoluteString.dropFirst("blob:".count)))
            return inner.map(hub.contains) == true ? .allow : .cancel
        case "mailto":
            return userInitiated ? .openExternally : .cancel
        default:
            return .cancel
        }
    }
}

/// One hub the switcher lists.
public struct RemoteHub: Hashable, Sendable, Identifiable {
    public enum Source: Sendable {
        /// The hub this Mac's `swe remote agent` reports to (`agent.json`).
        case agent
        /// Added by hand in Settings › Integrations › Remote.
        case added
    }

    public let origin: HubOrigin
    public let source: Source

    public var id: String { origin.id }

    public init(origin: HubOrigin, source: Source) {
        self.origin = origin
        self.source = source
    }
}

/// The machine switcher's state: the hubs this Mac knows and which machine
/// the window shows. Kept in the app's preferences: the hubs added by hand,
/// the agent's hub as swe last reported it (so a restart shows the same
/// list before swe answers), and the picked hub.
@MainActor
@Observable
public final class MachineSwitcher {
    public static let addedKey = "remoteHubs"
    public static let agentKey = "remoteHubAgent"
    public static let selectedKey = "remoteHubSelected"

    /// The agent's hub first, then the added ones in the order added.
    public private(set) var hubs: [RemoteHub] = []
    /// The hub on screen; nil for This Mac.
    public private(set) var selected: HubOrigin?

    @ObservationIgnored private let store: KeyValueStoring
    @ObservationIgnored private var added: [HubOrigin]
    @ObservationIgnored private var agent: HubOrigin?

    public init(store: KeyValueStoring) {
        self.store = store
        added = Self.unique((store.strings(forKey: Self.addedKey) ?? []).compactMap(HubOrigin.init))
        agent = store.strings(forKey: Self.agentKey)?.first.flatMap(HubOrigin.init)
        rebuild()
        // A hub that is no longer listed falls back to This Mac.
        let saved = store.strings(forKey: Self.selectedKey)?.first.flatMap(HubOrigin.init)
        selected = saved.flatMap { saved in hubs.contains { $0.origin == saved } ? saved : nil }
    }

    /// True while a hub's web app fills the window.
    public var showsHub: Bool { selected != nil }

    public func select(_ origin: HubOrigin?) {
        guard origin != selected else { return }
        if let origin, !hubs.contains(where: { $0.origin == origin }) { return }
        selected = origin
        store.set(origin.map { [$0.string] } ?? [], forKey: Self.selectedKey)
    }

    public func showThisMac() { select(nil) }

    /// Adds a hub typed in Settings; answers why it couldn't, else nil.
    @discardableResult
    public func add(_ text: String) -> String? {
        guard let origin = HubOrigin(text) else {
            return "Enter the hub's address, like https://box.tailnet.ts.net:8477."
        }
        guard !hubs.contains(where: { $0.origin == origin }) else { return "\(origin.address) is already listed." }
        added.append(origin)
        store.set(added.map(\.string), forKey: Self.addedKey)
        rebuild()
        return nil
    }

    /// Removes a hub added by hand; the agent's hub stays while the agent
    /// is paired with it.
    public func remove(_ origin: HubOrigin) {
        guard added.contains(origin) else { return }
        added.removeAll { $0 == origin }
        store.set(added.map(\.string), forKey: Self.addedKey)
        rebuild()
    }

    /// What `swe remote agent --status` reports: the hub `agent.json`
    /// names, or nil once swe says this Mac isn't a satellite.
    public func setAgentHub(_ text: String?) {
        let next = text.flatMap(HubOrigin.init)
        guard next != agent else { return }
        agent = next
        store.set(next.map { [$0.string] } ?? [], forKey: Self.agentKey)
        rebuild()
    }

    private func rebuild() {
        var out: [RemoteHub] = []
        if let agent { out.append(RemoteHub(origin: agent, source: .agent)) }
        for origin in added where origin != agent { out.append(RemoteHub(origin: origin, source: .added)) }
        hubs = out
        if let selected, !out.contains(where: { $0.origin == selected }) {
            self.selected = nil
            store.set([], forKey: Self.selectedKey)
        }
    }

    private static func unique(_ origins: [HubOrigin]) -> [HubOrigin] {
        var seen = Set<HubOrigin>()
        return origins.filter { seen.insert($0).inserted }
    }
}
