import Foundation

/// Wire types for the hub's `/v1` routes. Field names match
/// `src/quiver/remote/protocol.py` and `catalog.py`; every field the hub
/// may omit or null is optional so a newer hub never breaks decoding.

struct Machine: Codable, Identifiable, Hashable {
    let id: String
    let name: String?
    let os: String?
    let online: Bool?
    let lastSeenAt: Double?
    let harnesses: [String]?
    /// What `POST /v1/sessions` accepts on this machine; nil from a hub
    /// or satellite older than the new-session route.
    var launchable: [String]? = nil
    /// Each launchable harness's registry name, e.g. "Qwen Code".
    var harnessNames: [String: String]? = nil
    /// Launchable harnesses swe can't read sessions of: they start, but
    /// no chat ever opens.
    var untrackedHarnesses: [String]? = nil

    /// "kais-macbook-pro.local" reads as "kais-macbook-pro".
    var displayName: String {
        let n = name ?? id
        return n.hasSuffix(".local") ? String(n.dropLast(6)) : n
    }
}

/// The hub keeps a follow-up flagged forever. Past this age it no longer
/// counts as waiting on you: it still shows, but not in amber or in the
/// "Needs you" counts.
enum Staleness {
    static let window: Double = 3 * 86_400
    static func isFresh(_ ts: Double?, now: Double = Date().timeIntervalSince1970) -> Bool {
        (ts ?? 0) >= now - window
    }
}

/// A session's lifecycle as the hub reports it. Unknown values from a
/// newer hub land in `.other` rather than failing the whole list.
enum SessionStatus: Hashable {
    case active, done, followup, error, interrupted, approval, idle
    case other(String)

    init(raw: String?) {
        switch raw ?? "" {
        case "active": self = .active
        case "done": self = .done
        case "followup": self = .followup
        case "error": self = .error
        case "interrupted": self = .interrupted
        case "approval": self = .approval
        case "": self = .idle
        case let s: self = .other(s)
        }
    }

    var label: String {
        switch self {
        case .active: "active"
        case .done: "done"
        case .followup: "needs you"
        case .error: "error"
        case .interrupted: "interrupted"
        case .approval: "approval needed"
        case .idle: "idle"
        case .other(let s): s
        }
    }

    /// Waiting on the person holding the phone.
    var needsYou: Bool { self == .followup || self == .approval }
}

struct SessionRow: Codable, Identifiable, Hashable {
    let key: String
    let harness: String?
    let agent: String?
    let cwd: String?
    /// The name you gave it on the hub, else the harness's own title.
    var title: String?
    let lastActiveAt: Double?
    var status: String?
    let machineId: String?
    var ownership: String?
    let attachable: Bool?
    /// A satellite's session, which the hub forwards to that machine.
    var remote: Bool? = nil
    /// The harness's own title, even when `title` is a name you set.
    var harnessTitle: String? = nil
    var pinned: Bool? = nil
    /// When it was pinned (epoch seconds): the Pinned section's order.
    var pinnedAt: Double? = nil
    /// The harness can start a fork of this session.
    var canFork: Bool? = nil

    var id: String { key }
    var displayTitle: String { (title?.isEmpty == false ? title : nil) ?? "Untitled session" }
    var isPinned: Bool { pinned == true }
    /// A name set on the hub replaces the harness's title.
    var hasCustomTitle: Bool {
        guard let harnessTitle, let title, !title.isEmpty else { return false }
        return title != harnessTitle
    }

    /// The pin and name the hub answered or announced, laid over this row.
    mutating func apply(_ meta: SessionMeta) {
        title = meta.title
        if let h = meta.harnessTitle { harnessTitle = h }
        pinned = meta.pinned
        pinnedAt = meta.pinned == true ? meta.pinnedAt : nil
    }
    var harnessName: String { agent ?? harness ?? "agent" }
    var sessionStatus: SessionStatus { SessionStatus(raw: status) }
    var isAttached: Bool { ownership == "attached" }
    var canAttach: Bool { !isAttached && attachable == true && sessionStatus != .active }
    /// The hub can hold a send until this session goes idle. A satellite
    /// delivers on arrival or refuses a busy session, so it can't.
    var holdsQueuedSends: Bool { remote != true }

    /// Waiting on you right now: an approval, or a recent follow-up.
    var needsYouNow: Bool {
        sessionStatus == .approval || (sessionStatus == .followup && Staleness.isFresh(lastActiveAt))
    }
}

/// `POST .../pin` and `.../title` answer this; `session.meta` carries it.
struct SessionMeta: Codable, Equatable {
    var title: String?
    var harnessTitle: String?
    var pinned: Bool?
    var pinnedAt: Double?
}

/// `POST .../control {op: "detach", mode: "when_idle"}` answers 202 with this.
struct DetachQueued: Decodable {
    let queued: Bool?
    let mode: String?
    let note: String?
}

struct SessionsResponse: Codable {
    let sessions: [SessionRow]
    let machines: [Machine]?
    let serverTime: Double?
}

struct InboxItem: Codable, Identifiable, Hashable {
    let key: String
    let machineId: String?
    let harness: String?
    let agent: String?
    let title: String?
    let cwd: String?
    let status: String?
    let lastActiveAt: Double?
    let requestId: String?

