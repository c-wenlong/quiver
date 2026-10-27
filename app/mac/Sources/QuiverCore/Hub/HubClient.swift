import Foundation

/// Talks to this Mac's hub over loopback HTTP with the app's device
/// credential. Stateless apart from the base URL and credential, so a
/// re-pair just builds a new client. The iPhone app's `HubClient`
/// (app/ios) speaks the same routes.
public struct HubClient: Sendable {
    public let baseURL: URL
    public let credential: String?
    let session: URLSession
    let streamSession: URLSession

    public init(baseURL: URL, credential: String?,
                session: URLSession = HubClient.defaultSession,
                streamSession: URLSession = HubClient.defaultStreamSession) {
        self.baseURL = baseURL
        self.credential = credential
        self.session = session
        self.streamSession = streamSession
    }

    public static let defaultSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 15
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    /// Held SSE connections: no resource timeout, the hub's 15 s
    /// keepalive comments are what prove the stream is alive.
    public static let defaultStreamSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
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

    static func sessionPath(_ key: String, _ action: String) -> String {
        "/v1/sessions/\(encodeKey(key))/\(action)"
    }

    func request(_ method: String, _ path: String, query: [String: String] = [:],
                 body: [String: any Sendable]? = nil) -> URLRequest {
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
            return try HubWire.decoder.decode(T.self, from: data)
        } catch {
            throw HubError(status: status, code: "bad_response",
                           message: "The hub sent something this app doesn't understand. Update the app or swe.")
        }
    }

    func raw(_ req: URLRequest) async throws -> (Data, Int) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw HubError.unreachable
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw HubError.from(status: status, body: data) }
        return (data, status)
    }

    // MARK: Routes

    public func sessions() async throws -> [HubSessionRow] {
        try await send(request("GET", "/v1/sessions"), as: HubSessionsResponse.self).sessions
    }

    /// The request id of the approval the hub still waits on for `key`.
    public func pendingApproval(_ key: String) async throws -> String? {
        let inbox = try await send(request("GET", "/v1/inbox"), as: HubInboxResponse.self)
        return inbox.items.first { $0.key == key && $0.status == "approval" && $0.requestId?.isEmpty == false }?
            .requestId
    }

    /// The newest `limit` messages, or with `before` the `limit` just
    /// older than that seq.
    public func messages(_ key: String, limit: Int, before: Int? = nil) async throws -> HubMessagesPage {
        var query = ["limit": "\(limit)"]
        if let before { query["before"] = "\(before)" }
        return try await send(request("GET", Self.sessionPath(key, "messages"), query: query))
    }

    /// Sends `text` to the session. `queue` waits for the agent's turn to
    /// end, `steer` writes it now. Returns true when the hub holds it
    /// until then; the outcome arrives as a `command.result` event.
    public func sendMessage(_ key: String, text: String, mode: String, clientId: String) async throws -> Bool {
        try await send(request("POST", Self.sessionPath(key, "message"),
                               body: ["text": text, "mode": mode, "client_id": clientId]),
                       as: HubSendAccepted.self).waiting == true
    }

    /// `interrupt` stops the agent's turn. Returns true when the hub
    /// queued it for a satellite (202), whose `command.result` follows.
    @discardableResult
    public func control(_ key: String, op: String) async throws -> Bool {
        let (_, status) = try await raw(request("POST", Self.sessionPath(key, "control"), body: ["op": op]))
        return status == 202
    }

    /// Answers approval `requestId` with `allow`, `always` or `deny`.
    /// Returns true when the hub queued it for a satellite (202), whose
    /// `command.result` follows.
    public func approve(_ key: String, requestId: String, decision: String) async throws -> Bool {
        let (_, status) = try await raw(request("POST", Self.sessionPath(key, "approval"),
                                                body: ["request_id": requestId, "decision": decision]))
        return status == 202
    }

    /// The machines the hub knows, its own first among them.
    public func machines() async throws -> [HubMachine] {
        try await send(request("GET", "/v1/machines"), as: HubMachinesResponse.self).machines
    }

    /// The JSON `POST /v1/issues/resolve` carries. `prompt` and
    /// `context` go only when they hold text: an absent prompt means
    /// "use your suggested one".
    public static func resolveIssueBody(repo: String, number: Int, harness: String,
                                        prompt: String?, context: String?) -> [String: any Sendable] {
        var body: [String: any Sendable] = ["repo": repo, "number": number, "harness": harness]
        if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["prompt"] = prompt
        }
        if let context, !context.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["context"] = context
        }
        return body
    }

    /// Starts a session on the hub machine to resolve issue `number`:
    /// the hub makes the `quiver/issue-<n>` worktree, sends the first
    /// prompt and titles the session. Answers with the launch to poll.
    public func resolveIssue(repo: String, number: Int, harness: String,
                             prompt: String?, context: String?) async throws -> HubLaunchStatus {
        try await send(request("POST", "/v1/issues/resolve",
                               body: Self.resolveIssueBody(repo: repo, number: number, harness: harness,
                                                           prompt: prompt, context: context)))
    }

    /// Where launch `id` stands.
    public func launch(_ id: String) async throws -> HubLaunchStatus {
        try await send(request("GET", "/v1/launches/\(Self.encodeKey(id))"))
    }

    /// Trades a one-time token from `swe remote pair` for a credential.
    public func pair(token: String, name: String) async throws -> HubCredential {
        try await send(request("POST", "/v1/pair", body: ["token": token, "name": name]))
    }

    /// One session's live events after `last`, reconnecting with backoff
    /// until the consuming task is cancelled or the hub refuses the
    /// credential.
    public func events(_ key: String, last: Int) -> AsyncStream<HubStreamEvent> {
        AsyncStream { continuation in
            let task = Task {
                var cursor = last
                var delay = Duration.seconds(1)
                while !Task.isCancelled {
                    var req = request("GET", Self.sessionPath(key, "events"), query: ["last": "\(cursor)"])
                    req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    do {
                        let (bytes, response) = try await streamSession.bytes(for: req)
                        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                        if status == 401 {
                            continuation.yield(.unauthorized)
                            break
                        }
                        guard status == 200 else { throw HubError(status: status, code: "stream", message: "") }
                        continuation.yield(.connected)
                        delay = .seconds(1)
                        var parser = SSEParser()
                        // `lines` drops the blank separator lines, so each
                        // data line is a whole frame here: the hub never
                        // splits a JSON envelope across lines.
                        for try await line in bytes.lines {
                            guard let payload = parser.feed(line) ?? parser.feed("") else { continue }
                            guard let env = try? HubWire.decoder.decode(HubEnvelope.self, from: Data(payload.utf8))
                            else { continue }
                            if env.type == "journal.truncated" {
                                continuation.yield(.truncated)
                                continue
                            }
                            if env.type == "journal.caught-up" { continue }
                            if let seq = env.seq { cursor = max(cursor, seq) }
                            continuation.yield(.event(env))
                        }
                    } catch {
                        if Task.isCancelled { break }
                    }
                    continuation.yield(.disconnected)
                    try? await Task.sleep(for: delay)
                    delay = min(delay * 2, .seconds(15))
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

public enum HubStreamEvent: Sendable {
    case connected, disconnected, unauthorized
    /// The journal dropped events past the cursor: reload history.
    case truncated
    case event(HubEnvelope)
}

/// Splits a server-sent-events byte stream into `data:` payloads.
///
/// The hub writes `id:`, `event:` and one `data:` line per frame, then a
/// blank line; keepalives are `: ka` comments. Only `data:` matters: the
/// JSON envelope inside carries the type and seq.
struct SSEParser {
    private var dataLines: [String] = []

    /// Feeds one line (without its newline). Returns a frame's data when
    /// the line ends that frame.
    mutating func feed(_ line: String) -> String? {
        if line.isEmpty {
            defer { dataLines.removeAll() }
            return dataLines.isEmpty ? nil : dataLines.joined(separator: "\n")
        }
        if line.hasPrefix(":") { return nil }
        if line.hasPrefix("data:") {
            var value = line.dropFirst(5)
            if value.first == " " { value = value.dropFirst() }
            dataLines.append(String(value))
        }
        return nil
    }
}
