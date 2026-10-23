import Foundation

enum Hub {
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
    /// Plain keys: only used to re-serialize `JSONValue` trees, whose keys
    /// are already the hub's snake_case.
    static let encoder = JSONEncoder()
    static let defaultPort = 8477
}

/// A failed hub call, worded for the person reading it on the phone.
struct HubError: LocalizedError, Equatable {
    let status: Int
    let code: String
    let message: String

    var errorDescription: String? { message }
    var isUnauthorized: Bool { status == 401 }

    static func network(_ error: Error) -> HubError {
        HubError(status: 0, code: "network",
                 message: "Can't reach the hub. Check that Tailscale is on.")
    }
}

/// Talks to one hub over plain HTTP on the tailnet. Stateless apart from
/// the base URL and credential, so a re-pair just builds a new client.
struct HubClient: Sendable {
    let baseURL: URL
    let credential: String?

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    /// Long-lived SSE connections: no request timeout, the hub's 15 s
    /// keepalive comments are what prove the stream is alive.
    static let streamSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = .infinity
        return URLSession(configuration: config)
    }()

    func url(_ path: String, query: [String: String] = [:]) -> URL {
        var parts = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        parts.percentEncodedPath = path
        if !query.isEmpty {
            parts.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        return parts.url!
    }

    /// Session keys hold `:` and `@`, so they go in the path percent-encoded.
    static func encodeKey(_ key: String) -> String {
        key.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-._~"))) ?? key
    }

    func request(_ method: String, _ path: String, query: [String: String] = [:], body: [String: Any]? = nil) -> URLRequest {
        var req = URLRequest(url: url(path, query: query))
        req.httpMethod = method
        if let credential { req.setValue("Bearer \(credential)", forHTTPHeaderField: "Authorization") }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        return req
    }

    /// Runs a request and decodes the 2xx body as `T`.
    func send<T: Decodable>(_ req: URLRequest, as: T.Type = T.self) async throws -> T {
        let (data, status) = try await raw(req)
        do {
            return try Hub.decoder.decode(T.self, from: data)
        } catch {
            throw HubError(status: status, code: "bad_response", message: "The hub sent something this app doesn't understand. Update the app or the hub.")
        }
    }

    func raw(_ req: URLRequest) async throws -> (Data, Int) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await Self.session.data(for: req)
        } catch {
            throw HubError.network(error)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let body = try? Hub.decoder.decode(HubErrorBody.self, from: data)
            throw HubError(status: status, code: body?.error.code ?? "http_\(status)",
                           message: Self.friendly(code: body?.error.code, fallback: body?.error.message, status: status))
        }
        return (data, status)
    }

    static func friendly(code: String?, fallback: String?, status: Int) -> String {
        switch code {
        case "unauthorized": "This device isn't paired any more. Pair it again."
        case "not_tailnet": "The hub can't see this phone on your tailnet. Use a pair token instead."
        case "rate_limited", "busy": "Too many pair requests. Wait a minute and try again."
        case "pair_failed": "That pair token expired or was already used."
        case "unknown_request": "This pair request expired."
        case "not_attached": "This session is read-only until you attach it."
        case "not_idle": "The session is mid-turn. Attach once it's idle."
        case "not_attachable": "This harness can't be attached from the phone."
        case "backlog_full": "Too many messages are waiting. Try again shortly."
        case "no_match": "That code doesn't match a pending request."
        case "self_approval": "Approve from a different device."
        case "bad_cwd": "That folder doesn't exist on this machine."
        case "not_launchable": "That harness can't start on this machine."
        case "unknown_launch": "The hub lost track of this launch. Check Sessions in a moment."
        default: fallback ?? "The hub answered with an error (\(status))."
        }
    }

    // MARK: Routes

    func status() async throws -> JSONValue { try await send(request("GET", "/v1/status")) }

    func sessions() async throws -> SessionsResponse { try await send(request("GET", "/v1/sessions")) }

    func inbox() async throws -> InboxResponse { try await send(request("GET", "/v1/inbox")) }

    func search(_ query: String, limit: Int = 50) async throws -> SearchResponse {
        try await send(request("GET", "/v1/search", query: ["q": query, "limit": "\(limit)"]))
    }

    func messages(_ key: String, limit: Int = 500) async throws -> MessagesResponse {
        try await send(request("GET", "/v1/sessions/\(Self.encodeKey(key))/messages", query: ["limit": "\(limit)"]))
    }

    func sendMessage(_ key: String, text: String, mode: String, clientId: String) async throws -> SendAccepted {
        try await send(request("POST", "/v1/sessions/\(Self.encodeKey(key))/message",
                               body: ["text": text, "mode": mode, "client_id": clientId]))
    }

    /// Returns true when the hub queued it for a satellite (202), so the
    /// outcome arrives later as `command.result` or `session.ownership`.
    @discardableResult
    func control(_ key: String, op: String) async throws -> Bool {
        let (_, status) = try await raw(request("POST", "/v1/sessions/\(Self.encodeKey(key))/control", body: ["op": op]))
        return status == 202
    }

    /// Returns true when the hub queued it for a satellite (202): the
    /// outcome then arrives later as `command.result`.
    func approve(_ key: String, requestId: String, decision: String) async throws -> Bool {
        let (_, status) = try await raw(request("POST", "/v1/sessions/\(Self.encodeKey(key))/approval",
                                                body: ["request_id": requestId, "decision": decision]))
        return status == 202
    }

    /// Starts `harness` in `cwd` on `machine` with `prompt` as its first
    /// message. The hub answers before the harness has a session id, so
    /// poll `launch(_:)` for the key.
    func startSession(machine: String, harness: String, cwd: String, prompt: String) async throws -> LaunchStatus {
        try await send(request("POST", "/v1/sessions",
                               body: ["machine": machine, "harness": harness, "cwd": cwd, "prompt": prompt]))
    }

    func launch(_ id: String) async throws -> LaunchStatus {
        try await send(request("GET", "/v1/launches/\(Self.encodeKey(id))"))
    }

    func pair(token: String, name: String) async throws -> Credential {
        try await send(request("POST", "/v1/pair", body: ["token": token, "name": name]))
    }

    func startPairRequest(name: String) async throws -> PairRequestStarted {
        try await send(request("POST", "/v1/pair/request", body: ["name": name]))
    }

    func pollPairRequest(id: String, secret: String) async throws -> PairPoll {
        try await send(request("GET", "/v1/pair/request/\(Self.encodeKey(id))", query: ["secret": secret]))
    }

    func pairRequests() async throws -> [PendingPairRequest] {
        try await send(request("GET", "/v1/pair/requests"), as: PairRequestsResponse.self).requests
    }

    func decidePairRequest(id: String, code: String, approve: Bool) async throws {
        _ = try await raw(request("POST", approve ? "/v1/pair/requests/approve" : "/v1/pair/requests/deny",
                                  body: ["request_id": id, "code": code]))
    }
}
