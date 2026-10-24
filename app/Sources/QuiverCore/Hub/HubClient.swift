import Foundation

/// Talks to this Mac's hub over loopback HTTP with the app's device
/// credential. Stateless apart from the base URL and credential, so a
/// re-pair just builds a new client. The iPhone app's `HubClient`
/// (remote/ios) speaks the same routes.
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

    /// The newest `limit` messages, or with `before` the `limit` just
    /// older than that seq.
    public func messages(_ key: String, limit: Int, before: Int? = nil) async throws -> HubMessagesPage {
        var query = ["limit": "\(limit)"]
        if let before { query["before"] = "\(before)" }
        return try await send(request("GET", Self.sessionPath(key, "messages"), query: query))
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
