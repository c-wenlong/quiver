import Foundation
import Observation

/// What the Sessions chat needs from swe: where this Mac's hub answers,
/// and a one-time pair token it honours.
public protocol HubLocating: Sendable {
    /// `host:port` the hub listens on, or nil when it isn't running.
    func hubAddress() async throws -> String?
    func pairToken(name: String) async throws -> String
}

extension SweClient: HubLocating {
    public func hubAddress() async throws -> String? {
        let status = try await remoteStatus()
        return status.hub.ok ? status.hubAddr : nil
    }

    public func pairToken(name: String) async throws -> String {
        try await remotePair(name: name).token
    }
}

/// The app's own device credential for this Mac's hub, in
/// `~/.quiver/secrets/remote/app-device.json` (0600). It names the hub
/// address it was paired with, so a hub that moved pairs again.
public struct HubDeviceStore: Sendable {
    public let url: URL

    public init(url: URL) { self.url = url }

    public static func defaultURL(home: URL) -> URL {
        APIKeysStore.secretsFolder(home: home).appending(path: "remote/app-device.json")
    }

    struct Saved: Codable {
        var hub: String
        var deviceId: String
        var credential: String
    }

    public func credential(for hub: String) -> String? {
        guard let data = try? Data(contentsOf: url),
              let saved = try? JSONDecoder().decode(Saved.self, from: data),
              saved.hub == hub, !saved.credential.isEmpty else { return nil }
        return saved.credential
    }

    public func save(_ credential: HubCredential, for hub: String) throws {
        let manager = FileManager.default
        try manager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                    attributes: [.posixPermissions: 0o700])
        let data = try JSONEncoder().encode(Saved(hub: hub, deviceId: credential.deviceId,
                                                  credential: credential.credential))
        try data.write(to: url, options: .atomic)
        try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    public func clear() {
        try? FileManager.default.removeItem(at: url)
    }
}

/// This Mac's hub, as the Sessions page talks to it: finds the address
/// through swe, pairs the app once (it then lists as a device in
/// Settings › Remote), and knows which sessions the hub can open.
@MainActor
@Observable
public final class LocalHub {
    /// Which sessions a click opens as a chat.
    public enum Keys: Equatable, Sendable {
        /// Not asked yet, or the app isn't paired: the chat will find out.
        case unknown
        /// The hub isn't running: rows open their details instead.
        case unavailable
        case known(Set<String>)
    }

    public private(set) var keys: Keys = .unknown

    /// How the app names itself in the hub's device list.
    public static let deviceName = "Quiver for Mac"

    private let locator: any HubLocating
    private let store: HubDeviceStore
    private let makeClient: @Sendable (URL, String?) -> HubClient
    private var current: HubClient?
    private var connecting: Task<HubClient, Error>?

    public init(locator: any HubLocating, store: HubDeviceStore,
                makeClient: @escaping @Sendable (URL, String?) -> HubClient = { HubClient(baseURL: $0, credential: $1) }) {
        self.locator = locator
        self.store = store
        self.makeClient = makeClient
    }

    /// `http://host:port` for swe's `host:port`, brackets around IPv6.
    nonisolated static func baseURL(_ address: String) -> URL? {
        guard let colon = address.lastIndex(of: ":") else { return nil }
        var host = String(address[..<colon])
        let port = address[address.index(after: colon)...]
        guard !host.isEmpty, Int(port) != nil else { return nil }
        if host == "0.0.0.0" { host = "127.0.0.1" }
        if host == "::" { host = "::1" }
        if host.contains(":"), !host.hasPrefix("[") { host = "[\(host)]" }
        return URL(string: "http://\(host):\(port)")
    }

    /// A client holding a credential, pairing first when there is none.
    /// Concurrent callers share one pairing.
    public func client() async throws -> HubClient {
        if let current { return current }
        if let connecting { return try await connecting.value }
        let task = Task { try await self.connect() }
        connecting = task
        defer { connecting = nil }
        let client = try await task.value
        current = client
        return client
    }

    private func connect() async throws -> HubClient {
        guard let address = try await locator.hubAddress(), let base = Self.baseURL(address) else {
            keys = .unavailable
            throw HubError(status: 0, code: "hub_down",
                           message: "This Mac's hub isn't running. Start it in Settings › Remote.")
        }
        if let saved = store.credential(for: address) { return makeClient(base, saved) }
        let token = try await locator.pairToken(name: Self.deviceName)
        let credential = try await makeClient(base, nil).pair(token: token, name: Self.deviceName)
        try store.save(credential, for: address)
        return makeClient(base, credential.credential)
    }

    /// The hub refused this client's credential (revoked in Settings ›
    /// Remote, or the hub's state was reset): forget it, so the next
    /// `client()` pairs again.
    public func refused(_ client: HubClient) {
        guard current?.credential == client.credential else { return }
        current = nil
        store.clear()
    }

    /// Runs `call` with a client, pairing again once if the hub no longer
    /// knows the credential.
    public func withClient<T: Sendable>(_ call: (HubClient) async throws -> T) async throws -> T {
        let client = try await client()
        do {
            return try await call(client)
        } catch let error as HubError where error.isUnauthorized {
            refused(client)
            return try await call(try await self.client())
        }
    }

    /// Asks the hub which sessions it lists. Never pairs: until the app
    /// is paired the keys stay unknown and a click opens the chat, which
    /// pairs.
    public func refreshKeys() async {
        let client: HubClient
        if let current {
            client = current
        } else {
            guard let address = try? await locator.hubAddress(), let base = Self.baseURL(address) else {
                keys = .unavailable
                return
            }
            guard let saved = store.credential(for: address) else {
                keys = .unknown
                return
            }
            client = current ?? makeClient(base, saved)
            current = client
        }
        do {
            keys = .known(Set(try await client.sessions().map(\.key)))
        } catch let error as HubError where error.isUnauthorized {
            // Only this client's credential: another call may have paired
            // again meanwhile.
            refused(client)
            keys = .unknown
        } catch let error as HubError where error.code == "network" {
            // A hub that stopped since: forget the address with the client.
            if current?.credential == client.credential { current = nil }
            keys = .unavailable
        } catch {
            keys = .unknown
        }
    }

    /// Whether a click on this session opens the chat: always while the
    /// hub's list is unknown, never while the hub is down.
    public func opensChat(_ key: String) -> Bool {
        switch keys {
        case .unknown: true
        case .unavailable: false
        case .known(let known): known.contains(key)
        }
    }
}