    var id: String { key }
    var displayTitle: String { (title?.isEmpty == false ? title : nil) ?? "Untitled session" }
    var harnessName: String { agent ?? harness ?? "agent" }
    var sessionStatus: SessionStatus { SessionStatus(raw: status) }
}

struct InboxResponse: Codable { let items: [InboxItem] }

struct ToolCall: Codable, Hashable {
    let name: String
    let callId: String?
    let argsSummary: String?
    let argsFull: String?
}

/// `data` of a `message.appended` event.
struct MessageData: Codable, Hashable {
    let kind: String?
    let text: String?
    let ts: Double?
    let tool: ToolCall?
    let resultRef: String?
}

/// The hub's event envelope. `data` is kept raw because its shape
/// depends on `type`; `decode(_:)` reads it as the type that fits.
struct Envelope: Decodable {
    let type: String
    let session: String?
    let seq: Int?
    let ts: Double?
    let data: JSONValue?

    func decode<T: Decodable>(_: T.Type) -> T? {
        guard let data else { return nil }
        return try? Hub.decoder.decode(T.self, from: Hub.encoder.encode(data))
    }
}

struct MessagesResponse: Decodable {
    let session: String?
    let messages: [Envelope]
    let nextBefore: Int?
    let hasMore: Bool?
}

struct StatusData: Decodable { let status: String? }
struct OwnershipData: Decodable { let ownership: String? }
struct ApprovalRequested: Decodable { let requestId: String?; let prompt: String? }
struct ApprovalResolved: Decodable { let requestId: String?; let decision: String?; let source: String? }
struct CommandResult: Decodable {
    let op: String?
    let ok: Bool?
    let note: String?
    let degraded: Bool?
    let clientId: String?
    let requestId: String?
    /// `when_idle` on a detach's result.
    var mode: String? = nil
    /// A queued send removed before it went out (`POST .../queue` cancel):
    /// `ok` is false, but nothing failed.
    var cancelled: Bool? = nil
}

/// `/v1/pair` and the approved pair-request poll both answer with this.
struct Credential: Codable, Hashable {
    let deviceId: String
    let credential: String
    let name: String?
}

struct PairRequestStarted: Decodable {
    struct Node: Decodable { let name: String?; let login: String? }
    let requestId: String
    let pollSecret: String
    let code: String
    let expiresInS: Int?
    let node: Node?
}

struct PairPoll: Decodable {
    let status: String
    let expiresInS: Int?
    let deviceId: String?
    let credential: String?
    let name: String?
}

struct PendingPairRequest: Decodable, Identifiable, Hashable {
    let requestId: String
    let name: String?
    let nodeName: String?
    let login: String?
    let createdAt: Double?
    let expiresInS: Int?
    var id: String { requestId }
}

struct PairRequestsResponse: Decodable { let requests: [PendingPairRequest] }

struct SendAccepted: Decodable { let ok: Bool?; let waiting: Bool? }

/// One queue-mode send the hub still holds until the session goes idle,
/// from `GET /v1/sessions/{key}/queue`.
struct WaitingSend: Decodable, Equatable {
    let clientId: String
    let text: String?
    /// Attachment refs as sent; kept raw, the tray only shows the text.
    var attachments: [JSONValue]? = nil
    var queuedAt: Double? = nil
}

struct QueueResponse: Decodable { let sends: [WaitingSend] }

/// `GET /v1/folders`: one folder's subfolders on the hub machine.
struct FolderListing: Decodable, Equatable {
    struct Entry: Decodable, Equatable, Identifiable, Hashable {
        let name: String
        let path: String
        var id: String { path }
    }
    let path: String
    let parent: String?
    let folders: [Entry]
    let truncated: Bool?
}

/// `POST /v1/sessions` and `GET /v1/launches/<id>` both answer with this.
/// `key` is set once `state` is "started".
struct LaunchStatus: Decodable, Equatable {
    let launchId: String
    let state: String?
    let key: String?
    let note: String?
    let queued: Bool?
}

struct HubErrorBody: Decodable {
    struct Inner: Decodable { let code: String?; let message: String? }
    let error: Inner
}

/// Minimal JSON tree so envelopes can carry any `data` shape.
enum JSONValue: Codable, Hashable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
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

/// One `/v1/search` result: the catalog row, plus the best transcript
/// snippet when the hit came from the app's search index.
struct SearchResult: Decodable, Identifiable, Hashable {
    let key: String
    let source: String?
    let snippet: String?
    let markedSnippet: String?
    let role: String?
    let session: SessionRow
    var id: String { key }
}

struct SearchResponse: Decodable {
    let results: [SearchResult]
    let indexed: Bool?
}

/// One path from `GET /v1/sessions/{id}/files`, relative to the
/// session's working folder.
struct FileEntry: Codable, Hashable {
    let path: String
    var kind: String? = nil
    var mtime: Double? = nil

    var isDir: Bool { kind == "dir" }
}

/// `GET /v1/sessions/{id}/files?q=&limit=`: the prompt box's `@` menu.
struct FilesResponse: Codable {
    var cwd: String? = nil
    var query: String? = nil
    var source: String? = nil
    var truncated: Bool? = nil
    let entries: [FileEntry]
}
