import Foundation

/// Wire types for the hub's `/v1/sessions` routes, as the Sessions chat
/// reads them. Field names match `src/quiver/remote/protocol.py` and
/// `catalog.py`; the iPhone app's `Models.swift` (app/ios) holds the
/// same shapes. Every field the hub may omit is optional, so a newer hub
/// never breaks decoding.
public enum HubWire {
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    /// Plain keys: only re-serializes `HubJSON` trees, whose keys are
    /// already the hub's snake_case.
    static let encoder = JSONEncoder()
}

/// One row of `GET /v1/sessions`.
public struct HubSessionRow: Codable, Sendable, Equatable {
    public var key: String
    public var harness: String?
    public var agent: String?
    public var cwd: String?
    public var title: String?
    public var status: String?
    public var machineId: String?
    /// `attached` (runs under Quiver: writable) or `observed` (its
    /// terminal owns it: read-only here).
    public var ownership: String?
    /// The session has resume flags, so Attach can take it over.
    public var attachable: Bool?
    /// A satellite's session, which the hub forwards to that machine.
    public var remote: Bool?

    public init(key: String, harness: String? = nil, agent: String? = nil, cwd: String? = nil,
                title: String? = nil, status: String? = nil, machineId: String? = nil,
                ownership: String? = nil, attachable: Bool? = nil, remote: Bool? = nil) {
        self.key = key
        self.harness = harness
        self.agent = agent
        self.cwd = cwd
        self.title = title
        self.status = status
        self.machineId = machineId
        self.ownership = ownership
        self.attachable = attachable
        self.remote = remote
    }

    public var isAttached: Bool { ownership == "attached" }

    /// The hub's name for a session the Mac lists: `harness:id`, as
    /// `protocol.session_key` builds it.
    public static func key(harness: String, id: String) -> String { "\(harness):\(id)" }
}

struct HubSessionsResponse: Decodable {
    let sessions: [HubSessionRow]
}

/// `GET /v1/inbox`: sessions waiting on someone, with the approval's
/// request id when that's what they wait on.
struct HubInboxResponse: Decodable {
    struct Item: Decodable { let key: String; let status: String?; let requestId: String? }
    let items: [Item]
}

public struct HubToolCall: Codable, Sendable, Equatable {
    public var name: String
    public var callId: String?
    public var argsSummary: String?
    public var argsFull: String?

    public init(name: String, callId: String? = nil, argsSummary: String? = nil, argsFull: String? = nil) {
        self.name = name
        self.callId = callId
        self.argsSummary = argsSummary
        self.argsFull = argsFull
    }
}

/// `data` of a `message.appended` event.
public struct HubMessage: Codable, Sendable, Equatable {
    public var kind: String?
    public var text: String?
    public var ts: Double?
    public var tool: HubToolCall?
    public var resultRef: String?

    public init(kind: String?, text: String? = nil, ts: Double? = nil, tool: HubToolCall? = nil,
                resultRef: String? = nil) {
        self.kind = kind
        self.text = text
        self.ts = ts
        self.tool = tool
        self.resultRef = resultRef
    }
}

/// The hub's event envelope. `data` stays raw because its shape depends
/// on `type`; `decode(_:)` reads it as the type that fits.
public struct HubEnvelope: Codable, Sendable, Equatable {
    public var type: String
    public var session: String?
    public var seq: Int?
    public var data: HubJSON?

    public init(type: String, session: String? = nil, seq: Int? = nil, data: HubJSON? = nil) {
        self.type = type
        self.session = session
        self.seq = seq
        self.data = data
    }

    func decode<T: Decodable>(_: T.Type) -> T? {
        guard let data, let raw = try? HubWire.encoder.encode(data) else { return nil }
        return try? HubWire.decoder.decode(T.self, from: raw)
    }
}

/// `GET /v1/sessions/<key>/messages`: the newest page, or with `before`
/// the page just older than that seq.
public struct HubMessagesPage: Decodable, Sendable, Equatable {
    public var messages: [HubEnvelope]
    public var nextBefore: Int?
    public var hasMore: Bool?
}

struct HubStatusData: Decodable { let status: String? }
struct HubOwnershipData: Decodable { let ownership: String? }
struct HubApprovalRequested: Decodable { let requestId: String?; let prompt: String? }
struct HubApprovalResolved: Decodable { let requestId: String?; let decision: String? }
/// What a command came to, after the hub (or a satellite) ran it: a
/// send, a stop, an attach or an approval.
struct HubCommandResult: Decodable {
    let op: String?
    let ok: Bool?
    let note: String?
    let degraded: Bool?
    let clientId: String?
    let requestId: String?
    /// A queued send removed before it went out: `ok` is false, but
    /// nothing failed.
    var cancelled: Bool? = nil
}
/// `POST .../message` answers 202 with this; `waiting` says the hub
/// holds the send until the agent's turn ends.
struct HubSendAccepted: Decodable { let waiting: Bool? }

