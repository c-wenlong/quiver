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
            // URLComponents leaves `+` bare, and the hub's `parse_qs` reads
            // a bare `+` as a space: `~/C++ tools` would ask for `~/C   tools`.
            parts.percentEncodedQuery = parts.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
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
        } catch {
            // URLSession reports a cancelled task as URLError(.cancelled),
            // not CancellationError: leaving a page is not a down hub.
            if error is CancellationError || (error as? URLError)?.code == .cancelled
                || Task.isCancelled {
                throw CancellationError()
            }
            throw HubError.unreachable
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else { throw HubError.from(status: status, body: data) }
        return (data, status)
    }

    // MARK: Routes

    public func sessions() async throws -> [HubSessionRow] {
        try await sessionsAndMachines().sessions
    }

    /// `GET /v1/sessions` with the machine list it carries alongside.
    public func sessionsAndMachines() async throws -> (sessions: [HubSessionRow], machines: [HubMachine]) {
        let reply = try await send(request("GET", "/v1/sessions"), as: HubSessionsResponse.self)
        return (reply.sessions, reply.machines ?? [])
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

    /// The slash commands this session's harness will run, for the
    /// prompt box's `/` menu.
    public func commands(_ key: String) async throws -> CommandsResponse {
        try await send(request("GET", Self.sessionPath(key, "commands")))
    }

    /// The queue-mode sends the hub holds for `key` until its turn ends,
    /// oldest first: the chat's Queued tray. Empty for a satellite's
    /// session, whose sends never wait on the hub.
    public func queue(_ key: String) async throws -> [HubWaitingSend] {
        try await send(request("GET", Self.sessionPath(key, "queue")), as: HubQueueResponse.self).sends
    }

    /// Acts on one waiting send: `steer` sends it now, `cancel` drops it,
    /// `edit` replaces its text in place. A steer's outcome arrives later
    /// as its `command.result`.
    public func queueAction(_ key: String, clientId: String, op: String, text: String? = nil) async throws {
        var body: [String: any Sendable] = ["client_id": clientId, "op": op]
        if let text { body["text"] = text }
        _ = try await raw(request("POST", Self.sessionPath(key, "queue"), body: body))
    }

    /// Paths under the session's working folder matching `query`, for
    /// the prompt box's `@` menu. Hub-machine sessions only: a
    /// satellite's session answers `files_unavailable`.
    public func files(_ key: String, query: String, limit: Int = MentionSearch.limit) async throws -> FilesResponse {
        try await send(request("GET", Self.sessionPath(key, "files"), query: ["q": query, "limit": "\(limit)"]))
    }

    /// `interrupt` stops the agent's turn. Returns true when the hub
    /// queued it for a satellite (202), whose `command.result` follows.
    @discardableResult
    public func control(_ key: String, op: String) async throws -> Bool {
        let (_, status) = try await raw(request("POST", Self.sessionPath(key, "control"), body: ["op": op]))
        return status == 202
    }

    /// Answers approval `requestId` with `allow`, `always`, `deny`, or a
    /// menu row's digit. Returns true when the hub queued it for a
    /// satellite (202), whose `command.result` follows.
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
    /// "use your suggested one". `model` goes only as a non-blank
    /// token — the hub refuses a malformed one and a harness whose
    /// launches can't honour it.
    public static func resolveIssueBody(repo: String, number: Int, harness: String,
                                        prompt: String?, context: String?,
                                        model: String? = nil) -> [String: any Sendable] {
        var body: [String: any Sendable] = ["repo": repo, "number": number, "harness": harness]
        if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["prompt"] = prompt
        }
        if let context, !context.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["context"] = context
        }
        if let model = launchToken(model) { body["model"] = model }
        return body
    }

    /// A launch option as the hub takes it: one token, or nil when the
    /// field was blank — whitespace would fail the hub's shape check,
    /// so it is trimmed before it can travel.
    static func launchToken(_ value: String?) -> String? {
        let pick = (value ?? "").trimmingCharacters(in: .whitespaces)
        return pick.isEmpty ? nil : pick
    }

    /// Starts a session on the hub machine to resolve issue `number`:
    /// the hub makes the `quiver/issue-<n>` worktree, sends the first
    /// prompt and titles the session. Answers with the launch to poll.
    public func resolveIssue(repo: String, number: Int, harness: String,
                             prompt: String?, context: String?,
                             model: String? = nil) async throws -> HubLaunchStatus {
        try await send(request("POST", "/v1/issues/resolve",
                               body: Self.resolveIssueBody(repo: repo, number: number, harness: harness,
                                                           prompt: prompt, context: context, model: model)))
    }

    /// Where launch `id` stands.
    public func launch(_ id: String) async throws -> HubLaunchStatus {
        try await send(request("GET", "/v1/launches/\(Self.encodeKey(id))"))
    }

    /// Trades a one-time token from `swe remote pair` for a credential.
    public func pair(token: String, name: String) async throws -> HubCredential {
        try await send(request("POST", "/v1/pair", body: ["token": token, "name": name]))
    }

    // MARK: Sessions' launch

    /// The JSON `POST /v1/sessions` carries: a launchable harness, an
    /// absolute folder, and the first prompt — the hub refuses a blank
    /// one. `machine` targets a satellite; nil means the hub itself.
    /// `model` is the optional launch pick: a non-blank token or
    /// nothing.
    public static func launchBody(harness: String, cwd: String, prompt: String,
                                  machine: String? = nil,
                                  model: String? = nil,
                                  yolo: Bool = false) -> [String: any Sendable] {
        var body: [String: any Sendable] = ["harness": harness, "cwd": cwd, "prompt": prompt]
        if let machine, !machine.isEmpty { body["machine"] = machine }
        if let model = launchToken(model) { body["model"] = model }
        if yolo { body["yolo"] = true }
        return body
    }

    /// `POST /v1/sessions`: start `harness` in `cwd` with `prompt`. The
    /// 202 answer is the launch to poll through `launch(_:)` — the same
    /// shape `resolveIssue` returns. `yolo` starts the session with its
    /// approval checks off; a harness the machine doesn't list in
    /// `yolo_at_launch` gets the flag refused, never silently dropped.
    public func startSession(harness: String, cwd: String, prompt: String,
                             machine: String? = nil,
                             model: String? = nil,
                             yolo: Bool = false) async throws -> HubLaunchStatus {
        try await send(request("POST", "/v1/sessions",
                               body: Self.launchBody(harness: harness, cwd: cwd,
                                                     prompt: prompt, machine: machine,
                                                     model: model, yolo: yolo)))
    }

    /// `POST /v1/pulls/<o>/<r>/<n>/open`: detached PR worktree + launch,
    /// the resolve shape. Its `origin` links the adopted session to the
    /// pull's task row, as an issue resolve's does.
    public func openPull(owner: String, repo: String, number: Int, harness: String,
                         prompt: String? = nil, model: String? = nil) async throws -> HubLaunchStatus {
        var body: [String: any Sendable] = ["harness": harness]
        if let prompt, !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            body["prompt"] = prompt
        }
        if let model = Self.launchToken(model) { body["model"] = model }
        let path = "/v1/pulls/\(Self.encodeKey(owner))/\(Self.encodeKey(repo))/\(number)/open"
        return try await send(request("POST", path, body: body))
    }

    /// `GET /v1/folders?path=`: subfolders of `path` (the hub user's home
    /// when nil) on the hub machine — the new-session folder browser.
    /// Satellites' folders can't be listed from the hub.
    public func folders(path: String? = nil) async throws -> HubFolderListing {
        var query: [String: String] = [:]
        if let path, !path.isEmpty { query["path"] = path }
        return try await send(request("GET", "/v1/folders", query: query))
    }

    // MARK: Tasks

    /// `GET /v1/app/tasks`: the work-item store over the sources' lists
    /// with the page joins (`session_rows`, `launch_rows`) the web app's
    /// Tasks page reads. `refresh` skips the hub's one-minute `gh` cache.
    public func appTasks(refresh: Bool = false) async throws -> HubTasksResponse {
        try await send(request("GET", "/v1/app/tasks",
                               query: refresh ? ["refresh": "1"] : [:]))
    }

    /// `GET /v1/app/tasks?harnesses=1`: what `POST /v1/issues/resolve`
    /// and `…/pulls/open` can start on the hub, in Provider visibility
    /// order — the task detail's New-agent-session picker.
    public func taskHarnesses() async throws -> (harnesses: [HubTaskHarness], machine: String?) {
        let reply = try await send(request("GET", "/v1/app/tasks", query: ["harnesses": "1"]),
                                   as: HubTaskHarnesses.self)
        return (reply.harnesses, reply.machine)
    }

    /// `POST /v1/sessions/<key>/task {task_ref}`: link a session to the
    /// task by hand — a resolve or PR open links itself through the
    /// launch's `origin`, so this covers sessions started outside a
    /// task flow. `ref` is `HubTaskRow.refBody`.
    public func linkTask(_ key: String, ref: [String: any Sendable]) async throws {
        _ = try await raw(request("POST", Self.sessionPath(key, "task"),
                                  body: ["task_ref": ref]))
    }

    /// `DELETE /v1/sessions/<key>/task {task_ref}`: drop this task's
    /// link from the session.
    public func unlinkTask(_ key: String, ref: [String: any Sendable]) async throws {
        _ = try await raw(request("DELETE", Self.sessionPath(key, "task"),
                                  body: ["task_ref": ref]))
    }

    /// One session's live events after `last`, reconnecting with backoff
    /// until the hub refuses the credential or the reader goes away: its
    /// task cancelled, or its loop left by `break` or `return`. Either
    /// way the stream terminates, and `onTermination` cancels the loop
    /// and its connection.
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
                                // The hub restarts the stream at `seq`: our
                                // cursor when its ring dropped events past it,
                                // its head when the journal was wiped under it.
                                // Take it even when lower, or every reconnect
                                // asks from the old cursor again.
                                if let seq = env.seq { cursor = seq }
                                continuation.yield(.truncated(seq: env.seq))
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
    /// The journal dropped events past the cursor (or was wiped under
    /// it): reload history and follow on from `seq`, the hub's restart
    /// point, which can be lower than the cursor.
    case truncated(seq: Int?)
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
