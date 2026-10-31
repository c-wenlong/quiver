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
        // A credential is never world-readable, even for an instant: the
        // temp file is born 0600, then renamed over the old one.
        let temp = url.appendingPathExtension("tmp-\(UUID().uuidString)")
        let fd = open(temp.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard fd >= 0 else {
            throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
        }
        let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        do {
            try handle.write(contentsOf: data)
            try handle.synchronize()
            try handle.close()
        } catch {
            try? manager.removeItem(at: temp)
            throw error
        }
        // rename(2), not moveItem: the old credential must be replaced,
        // and FileManager refuses to overwrite it.
        guard Darwin.rename(temp.path, url.path) == 0 else {
            let code = POSIXErrorCode(rawValue: errno) ?? .EIO
            try? manager.removeItem(at: temp)
            throw POSIXError(code)
        }
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

    /// The model of each listed session's newest reply, by hub key, from
    /// the same read as `keys`. A session whose model the hub doesn't know
    /// has no entry; an unknown or down hub leaves it empty.
    public private(set) var models: [String: String] = [:]

    /// The hub's pins, names and fork flags by hub key, from the same read
    /// as `keys`, with the pins chosen here since laid over it
    /// (`SessionHubMeta.swift`). Empty while the hub is down, unknown or
    /// unpaired: the Sessions rows then offer no pin, rename or fork.
    public internal(set) var overlay = HubSessionOverlay()
    /// Every row and machine the same read returned, satellites' too: the
    /// Sessions page's "Other machines" group (`OtherMachines`). Empty
    /// while the hub is down, unknown or unpaired, which hides the group.
    public private(set) var hubRows: [HubSessionRow] = []
    public private(set) var hubMachines: [HubMachine] = []
    /// Pins chosen here that a list read started too early must not undo.
    var pins = SessionPinLedger()
    /// Meta applied from `session.meta` events and answered calls, in
    /// order. A list read that left before one reached us would put back
    /// what it replaced, so `refreshKeys` lays the newer ones over its
    /// answer the way `pins` does for unconfirmed pin choices.
    var metaLog: [(at: Int, key: String, reply: HubMetaReply)] = []
    var metaClock = 0

    /// The hub's base once a client exists — scopes kept command lists,
    /// so a repointed hub never shows the last hub's answers.
    public var connectedHub: String? { current?.baseURL.absoluteString }

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
    /// The address only ever earns plain HTTP when it stays on this Mac
    /// or inside the tailnet: anywhere else the bearer credential would
    /// cross the wire readable.
    nonisolated static func baseURL(_ address: String) -> URL? {
        guard let colon = address.lastIndex(of: ":") else { return nil }
        var host = String(address[..<colon])
        let port = address[address.index(after: colon)...]
        guard !host.isEmpty, Int(port) != nil else { return nil }
        if host == "0.0.0.0" { host = "127.0.0.1" }
        if host == "::" { host = "::1" }
        guard localHubHost(host) else { return nil }
        if host.contains(":"), !host.hasPrefix("[") { host = "[\(host)]" }
        return URL(string: "http://\(host):\(port)")
    }

    /// Loopback or tailnet — the only places a plain-HTTP credential is
    /// safe to send. A hub bound to a LAN address is a misconfiguration
    /// this refuses rather than leaks over.
    nonisolated static func localHubHost(_ host: String) -> Bool {
        let host = host.trimmingCharacters(in: CharacterSet(charactersIn: "[]")).lowercased()
        if host == "localhost" || host.hasSuffix(".localhost") || host == "::1" { return true }
        if host.hasSuffix(".ts.net") { return true }
        // A numeric host has to be a real dotted quad — "127.evil.example"
        // carries a loopback-looking prefix but resolves wherever DNS
        // points, and "0127.0.0.1" parses octal under inet_aton. Every
        // octet, in canonical decimal.
        let octets = host.split(separator: ".")
        guard octets.count == 4, octets.allSatisfy({ part in
            guard let value = Int(part), (0...255).contains(value) else { return false }
            return String(value) == String(part)
        }) else { return false }
        let quad = octets.compactMap { Int($0) }
        return quad[0] == 127 || (quad[0] == 100 && (64...127).contains(quad[1]))
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
        guard let address = try await locator.hubAddress() else {
            keys = .unavailable
            throw HubError(status: 0, code: "hub_down",
                           message: "This Mac's hub isn't running. Start it in Settings › Remote.")
        }
        guard let base = Self.baseURL(address) else {
            keys = .unavailable
            throw HubError(status: 0, code: "hub_blocked",
                           message: "The hub at \(address) is running, but not on this Mac or the tailnet — sending its credential there would go over plain HTTP. Bind the hub to 127.0.0.1 or a Tailscale address.")
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
                forgetRows()
                return
            }
            guard let saved = store.credential(for: address) else {
                keys = .unknown
                forgetRows()
                return
            }
            client = current ?? makeClient(base, saved)
            current = client
        }
        // Pins chosen after this read leaves are newer than its answer.
        let mark = pins.mark
        let metaMark = metaClock
        do {
            let (rows, machines) = try await client.sessionsAndMachines()
            hubRows = rows
            hubMachines = machines
            keys = .known(Set(rows.map(\.key)))
            models = Dictionary(rows.compactMap { row in row.model.map { (row.key, $0) } },
                                uniquingKeysWith: { _, newest in newest })
            var read = HubSessionOverlay(rows)
            pins.overlay(&read, fetchedFrom: mark)
            // A meta applied after this read left (a `session.meta` or an
            // answered rename or pin) is newer than its answer too.
            for entry in metaLog where entry.at > metaMark {
                let held = pins.held(entry.key)
                read.update(entry.key) { meta in
                    meta.apply(entry.reply)
                    if let held {
                        meta.pinned = held.pinned
                        meta.pinnedAt = held.pinnedAt
                    }
                }
            }
            metaLog.removeAll { $0.at <= metaMark }
            overlay = read
        } catch let error as HubError where error.isUnauthorized {
            // Only this client's credential: another call may have paired
            // again meanwhile.
            refused(client)
            keys = .unknown
            forgetRows()
        } catch let error as HubError where error.code == "network" {
            // A hub that stopped since: forget the address with the client.
            if current?.credential == client.credential { current = nil }
            keys = .unavailable
            forgetRows()
        } catch is CancellationError {
            // The page that asked went away: the hub didn't change.
            return
        } catch {
            keys = .unknown
            forgetRows()
        }
    }

    /// What the last read of the hub's rows said, gone with the hub.
    private func forgetRows() {
        models = [:]
        overlay = HubSessionOverlay()
        hubRows = []
        hubMachines = []
    }

    /// The model of session `key`'s newest reply, as the hub reported it
    /// on the last `refreshKeys`; nil when it didn't.
    public func model(for key: String) -> String? { models[key] }

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
