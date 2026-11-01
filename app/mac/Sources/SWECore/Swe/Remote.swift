import Foundation
import Observation

/// `swe remote status --json` — hub reachability plus the paired-device
/// and machine registries the Settings card renders.
public struct RemoteStatus: Decodable, Sendable, Equatable {
    public let hub: Hub
    public let hubAddr: String?
    public let machines: [Machine]
    public let devices: [Device]

    public struct Hub: Decodable, Sendable, Equatable {
        public let ok: Bool
        public let version: String?
        public let uptimeS: Double?
        public let machine: Machine?
    }

    public struct Machine: Decodable, Sendable, Equatable, Identifiable {
        public let id: String
        public let name: String?
        public let os: String?
        public let online: Bool?
        /// Epoch seconds on the wire; nil when a machine has never been
        /// seen or an older build omits it.
        public let lastSeenAt: Date?
        public let harnesses: [String]?
    }

    public struct Device: Decodable, Sendable, Equatable, Identifiable {
        public let id: String
        public let name: String?
        /// The user's own rename — beats `name` wherever the device is
        /// listed. Set through `/v1/devices/rename`.
        public let displayName: String?
        /// `iphone`/`ipad`/`android`/`mac`/`windows`/`linux`/`browser` —
        /// picks the row's icon. Absent from hubs older than the
        /// metadata change, so everything below stays optional.
        public let kind: String?
        /// Hardware model where the client knows it (the iOS wrapper
        /// reports it; browser UAs can't).
        public let model: String?
        /// Client name+version, e.g. "SWE Remote 1.0" or "Safari 18.5".
        public let client: String?
        public let os: String?
        /// Tailnet node the pairing request came from — the closest
        /// thing to an origin CGNAT offers.
        public let nodeName: String?
        public let createdAt: Date?
        public let lastSeenAt: Date?
        public let revoked: Bool

        public init(id: String, name: String? = nil, displayName: String? = nil,
                    kind: String? = nil, model: String? = nil, client: String? = nil,
                    os: String? = nil, nodeName: String? = nil,
                    createdAt: Date? = nil, lastSeenAt: Date? = nil,
                    revoked: Bool = false) {
            self.id = id
            self.name = name
            self.displayName = displayName
            self.kind = kind
            self.model = model
            self.client = client
            self.os = os
            self.nodeName = nodeName
            self.createdAt = createdAt
            self.lastSeenAt = lastSeenAt
            self.revoked = revoked
        }
    }
}

/// `swe remote pair --json` — one minted token rendered two ways.
public struct PairTicket: Decodable, Sendable, Equatable {
    public let host: String
    public let port: Int
    public let token: String
    public let name: String?
    /// `http://host:port/#pair=token` — opens the web app in a phone browser.
    public let url: String
    /// `swe://pair?…` — opens the iOS app.
    public let deepLink: String
    /// Seconds until the hub stops honouring this token.
    public let expiresInS: Int

    public init(host: String, port: Int, token: String, name: String?,
                url: String, deepLink: String, expiresInS: Int) {
        self.host = host
        self.port = port
        self.token = token
        self.name = name
        self.url = url
        self.deepLink = deepLink
        self.expiresInS = expiresInS
    }
}

/// `swe remote devices --revoke=<id> --json`.
public struct RemoteRevokeResult: Decodable, Sendable, Equatable {
    public let revoked: String
    public let ok: Bool
}

extension SweClient {
    /// `status` exits 1 when the hub is down but still emits the envelope,
    /// so a non-zero status with a JSON body is a report, not a failure.
    public func remoteStatus() async throws -> RemoteStatus {
        try await call(["remote", "status", "--json"], allowFailure: true)
    }

    /// Mints a one-time pair token the running hub honours for a few
    /// minutes; both the web-app URL and the `swe://` deep link come back.
    /// `name` is what the device is called once it trades the token.
    public func remotePair(name: String? = nil) async throws -> PairTicket {
        try await call(["remote", "pair"] + (name.map { ["--name=\($0)"] } ?? []) + ["--json"])
    }

    @discardableResult
    public func remoteRevoke(_ deviceID: String) async throws -> Bool {
        let result: RemoteRevokeResult = try await call(
            ["remote", "devices", "--revoke=\(deviceID)", "--json"])
        return result.ok
    }
}

/// Settings › Integrations › Remote: hub reachability, paired devices, and the pair
/// sheet's ticket state. One load at a time; a reload mid-flight replaces
/// the earlier answer via `generation`.
@MainActor
@Observable
public final class RemoteModel {
    public private(set) var status: Loadable<RemoteStatus> = .idle
    /// The ticket the pair sheet is showing, if any.
    public internal(set) var ticket: PairTicket?
    /// When the ticket was minted — the expiry countdown counts from here.
    public internal(set) var ticketMintedAt: Date?
    public internal(set) var pairing = false
    public internal(set) var pairError: String?
    /// Device ids with a revoke in flight, and the last failure per id.
    public internal(set) var revoking: Set<String> = []
    public internal(set) var revokeErrors: [String: String] = [:]
    private let swe: SweClient
    private var generation = 0
    /// Bumped by each mint and by dismissing the sheet: only the newest
    /// mint's reply lands.
    private var mintGeneration = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        generation += 1
        let mine = generation
        // A cancelled load puts this back rather than spinning for good.
        let before = status == .loading ? .idle : status
        status = .loading
        let next: Loadable<RemoteStatus>
        do {
            next = .loaded(try await swe.remoteStatus())
        } catch is CancellationError {
            if mine == generation { status = before }
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == generation { status = next }
    }

    /// Mint a fresh token for the pair sheet. A stale mint can't land on
    /// top of a newer sheet.
    public func mintTicket() async {
        mintGeneration += 1
        let mine = mintGeneration
        pairing = true
        pairError = nil
        defer { if mine == mintGeneration { pairing = false } }
        do {
            let minted = try await swe.remotePair()
            // A newer mint, or the sheet closing, overtook this one.
            guard mine == mintGeneration else { return }
            ticket = minted
            ticketMintedAt = Date()
        } catch is CancellationError {
        } catch {
            if mine == mintGeneration { pairError = error.localizedDescription }
        }
    }

    public func dismissTicket() {
        mintGeneration += 1
        pairing = false
        ticket = nil
        ticketMintedAt = nil
        pairError = nil
    }

    /// Seconds of validity left at `now`; nil with no live ticket.
    public func ticketSecondsLeft(at now: Date) -> Int? {
        guard let ticket, let minted = ticketMintedAt else { return nil }
        let left = ticket.expiresInS - Int(now.timeIntervalSince(minted))
        return max(0, left)
    }

    public func revoke(_ device: RemoteStatus.Device) async {
        guard !revoking.contains(device.id) else { return }
        revoking.insert(device.id)
        revokeErrors[device.id] = nil
        defer { revoking.remove(device.id) }
        do {
            if try await swe.remoteRevoke(device.id) {
                // A revoked row stops being a device you can steer from;
                // drop the sheet's ticket if the phone it was for went away.
                await load()
            } else {
                revokeErrors[device.id] = "swe did not find that device"
            }
        } catch {
            revokeErrors[device.id] = error.localizedDescription
        }
    }
}
