import Foundation
import UIKit

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
    /// The hub's own wording, kept when `message` replaces it with a
    /// friendlier one (`not_waiting` says why the send left the queue).
    var reason: String? = nil

    var errorDescription: String? { message }
    var isUnauthorized: Bool { status == 401 }

    static let networkMessage = "Can't reach the hub. Check that Tailscale is on."

    static func network(_ error: Error) -> HubError {
        HubError(status: 0, code: "network", message: networkMessage)
    }

    /// The message under a "Can't reach the hub" title. The network
    /// error's first sentence is that title, so only the next step stays
    /// (the web app's "Check the connection, then try again", worded for
    /// the tailnet). Any other failure keeps its own words.
    static func detail(underUnreachableTitle message: String) -> String {
        message == networkMessage ? "Check that Tailscale is on, then try again." : message
    }
}

/// Talks to one hub over plain HTTP on the tailnet. Stateless apart from
/// the base URL and credential, so a re-pair just builds a new client.
struct HubClient: Sendable {
    let baseURL: URL
    let credential: String?
    /// The session REST calls go through; tests hand in one whose
    /// protocol is stubbed.
    var session: URLSession = HubClient.requestSession

    static let requestSession: URLSession = {
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
            // URLComponents leaves `+` bare, and the hub's `parse_qs` reads
            // a bare `+` as a space: `a+b.html` would ask for `a b.html`.
            parts.percentEncodedQuery = parts.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
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
        let (data, response) = try await fetch(req)
        return (data, response.statusCode)
    }