/// A launch the hub accepted (`202` from `POST /v1/issues/resolve`) and
/// what `GET /v1/launches/<id>` says of it since: `pending` while the
/// hub works (`note` names the step), then `started` once its catalog
/// lists `key`, `failed` with the reason in `note`, or `untracked` for a
/// harness whose sessions the hub can't read.
public struct HubLaunchStatus: Decodable, Sendable, Equatable {
    public var launchId: String
    public var state: String?
    public var key: String?
    public var note: String?
    /// The launch waits for a satellite that is offline.
    public var queued: Bool?

    public init(launchId: String, state: String? = nil, key: String? = nil,
                note: String? = nil, queued: Bool? = nil) {
        self.launchId = launchId
        self.state = state
        self.key = key
        self.note = note
        self.queued = queued
    }
}

/// One row of `GET /v1/machines`: the hub machine and its satellites.
public struct HubMachine: Decodable, Sendable, Equatable, Identifiable {
    public var id: String
    public var name: String?
    public var online: Bool?
    /// Every harness swe detected there.
    public var harnesses: [String]?
    /// What a launch there accepts, in Provider visibility order; nil
    /// from a hub older than launches.
    public var launchable: [String]?

    public init(id: String, name: String? = nil, online: Bool? = nil,
                harnesses: [String]? = nil, launchable: [String]? = nil) {
        self.id = id
        self.name = name
        self.online = online
        self.harnesses = harnesses
        self.launchable = launchable
    }
}

struct HubMachinesResponse: Decodable {
    let machines: [HubMachine]
}

/// `POST /v1/pair`: a pair token traded for this app's device credential.
public struct HubCredential: Codable, Sendable, Equatable {
    public var deviceId: String
    public var credential: String
}

struct HubErrorBody: Decodable {
    struct Inner: Decodable { let code: String?; let message: String? }
    let error: Inner
}

/// A failed hub call, worded for the person reading the chat.
public struct HubError: LocalizedError, Equatable, Sendable {
    public var status: Int
    public var code: String
    public var message: String

    public init(status: Int, code: String, message: String) {
        self.status = status
        self.code = code
        self.message = message
    }

    public var errorDescription: String? { message }
    public var isUnauthorized: Bool { status == 401 }

    static let unreachable = HubError(status: 0, code: "network",
                                      message: "Can't reach this Mac's hub. Check Settings › Remote.")

    /// A non-2xx answer as the error the chat shows.
    static func from(status: Int, body: Data) -> HubError {
        let parsed = try? HubWire.decoder.decode(HubErrorBody.self, from: body)
        let code = parsed?.error.code ?? "http_\(status)"
        return HubError(status: status, code: code,
                        message: friendly(code: code, fallback: parsed?.error.message, status: status))
    }

    static func friendly(code: String, fallback: String?, status: Int) -> String {
        switch code {
        case "unauthorized": "The hub no longer knows this app. Open the session again to reconnect."
        case "unknown_session": "The hub doesn't list this session."
        case "pair_failed": "The hub refused the pairing token. Try again."
        case "not_attached": "This session is read-only until you attach it."
        case "not_idle": "The session is mid-turn. Attach once it's idle."
        case "not_attachable": "This harness can't be attached."
        case "backlog_full": "Too many messages are waiting. Try again shortly."
        case "duplicate_client_id": "That message is already queued."
        // Resolve-an-issue refusals: the hub's own words name the repo or
        // harness, so they win as sent (a repo name keeps its case).
        case "not_pinned":
            fallback ?? "The hub hasn't pinned this repo. Pin it there with swe gh repo add."
        case "not_launchable":
            fallback ?? "The hub can't start that harness."
        case "gh_unavailable":
            fallback ?? "gh isn't installed or signed in on the hub machine."
        case "launch_failed":
            fallback ?? "The hub couldn't start the session."
        case "bad_command":
            fallback ?? "The hub refused the request. Update swe on the hub."
        default: fallback.map(capitalized) ?? "The hub answered with an error (\(status))."
        }
    }

    private static func capitalized(_ text: String) -> String {
        text.prefix(1).uppercased() + text.dropFirst()
    }
}

/// Minimal JSON tree so an envelope can carry any `data` shape.
public enum HubJSON: Codable, Sendable, Equatable {
    case string(String), number(Double), bool(Bool), object([String: HubJSON]), array([HubJSON]), null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([HubJSON].self) { self = .array(a) }
        else { self = .object(try c.decode([String: HubJSON].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        case .object(let o): try c.encode(o)
        case .array(let a): try c.encode(a)
        case .null: try c.encodeNil()
        }
    }
}