    /// Runs a request and hands back a 2xx body with its response, for a
    /// route whose headers matter (the preview's `Content-Type`).
    func fetch(_ req: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            // URLSession reports a cancelled task as URLError(.cancelled):
            // leaving a page is not a hub that can't be reached, so callers
            // get CancellationError and ignore it.
            if error is CancellationError || (error as? URLError)?.code == .cancelled
                || Task.isCancelled {
                throw CancellationError()
            }
            throw HubError.network(error)
        }
        let http = response as? HTTPURLResponse
        guard let http, (200..<300).contains(http.statusCode) else {
            throw Self.error(status: http?.statusCode ?? 0, body: data)
        }
        return (data, http)
    }

    /// A non-2xx answer as the error the phone shows.
    static func error(status: Int, body data: Data) -> HubError {
        let body = try? Hub.decoder.decode(HubErrorBody.self, from: data)
        return HubError(status: status, code: body?.error.code ?? "http_\(status)",
                        message: friendly(code: body?.error.code, fallback: body?.error.message, status: status),
                        reason: body?.error.message)
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
        case "browse_unavailable": "Browsing works on the hub machine only for now. Pick a recent folder instead."
        case "not_readable": "The hub can't read that folder."
        case "duplicate_client_id": "That message is already queued."
        case "not_waiting": "That message already left the queue."
        case "unknown_send": "The hub doesn't know that message."
        case "queue_unsupported": "Queued messages can't be changed on this machine."
        case "too_many_pins": "Too many sessions are pinned. Unpin one first."
        case "fork_pending": "Another fork in this folder is still starting. Try again once it shows up."
        case "fork_unsupported": "This session can't be forked: its harness has no fork command, or the session has no id yet."
        case "not_installed": "That harness isn't installed on the machine any more."
        case "unknown_session": "The hub doesn't know this session any more. Pull to refresh."
        case "not_pinned": "That repo isn't pinned on this hub. Pin it with `swe gh repo add`."
        case "gh_unavailable": "GitHub's gh isn't signed in on the hub."
        case "open_pending": "An agent for this pull request is still starting in its worktree."
        case "resolve_pending": "An agent for this issue is still starting in its worktree."
        case "needs_pairing": "Pair this phone with the hub to use the console."
        case "bad_key": "The hub didn't accept this phone's SSH key. Update the hub."
        case "unknown_device": "The hub doesn't know this device any more."
        case "bad_dismiss": "The hub couldn't clear that row. Update the app or the hub."
        default: fallback ?? "The hub answered with an error (\(status))."
        }
    }

    // MARK: Routes

    func sessions() async throws -> SessionsResponse { try await send(request("GET", "/v1/sessions")) }

    /// `GET /v1/inbox`: this device's undismissed pending rows — the
    /// hub already cut stale follow-ups/errors and applied dismissals.
    /// `done` adds the sessions finished since the hub's midnight.
    func inbox(done: Bool = false) async throws -> InboxResponse {
        try await send(request("GET", "/v1/inbox", query: done ? ["done": "1"] : [:]))
    }

    /// `POST /v1/inbox/dismiss {key, last_active_at}`: hides the row for
    /// this device until the session (or GitHub thread) moves past the
    /// stamp sent, which is what brings a cleared row back.
    func dismissInbox(_ key: String, lastActiveAt: Double) async throws {
        _ = try await raw(request("POST", "/v1/inbox/dismiss",
                                  body: ["key": key, "last_active_at": lastActiveAt]))
    }

    // MARK: Pull requests

    /// `GET /v1/pulls`: the hub's pinned repos, each with its open
    /// pull requests. `refresh` skips the hub's one-minute `gh` cache.
    func pulls(refresh: Bool = false) async throws -> PullsResponse {
        try await send(request("GET", "/v1/pulls",
                               query: refresh ? ["refresh": "1"] : [:]))
    }

    /// `GET /v1/pulls/<o>/<r>/<n>`: one pull request in full — body,
    /// per-check rows and reviews.
    func pull(repo: String, number: Int, refresh: Bool = false) async throws -> PullResponse {
        try await send(request("GET", "/v1/pulls/\(Self.repoPath(repo))/\(number)",
                               query: refresh ? ["refresh": "1"] : [:]))
    }

    /// `POST /v1/pulls/<o>/<r>/<n>/open {harness, prompt?, model?}` → a
    /// launch in the PR's detached worktree; poll `launch(_:)` for the
    /// session key, as for `startSession`. An empty `prompt` lets the
    /// hub build its review brief; a nil `model` sends no pick.
    func openPull(repo: String, number: Int, harness: String, prompt: String, model: String? = nil) async throws -> LaunchStatus {
        var body: [String: Any] = ["harness": harness]
        if !prompt.isEmpty { body["prompt"] = prompt }
        if let model, !model.isEmpty { body["model"] = model }
        return try await send(request("POST", "/v1/pulls/\(Self.repoPath(repo))/\(number)/open",
                                      body: body))
    }

    /// `POST /v1/app/repositories/merge {repo, number, method, auto}`:
    /// the web app's merge, which the hub serves to any paired device.
    /// The hub reads the PR again first and refuses (409) a merge the
    /// detail page wouldn't offer any more; GitHub's own refusals come
    /// back as a 502 in its words.
    func mergePull(repo: String, number: Int, method: MergeMethod, auto: Bool) async throws {
        _ = try await raw(mergeRequest(repo: repo, number: number, method: method, auto: auto))
    }

    func mergeRequest(repo: String, number: Int, method: MergeMethod, auto: Bool) -> URLRequest {
        request("POST", "/v1/app/repositories/merge",
                body: ["repo": repo, "number": number, "method": method.rawValue, "auto": auto])
    }

    /// `POST /v1/app/repositories/rerun {repo, run, failed}`: re-run a
    /// workflow run's failed jobs, or all of them. GitHub decides what
    /// can re-run; its refusal comes back as a 502 in its words.
    func rerunRun(repo: String, run: Int, failed: Bool) async throws {
        _ = try await raw(rerunRequest(repo: repo, run: run, failed: failed))
    }

    func rerunRequest(repo: String, run: Int, failed: Bool) -> URLRequest {
        request("POST", "/v1/app/repositories/rerun",
                body: ["repo": repo, "run": run, "failed": failed])
    }

    /// "o/r" as the `/v1/pulls/<owner>/<repo>` path pair, each part
    /// percent-encoded on its own.
    static func repoPath(_ repo: String) -> String {
        repo.split(separator: "/", omittingEmptySubsequences: false)
            .map { encodeKey(String($0)) }.joined(separator: "/")
    }

    // MARK: Issues

    /// `GET /v1/issues`: the hub's pinned repos, each with its issues
    /// in `state` (`open`, `closed` or `all`). `refresh` skips the
    /// hub's one-minute `gh` cache.
    func issues(refresh: Bool = false, state: String = "open") async throws -> IssuesResponse {
        var query: [String: String] = [:]
        if refresh { query["refresh"] = "1" }
        if state != "open" { query["state"] = state }
        return try await send(request("GET", "/v1/issues", query: query))
    }

    /// `GET /v1/issues/<o>/<r>/<n>`: one issue in full — body, comments
    /// and the prompt a resolve would start with.
    func issue(repo: String, number: Int, refresh: Bool = false) async throws -> IssueResponse {
        try await send(request("GET", "/v1/issues/\(Self.repoPath(repo))/\(number)",
                               query: refresh ? ["refresh": "1"] : [:]))
    }

    /// `POST /v1/issues/resolve {repo, number, harness, prompt?, model?}`
    /// → a launch on branch `quiver/issue-<n>` in the issue's worktree;
    /// poll `launch(_:)` for the session key, as for `startSession`. An
    /// empty `prompt` lets the hub build its resolve brief; a nil `model`
    /// sends no pick.
    func resolveIssue(repo: String, number: Int, harness: String, prompt: String, model: String? = nil) async throws -> LaunchStatus {
        var body: [String: Any] = ["repo": repo, "number": number, "harness": harness]
        if !prompt.isEmpty { body["prompt"] = prompt }
        if let model, !model.isEmpty { body["model"] = model }
        return try await send(request("POST", "/v1/issues/resolve", body: body))
    }

    // MARK: Tasks

    /// `GET /v1/tasks`: the hub's work-item store over the sources' open
    /// items — GitHub issues and PRs from the pinned repos, Linear when a
    /// key is configured. `refresh` skips the one-minute `gh` cache.
    func tasks(refresh: Bool = false) async throws -> TasksResponse {
        try await send(request("GET", "/v1/tasks",
                               query: refresh ? ["refresh": "1"] : [:]))
    }

    /// `POST /v1/sessions/<key>/task {task_ref}`: link a session to the
    /// task by hand — a resolve or PR open links itself through the
    /// launch's `origin`, so this covers the sessions the user started
    /// outside a task flow.
    func linkTask(_ key: String, task: TaskRow) async throws {
        _ = try await raw(request("POST", "/v1/sessions/\(Self.encodeKey(key))/task",
                                  body: ["task_ref": task.refBody]))
    }

    /// `DELETE /v1/sessions/<key>/task {task_ref}`: drop this task's link
    /// from the session.
    func unlinkTask(_ key: String, task: TaskRow) async throws {
        _ = try await raw(request("DELETE", "/v1/sessions/\(Self.encodeKey(key))/task",
                                  body: ["task_ref": task.refBody]))
    }

    func search(_ query: String, limit: Int = 50) async throws -> SearchResponse {
        try await send(request("GET", "/v1/search", query: ["q": query, "limit": "\(limit)"]))
    }

    /// Limits and 100-day session counts for the hub and each satellite.
    /// `refresh` refetches readings older than a minute, and failed ones.
    func usage(refresh: Bool = false) async throws -> UsageResponse {
        try await send(request("GET", "/v1/usage", query: refresh ? ["refresh": "1"] : [:]))
    }

    /// Token and cost totals for `period`, with days cut at midnight in
    /// the phone's time zone.
    func overview(period: UsagePeriod, timeZone: TimeZone = .current) async throws -> OverviewResponse {
        try await send(request("GET", "/v1/app/overview", query: ["period": period.rawValue, "tz": timeZone.identifier]))
    }

    /// The newest `limit` messages, or with `before` the `limit` just
    /// older than that seq: how the chat pages back through history.
    func messages(_ key: String, limit: Int, before: Int? = nil) async throws -> MessagesResponse {
        var query = ["limit": "\(limit)"]
        if let before { query["before"] = "\(before)" }
        return try await send(request("GET", "/v1/sessions/\(Self.encodeKey(key))/messages", query: query))
    }

    func sendMessage(_ key: String, text: String, mode: String, clientId: String) async throws -> SendAccepted {
        try await send(request("POST", "/v1/sessions/\(Self.encodeKey(key))/message",
                               body: ["text": text, "mode": mode, "client_id": clientId]))
    }

    /// Queue-mode sends still waiting for the session to go idle, oldest first.
    func queue(_ key: String) async throws -> QueueResponse {
        try await send(request("GET", "/v1/sessions/\(Self.encodeKey(key))/queue"))
    }

    /// Acts on one waiting send: `steer` sends it now, `cancel` drops it,
    /// `edit` replaces its text in place. The outcome of a steer arrives
    /// later as its `command.result`.
    func queueAction(_ key: String, clientId: String, op: String, text: String? = nil) async throws {
        var body: [String: Any] = ["client_id": clientId, "op": op]
        if let text { body["text"] = text }
        _ = try await raw(request("POST", "/v1/sessions/\(Self.encodeKey(key))/queue", body: body))
    }

    /// Paths under the session's working folder matching `query`, for
    /// the prompt box's `@` menu. Hub-machine sessions only.
    func files(_ key: String, query: String, limit: Int = 50) async throws -> FilesResponse {
        try await send(request("GET", "/v1/sessions/\(Self.encodeKey(key))/files",
                               query: ["q": query, "limit": "\(limit)"]))
    }

    /// `GET /v1/sessions/{key}/preview?path=`: one `.html`, `.htm` or
    /// `.svg` file from the session's working folder as it is on disk
    /// now, for a tool card's Preview. `path` is relative to that folder
    /// (`PagePreview.relative`). Hub-machine sessions only.
    func preview(_ key: String, path: String) async throws -> PagePreview.Page {
        let (data, response) = try await fetch(request("GET", "/v1/sessions/\(Self.encodeKey(key))/preview",
                                                        query: ["path": path]))
        return PagePreview.Page(path: path, body: data,
                                contentType: response.value(forHTTPHeaderField: "Content-Type"))
    }

    /// The slash commands this session's harness will run, for the prompt
    /// box's `/` menu.
    func commands(_ key: String) async throws -> CommandsResponse {
        try await send(request("GET", "/v1/sessions/\(Self.encodeKey(key))/commands"))
    }

    /// Returns true when the hub queued it for a satellite (202), so the
    /// outcome arrives later as `command.result` or `session.ownership`.
    @discardableResult
    func control(_ key: String, op: String, value: String? = nil, clientId: String? = nil) async throws -> Bool {
        var body: [String: Any] = ["op": op]
        if let value { body["value"] = value }
        if let clientId { body["client_id"] = clientId }
        let (_, status) = try await raw(request("POST", "/v1/sessions/\(Self.encodeKey(key))/control", body: body))
        return status == 202
    }

    /// Hands an attached session back. Returns true on a 202: a
    /// `when_idle` detach waiting for the turn to end, or any mode
    /// forwarded to a satellite.
    @discardableResult
    func detach(_ key: String, mode: DetachMode) async throws -> Bool {
        let (_, status) = try await raw(request("POST", "/v1/sessions/\(Self.encodeKey(key))/control",
                                                body: ["op": "detach", "mode": mode.rawValue]))
        return status == 202
    }

    func pin(_ key: String, pinned: Bool) async throws -> SessionMeta {
        try await send(request("POST", "/v1/sessions/\(Self.encodeKey(key))/pin", body: ["pinned": pinned]))
    }

    /// An empty title clears the name, so the harness's own shows again.
    func rename(_ key: String, title: String) async throws -> SessionMeta {
        try await send(request("POST", "/v1/sessions/\(Self.encodeKey(key))/title", body: ["title": title]))
    }

    /// Starts a new session from this one's history with `prompt` as its
    /// first message; poll `launch(_:)` for the new key, as for a new session.
    func fork(_ key: String, prompt: String) async throws -> LaunchStatus {
        try await send(request("POST", "/v1/sessions/\(Self.encodeKey(key))/fork", body: ["prompt": prompt]))
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
    /// poll `launch(_:)` for the key. `model` is the sheet's pick: the
    /// field sends it only when the machine's `model_at_launch` says the
    /// harness honours one, and a nil sends no pick.
    func startSession(machine: String, harness: String, cwd: String, prompt: String, model: String? = nil) async throws -> LaunchStatus {
        var body: [String: Any] = ["machine": machine, "harness": harness, "cwd": cwd, "prompt": prompt]
        if let model, !model.isEmpty { body["model"] = model }
        return try await send(request("POST", "/v1/sessions", body: body))
    }

    func launch(_ id: String) async throws -> LaunchStatus {
        try await send(request("GET", "/v1/launches/\(Self.encodeKey(id))"))
    }

    /// Subfolders of `path` (the hub user's home when nil) on `machine`.
    func folders(machine: String, path: String?) async throws -> FolderListing {
        var query = ["machine": machine]
        if let path { query["path"] = path }
        return try await send(request("GET", "/v1/folders", query: query))
    }

    /// The console's setup on the hub's machine: login user, port, host keys.
    func ssh() async throws -> SSHInfo { try await send(request("GET", "/v1/ssh")) }

    /// Stores this phone's SSH public key on the hub ("" clears it).
    func registerSSHKey(_ publicKey: String) async throws -> SSHInfo {
        try await send(request("POST", "/v1/ssh/key", body: ["public_key": publicKey]))
    }

    /// What this phone tells the hub it is at pair time: the Settings
    /// list's kind icon, hardware model and OS. A browser can't report
    /// `model`; `uname` can — "iPhone16,2".
    static let deviceMeta: [String: Any] = {
        let device = UIDevice.current
        var uts = utsname()
        uname(&uts)
        let model = withUnsafeBytes(of: &uts.machine) {
            String(cString: $0.baseAddress!.assumingMemoryBound(to: CChar.self))
        }
        return [
            "kind": device.userInterfaceIdiom == .pad ? "ipad" : "iphone",
            "model": model,
            "client": "Quiver Remote \(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")",
            "os": "\(device.systemName) \(device.systemVersion)",
        ]
    }()

    func pair(token: String, name: String) async throws -> Credential {
        try await send(request("POST", "/v1/pair",
                               body: ["token": token, "name": name, "device": Self.deviceMeta]))
    }

    func startPairRequest(name: String) async throws -> PairRequestStarted {
        try await send(request("POST", "/v1/pair/request",
                               body: ["name": name, "device": Self.deviceMeta]))
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

    // MARK: Devices

    /// `GET /v1/devices`: every device the hub has paired, each as its
    /// `device_view` row (name, kind, last_seen_at, revoked flag).
    func devices() async throws -> [PairedDevice] {
        try await send(request("GET", "/v1/devices"), as: DevicesResponse.self).devices
    }

    /// `POST /v1/devices/revoke {device_id}`: the device can't use its
    /// credential again and would have to pair fresh. Removing the caller's
    /// own row takes `confirm_this_device: true`, or the hub refuses with
    /// 409 `confirm_this_device`; the Remove dialog is that confirmation.
    func revokeDevice(id: String, confirmThisDevice: Bool = false) async throws {
        var body: [String: Any] = ["device_id": id]
        if confirmThisDevice { body["confirm_this_device"] = true }
        _ = try await raw(request("POST", "/v1/devices/revoke", body: body))
    }
}
