import Foundation
import Synchronization
import Testing
@testable import SWECore

/// A fake Devin API. Answers by path from `routes`, records every request,
/// and can park the first `/v3/self` call until `release()`.
private final class FakeDevinAPI: HTTPTransport, Sendable {
    private struct State {
        var routes: [String: (Data, Int)] = [:]
        var requests: [URLRequest] = []
        var gateSelf = false
        var parked: CheckedContinuation<Void, Never>?
        /// Set once a request is parked, so `waitUntilParked` never misses it.
        var didPark = false
        var parkWaiter: CheckedContinuation<Void, Never>?
    }

    private let state = Mutex(State())

    var requests: [URLRequest] { state.withLock { $0.requests } }

    func route(_ path: String, _ json: String, status: Int = 200) {
        state.withLock { $0.routes[path] = (Data(json.utf8), status) }
    }

    func gateFirstSelfCall() { state.withLock { $0.gateSelf = true } }

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let url = request.url!
        let key = url.path + (url.query.map { "?" + $0 } ?? "")
        let park = state.withLock { s -> Bool in
            s.requests.append(request)
            guard url.path == "/v3/self", s.gateSelf else { return false }
            s.gateSelf = false
            return true
        }
        if park {
            await withCheckedContinuation { continuation in
                state.withLock { s in
                    // Park and announce it in one locked section.
                    s.parked = continuation
                    s.didPark = true
                    s.parkWaiter?.resume()
                    s.parkWaiter = nil
                }
            }
        }
        let (data, status) = state.withLock { s in
            s.routes[key] ?? s.routes[url.path] ?? (Data("{}".utf8), 404)
        }
        return (data, HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }

    func waitUntilParked() async {
        await withCheckedContinuation { continuation in
            state.withLock { s in
                if s.didPark { continuation.resume() } else { s.parkWaiter = continuation }
            }
        }
    }

    func release() {
        state.withLock { s in
            s.parked?.resume()
            s.parked = nil
        }
    }
}

private func session(_ id: String, title: String, created: Int, updated: Int,
                     status: String = "suspended", detail: String? = "inactivity",
                     pr: String? = nil) -> String {
    let prs = pr.map { #"[{"pr_url":"\#($0)","pr_state":"open"}]"# } ?? "[]"
    let detailJSON = detail.map { "\"\($0)\"" } ?? "null"
    return """
    {"session_id":"\(id)","title":"\(title)","url":"https://app.devin.ai/sessions/\(id)",
     "status":"\(status)","status_detail":\(detailJSON),"created_at":\(created),"updated_at":\(updated),
     "acus_consumed":0.0,"origin":"webapp","user_id":"github|1","pull_requests":\(prs),"tags":[]}
    """
}

private func messages(_ times: [Int]) -> String {
    let items = times.enumerated().map { i, t in
        #"{"event_id":"e\#(i)","source":"devin","message":"hidden","created_at":\#(t)}"#
    }
    return #"{"items":[\#(items.joined(separator: ","))],"has_next_page":false,"end_cursor":null}"#
}

private let org = "org-1"
private let selfJSON = #"{"principal_type":"pat_user","user_id":"github|1","org_id":"org-1"}"#

/// Collects what `onStatus` was handed, in order; `first()` waits for one.
private final class Statuses: Sendable {
    private struct State {
        var values: [String?] = []
        var waiter: CheckedContinuation<String?, Never>?
    }
    private let state = Mutex(State())
    var values: [String?] { state.withLock { $0.values } }

    func record(_ error: String?) {
        state.withLock { s in
            s.values.append(error)
            s.waiter?.resume(returning: error)
            s.waiter = nil
        }
    }

    /// The first status, waiting for it when none has come yet. Checked
    /// and registered under one lock, so a status can't slip between.
    func first() async -> String? {
        await withCheckedContinuation { continuation in
            state.withLock { s in
                if let first = s.values.first { continuation.resume(returning: first) } else { s.waiter = continuation }
            }
        }
    }
}

/// Collects what `onChanged` was handed, in order.
private final class Changes: Sendable {
    private let list = Mutex<[[String]]>([])
    var ids: [[String]] { list.withLock { $0 } }
    func record(_ sessions: [DevinCloudSession]) { list.withLock { $0.append(sessions.map(\.id)) } }
}

@Suite struct DevinCloudSyncTests {
    private func sync(_ api: FakeDevinAPI, dir: TempDir, token: String? = "cog_test",
                      orgId: String? = nil, changes: Changes? = nil, statuses: Statuses? = nil,
                      maxPages: Int = 50) -> DevinCloudSync {
        DevinCloudSync(
            directory: dir.url,
            credentials: { token.map { .init(token: $0, orgId: orgId) } },
            transport: api,
            now: { Date(timeIntervalSince1970: 1_792_000_000) },
            onChanged: { changes?.record($0) },
            onStatus: { statuses?.record($0) },
            maxPages: maxPages)
    }

    private func standardRoutes(_ api: FakeDevinAPI) {
        api.route("/v3/self", selfJSON)
        api.route("/v3/organizations/\(org)/sessions", """
        {"items":[\(session("s1", title: "Audit landing page", created: 1_791_000_000, updated: 1_791_500_000,
                            pr: "https://github.com/acme/site/pull/12")),
                  \(session("s2", title: "Wispr notes", created: 1_791_100_000, updated: 1_791_100_900,
                            status: "running", detail: "waiting_for_user"))],
         "has_next_page":false,"end_cursor":null,"total":2}
        """)
        api.route("/v3/organizations/\(org)/sessions/s1/messages", messages([1_791_000_000, 1_791_000_600, 1_791_400_000]))
        api.route("/v3/organizations/\(org)/sessions/s2/messages", messages([1_791_100_000, 1_791_100_900]))
    }

    @Test func syncsOnlyTheTokensOwnSessionsAndStoresThem() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let changes = Changes()
        let sync = sync(api, dir: dir, changes: changes)
        await sync.sync()

        let list = try #require(api.requests.first { $0.url!.path == "/v3/organizations/org-1/sessions" })
        let query = URLComponents(url: list.url!, resolvingAgainstBaseURL: false)!.queryItems ?? []
        #expect(query.contains(URLQueryItem(name: "user_ids", value: "github|1")))
        #expect(query.contains(URLQueryItem(name: "first", value: "200")))
        #expect(api.requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == "Bearer cog_test" })

        let sessions = await sync.sessions
        // Newest activity first: s1's last message is the latest.
        #expect(sessions.map(\.id) == ["s1", "s2"])
        let s1 = sessions[0]
        #expect(s1.title == "Audit landing page")
        #expect(s1.url == "https://app.devin.ai/sessions/s1")
        #expect(s1.repos == ["acme/site"])
        #expect(s1.project == "site")
        #expect(s1.messageTimes.count == 3)
        #expect(s1.span == DateInterval(start: Date(timeIntervalSince1970: 1_791_000_000),
                                        end: Date(timeIntervalSince1970: 1_791_400_000)))
        #expect(changes.ids == [["s1", "s2"]])

        // On disk, without message text.
        let stored = DevinCloudSync.stored(in: dir.url)
        #expect(stored.sessions == sessions)
        #expect(stored.userId == "github|1")
        #expect(stored.orgId == "org-1")
        let raw = try String(contentsOf: dir.url.appending(path: "sessions.json"), encoding: .utf8)
        #expect(!raw.contains("hidden"))
        // Session titles and folders are this user's only.
        #expect(try PrivateFileTests.mode(dir.url.appending(path: "sessions.json")) == 0o600)
        #expect(try PrivateFileTests.mode(dir.url) == 0o700)
    }

    @Test func settingsOrgIdWinsOverTheTokensDefault() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        api.route("/v3/self", selfJSON)
        api.route("/v3/organizations/org-child/sessions", #"{"items":[],"has_next_page":false}"#)
        let sync = sync(api, dir: dir, orgId: "org-child")
        await sync.sync()
        #expect(api.requests.map(\.url!.path).contains("/v3/organizations/org-child/sessions"))
        #expect(await sync.lastError == nil)
    }

    @Test func followsTheCursorAcrossPages() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        api.route("/v3/self", selfJSON)
        api.route("/v3/organizations/\(org)/sessions?first=200&user_ids=github%7C1", """
        {"items":[\(session("s1", title: "One", created: 1_791_000_000, updated: 1_791_000_100))],
         "has_next_page":true,"end_cursor":"c1"}
        """)
        api.route("/v3/organizations/\(org)/sessions?first=200&user_ids=github%7C1&after=c1", """
        {"items":[\(session("s2", title: "Two", created: 1_790_000_000, updated: 1_790_000_100))],
         "has_next_page":false,"end_cursor":null}
        """)
        api.route("/v3/organizations/\(org)/sessions/s1/messages", messages([1_791_000_000]))
        api.route("/v3/organizations/\(org)/sessions/s2/messages", messages([1_790_000_000]))
        let sync = sync(api, dir: dir)
        await sync.sync()
        #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
    }

    @Test func readsMessagesAgainOnlyForSessionsThatChanged() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let changes = Changes()
        let sync = sync(api, dir: dir, changes: changes)
        await sync.sync()
        let firstRound = api.requests.count

        // Nothing moved: no message reads, and no change reported.
        await sync.sync()
        let second = api.requests[firstRound...].map(\.url!.path)
        #expect(!second.contains { $0.hasSuffix("/messages") })
        #expect(changes.ids.count == 1)

        // s2 moved on: only its messages are read again.
        api.route("/v3/organizations/\(org)/sessions", """
        {"items":[\(session("s1", title: "Audit landing page", created: 1_791_000_000, updated: 1_791_500_000,
                            pr: "https://github.com/acme/site/pull/12")),
                  \(session("s2", title: "Wispr notes", created: 1_791_100_000, updated: 1_791_600_000))],
         "has_next_page":false}
        """)
        api.route("/v3/organizations/\(org)/sessions/s2/messages",
                  messages([1_791_100_000, 1_791_100_900, 1_791_590_000]))
        let before = api.requests.count
        await sync.sync()
        let third = api.requests[before...].map(\.url!.path).filter { $0.hasSuffix("/messages") }
        #expect(third == ["/v3/organizations/org-1/sessions/s2/messages"])
        #expect(await sync.sessions.map(\.id) == ["s2", "s1"])
        #expect(changes.ids.count == 2)
    }

    @Test func aSessionTheListingDropsIsKept() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let changes = Changes()
        let sync = sync(api, dir: dir, changes: changes)
        await sync.sync()
        let s2 = try #require(await sync.sessions.first { $0.id == "s2" })

        // Deleted or archived on Devin's side: s2 is no longer listed, and
        // s1 moved on with a new title.
        api.route("/v3/organizations/\(org)/sessions", """
        {"items":[\(session("s1", title: "Audit landing page, take 2", created: 1_791_000_000,
                            updated: 1_791_700_000, pr: "https://github.com/acme/site/pull/12"))],
         "has_next_page":false}
        """)
        api.route("/v3/organizations/\(org)/sessions/s1/messages",
                  messages([1_791_000_000, 1_791_000_600, 1_791_690_000]))
        let before = api.requests.count
        await sync.sync()

        let sessions = await sync.sessions
        #expect(sessions.map(\.id) == ["s1", "s2"])
        #expect(sessions[0].title == "Audit landing page, take 2")
        #expect(sessions[0].messageTimes.last == Date(timeIntervalSince1970: 1_791_690_000))
        #expect(sessions[1] == s2)
        // The kept session's messages are not read again.
        let reads = api.requests[before...].map(\.url!.path).filter { $0.hasSuffix("/messages") }
        #expect(reads == ["/v3/organizations/org-1/sessions/s1/messages"])
        #expect(DevinCloudSync.stored(in: dir.url).sessions == sessions)
        #expect(changes.ids.last == ["s1", "s2"])
    }

    @Test func aFailedSyncKeepsTheStoredSessionsAndSaysWhy() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let sync = sync(api, dir: dir)
        await sync.sync()
        api.route("/v3/self", #"{"title":"Unauthorized","status":401}"#, status: 401)
        await sync.sync()
        #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
        let error = try #require(await sync.lastError)
        #expect(error.contains("401"))
        // A reopened app reads the same thing back.
        let reopened = self.sync(FakeDevinAPI(), dir: dir, token: nil)
        #expect(await reopened.sessions.map(\.id) == ["s1", "s2"])
        #expect(await reopened.lastError == error)
    }

    @Test func everySyncReportsItsOutcome() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let statuses = Statuses()
        let sync = sync(api, dir: dir, statuses: statuses)
        await sync.sync()
        api.route("/v3/self", "{}", status: 403)
        await sync.sync()
        standardRoutes(api)
        await sync.sync()
        #expect(statuses.values.count == 3)
        #expect(statuses.values[0] == nil)
        #expect(statuses.values[1]?.contains("403") == true)
        #expect(statuses.values[2] == nil)
    }

    @Test func aStoredErrorIsReportedAtLaunch() async throws {
        let dir = try TempDir()
        var snapshot = DevinCloudSnapshot()
        snapshot.error = "Devin answered HTTP 500."
        try DevinCloudSync.encoder.encode(snapshot).write(to: dir.url.appending(path: "sessions.json"))
        let statuses = Statuses()
        let sync = sync(FakeDevinAPI(), dir: dir, token: nil, statuses: statuses)
        let task = Task { await sync.keepFresh(every: .seconds(3_600)) }
        let first = await statuses.first()
        task.cancel()
        #expect(first == "Devin answered HTTP 500.")
    }

    @Test func noUserIdImportsNothing() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let sync = sync(api, dir: dir)
        await sync.sync()
        // An answer without the user's id must not fall back to the whole org.
        api.route("/v3/self", #"{"principal_type":"pat_user","org_id":"org-1"}"#)
        let before = api.requests.count
        await sync.sync()
        #expect(api.requests[before...].map(\.url!.path) == ["/v3/self"])
        #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
        #expect(await sync.lastError?.contains("which user") == true)
    }

    @Test func aWalkPastThePageCapFailsAndKeepsTheStore() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let sync = sync(api, dir: dir, maxPages: 1)
        await sync.sync()
        #expect(await sync.sessions.count == 2)
        // The list now says there is more than one page.
        let more = session("s3", title: "New", created: 1_791_900_000, updated: 1_791_900_100)
        api.route("/v3/organizations/\(org)/sessions",
                  #"{"items":[\#(more)],"has_next_page":true,"end_cursor":"c1"}"#)
        await sync.sync()
        #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
        #expect(await sync.lastError?.contains("more pages") == true)
    }

    @Test func messagesPastThePageCapFailTheSync() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        api.route("/v3/organizations/\(org)/sessions/s1/messages",
                  #"{"items":[{"event_id":"e","source":"devin","message":"x","created_at":1791000000}],"has_next_page":true,"end_cursor":"m1"}"#)
        let sync = sync(api, dir: dir, maxPages: 1)
        await sync.sync()
        #expect(await sync.sessions.isEmpty)
        #expect(await sync.lastError?.contains("more pages") == true)
    }

    @Test func aPagePromisingMoreWithoutACursorFailsAndKeepsTheStore() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let sync = sync(api, dir: dir)
        await sync.sync()
        #expect(await sync.sessions.count == 2)
        // Devin says another page exists but hands no cursor to reach it:
        // keeping the partial list would drop the sessions behind it.
        api.route("/v3/organizations/\(org)/sessions",
                  #"{"items":[\#(session("s3", title: "New", created: 1_791_900_000, updated: 1_791_900_100))],"has_next_page":true,"end_cursor":null}"#)
        await sync.sync()
        #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
        #expect(await sync.lastError?.contains("more pages") == true)
    }

    @Test func aMessagePagePromisingMoreWithoutACursorFailsTheSync() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        api.route("/v3/organizations/\(org)/sessions/s1/messages",
                  #"{"items":[{"event_id":"e","source":"devin","message":"x","created_at":1791000000}],"has_next_page":true,"end_cursor":null}"#)
        let sync = sync(api, dir: dir)
        await sync.sync()
        #expect(await sync.sessions.isEmpty)
        #expect(await sync.lastError?.contains("more pages") == true)
    }

    @Test func removingTheTokenClearsTheErrorButKeepsTheSessions() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let statuses = Statuses()
        let token = Mutex<String?>("cog_test")
        let sync = DevinCloudSync(
            directory: dir.url,
            credentials: { token.withLock { $0 }.map { .init(token: $0, orgId: nil) } },
            transport: api, onStatus: { statuses.record($0) })
        await sync.sync()
        api.route("/v3/self", "{}", status: 401)
        await sync.sync()
        #expect(await sync.lastError != nil)
        token.withLock { $0 = nil }
        await sync.sync()
        #expect(await sync.lastError == nil)
        #expect(statuses.values.last! == nil)
        #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
        #expect(DevinCloudSync.stored(in: dir.url).error == nil)
    }

    @Test func anOlderSuccessDoesNotClearANewerFailure() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let statuses = Statuses()
        // Only the first onChanged parks: the newer sync fails at /v3/self
        // and should never get there, but if it does it must not hang.
        let gate = HoldGate()
        let changes = Mutex(0)
        let sync = DevinCloudSync(
            directory: dir.url, credentials: { .init(token: "cog_test", orgId: nil) },
            transport: api,
            onChanged: { _ in
                let first = changes.withLock { n -> Bool in
                    n += 1
                    return n == 1
                }
                if first { try? await gate.pass() }
            },
            onStatus: { statuses.record($0) })
        // The older sync succeeds and parks inside onChanged.
        let older = Task { await sync.sync() }
        #expect(await gate.arrived())
        api.route("/v3/self", "{}", status: 500)
        await sync.sync()
        gate.open()
        await older.value
        #expect(statuses.values.count == 1)
        #expect(statuses.values.first??.contains("500") == true)
        #expect(await sync.lastError?.contains("500") == true)
    }

    /// A sessions.json the app can't read (locked, corrupt) is the only
    /// copy of sessions Devin no longer lists: a sync never writes over it.
    @Test func anUnreadableStoreIsNeverOverwritten() async throws {
        for locked in [true, false] {
            let dir = try TempDir()
            let file = dir.url.appending(path: "sessions.json")
            let bytes = Data(locked ? #"{"sessions":[]}"#.utf8 : "not json".utf8)
            try bytes.write(to: file)
            if locked { try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: file.path) }
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path) }
            let api = FakeDevinAPI()
            standardRoutes(api)
            let sync = sync(api, dir: dir, token: "cog_test")
            await sync.sync()
            #expect(await sync.sessions.map(\.id) == ["s1", "s2"])
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
            #expect(try Data(contentsOf: file) == bytes, "locked: \(locked)")
        }
    }

    @Test func noTokenMeansNoRequests() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        let sync = sync(api, dir: dir, token: nil)
        await sync.sync()
        #expect(api.requests.isEmpty)
        #expect(await sync.lastError == nil)
    }

    @Test func aNewTokenOwnerStartsOver() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        let sync = sync(api, dir: dir)
        await sync.sync()
        // Same session ids under another user must re-read messages.
        api.route("/v3/self", #"{"user_id":"github|2","org_id":"org-1"}"#)
        let before = api.requests.count
        await sync.sync()
        let reads = api.requests[before...].filter { $0.url!.path.hasSuffix("/messages") }
        #expect(reads.count == 2)
        #expect(DevinCloudSync.stored(in: dir.url).userId == "github|2")

        // The old owner's sessions are not kept past the change.
        api.route("/v3/self", #"{"user_id":"github|3","org_id":"org-1"}"#)
        api.route("/v3/organizations/\(org)/sessions", """
        {"items":[\(session("s3", title: "Someone else", created: 1_791_200_000, updated: 1_791_200_500))],
         "has_next_page":false}
        """)
        api.route("/v3/organizations/\(org)/sessions/s3/messages", messages([1_791_200_000]))
        await sync.sync()
        #expect(await sync.sessions.map(\.id) == ["s3"])
    }

    @Test func anOlderSyncThatFinishesLastIsDropped() async throws {
        let dir = try TempDir()
        let api = FakeDevinAPI()
        standardRoutes(api)
        api.gateFirstSelfCall()
        let sync = sync(api, dir: dir)
        let older = Task { await sync.sync() }
        await api.waitUntilParked()
        // The newer sync sees only s2.
        api.route("/v3/organizations/\(org)/sessions", """
        {"items":[\(session("s2", title: "Wispr notes", created: 1_791_100_000, updated: 1_791_100_900))],
         "has_next_page":false}
        """)
        await sync.sync()
        // The older one resumes against the old answer and must not win.
        standardRoutes(api)
        api.release()
        await older.value
        #expect(await sync.sessions.map(\.id) == ["s2"])
        #expect(DevinCloudSync.stored(in: dir.url).sessions.map(\.id) == ["s2"])
    }

    @Test func timestampsInMillisecondsAreRead() {
        #expect(DevinCloud.date(1_791_000_000) == Date(timeIntervalSince1970: 1_791_000_000))
        #expect(DevinCloud.date(1_791_000_000_000) == Date(timeIntervalSince1970: 1_791_000_000))
    }

    @Test func repoFromPullRequestURL() {
        #expect(DevinCloudSync.repo(fromPullRequest: "https://github.com/acme/site/pull/12") == "acme/site")
        #expect(DevinCloudSync.repo(fromPullRequest: "https://github.com/acme") == nil)
    }
}

@Suite struct DevinCloudSessionTests {
    private func session(status: String, detail: String?, times: [Double] = []) -> DevinCloudSession {
        DevinCloudSession(
            id: "s", title: "T", url: "https://app.devin.ai/sessions/s", status: status, statusDetail: detail,
            createdAt: Date(timeIntervalSince1970: 100), updatedAt: Date(timeIntervalSince1970: 900),
            origin: "webapp", repos: [], messageTimes: times.map { Date(timeIntervalSince1970: $0) })
    }

    @Test func statusMapsToTheSessionsVocabulary() {
        #expect(session(status: "running", detail: "working").listStatus == "active")
        #expect(session(status: "running", detail: "waiting_for_user").listStatus == "followup")
        #expect(session(status: "running", detail: "finished").listStatus == "done")
        #expect(session(status: "suspended", detail: "inactivity").listStatus == "done")
        #expect(session(status: "suspended", detail: "usage_limit_exceeded").listStatus == "error")
        #expect(session(status: "suspended", detail: "out_of_credits").listStatus == "error")
        #expect(session(status: "error", detail: nil).listStatus == "error")
        #expect(session(status: "exit", detail: nil).listStatus == "done")
        #expect(session(status: "something_new", detail: nil).listStatus == nil)
    }

    @Test func spanFallsBackToCreatedAndUpdated() {
        #expect(session(status: "exit", detail: nil).span
                == DateInterval(start: Date(timeIntervalSince1970: 100), end: Date(timeIntervalSince1970: 900)))
        #expect(session(status: "exit", detail: nil, times: [200, 300]).span
                == DateInterval(start: Date(timeIntervalSince1970: 200), end: Date(timeIntervalSince1970: 300)))
    }

    @Test func sessionInfoIsACloudRowThatOpensInTheBrowser() {
        let info = session(status: "exit", detail: nil, times: [200, 300]).sessionInfo
        #expect(info.harness == "devin-cloud")
        #expect(info.agent == "Devin")
        #expect(info.resume == nil)
        #expect(info.opensInBrowser)
        #expect(info.lastActiveAt == Date(timeIntervalSince1970: 300))
        #expect(SessionLocation(harness: info.harness, id: info.id) == .cloud)
        #expect(HarnessMark(info.harness).icon == "devin")
        #expect(HarnessMark(info.harness).name == "Devin cloud")
    }

    @Test func sessionInfoKeepsItsWebLinkThroughCoding() throws {
        let info = session(status: "exit", detail: nil).sessionInfo
        let decoded = try JSONDecoder().decode(SessionInfo.self, from: JSONEncoder().encode(info))
        #expect(decoded == info)
        #expect(decoded.opensInBrowser)
    }

    @Test func calendarBlocksSplitOnQuietStretchesAndGroupByRepo() {
        var s = session(status: "exit", detail: nil, times: [1_000, 1_600, 90_000, 90_300])
        let range = DateInterval(start: Date(timeIntervalSince1970: 0), end: Date(timeIntervalSince1970: 200_000))
        let blocks = DevinCloudActivity.blocks(for: [s], in: range, gap: 1_800)
        #expect(blocks.map(\.start.timeIntervalSince1970) == [1_000, 90_000])
        #expect(blocks.map(\.responses) == [2, 2])
        #expect(blocks.allSatisfy { $0.harness == "devin-cloud" && $0.accountHome == "devin-cloud" })
        #expect(blocks.allSatisfy { $0.project.isEmpty })

        s.repos = ["acme/site"]
        let grouped = DevinCloudActivity.blocks(for: [s], in: range, gap: 1_800)
        #expect(grouped.allSatisfy { $0.project == "site" && $0.projectKey == "github:acme/site" })

        // Outside the range: nothing.
        let later = DateInterval(start: Date(timeIntervalSince1970: 300_000), end: Date(timeIntervalSince1970: 400_000))
        #expect(DevinCloudActivity.blocks(for: [s], in: later, gap: 1_800).isEmpty)
    }

    @Test func activityReadsTheStoreFile() async throws {
        let dir = try TempDir()
        var snapshot = DevinCloudSnapshot()
        snapshot.sessions = [session(status: "exit", detail: nil, times: [1_000, 1_100])]
        try DevinCloudSync.encoder.encode(snapshot).write(to: dir.url.appending(path: "sessions.json"))
        let blocks = try await DevinCloudActivity(directory: dir.url).activityBlocks(
            in: DateInterval(start: .init(timeIntervalSince1970: 0), end: .init(timeIntervalSince1970: 5_000)),
            gap: 1_800, force: false)
        #expect(blocks.count == 1)
        // A missing file is no sessions, not an error.
        let empty = try TempDir()
        #expect(try await DevinCloudActivity(directory: empty.url).activityBlocks(
            in: DateInterval(start: .init(timeIntervalSince1970: 0), end: .init(timeIntervalSince1970: 5_000)),
            gap: 1_800, force: false).isEmpty)
    }

    @Test func accountLabelIsNotAFolder() {
        let directory = AccountDirectory.bare
        #expect(directory.label(for: DevinCloud.accountHome) == "Devin cloud")
    }
}

@MainActor
@Suite struct DevinCloudSessionsRowsTests {
    @Test func cloudSessionsJoinTheListAsCloudRows() async throws {
        let now = Date(timeIntervalSince1970: 1_791_700_000)
        // A harness the ledger doesn't read, so its missing usage hides nothing.
        let local = SessionInfo(id: "a", harness: "gemini", agent: "Gemini", cwd: "/c/quiver", title: "Local",
                                titleSource: nil, lastActiveAt: now.addingTimeInterval(-60), status: nil, resume: nil)
        let model = SessionsModel(source: FakeSessionSource(list: SessionList(sessions: [local], failures: [])),
                                  ledger: FakeSessionLedger(), now: { now })
        model.hideTrivial = true
        model.ledgerScanned = true
        await model.load()
        let cloud = DevinCloudSession(
            id: "s1", title: "Audit landing page", url: "https://app.devin.ai/sessions/s1", status: "suspended",
            statusDetail: "inactivity", createdAt: now.addingTimeInterval(-90_000),
            updatedAt: now.addingTimeInterval(-10), origin: "webapp", repos: ["acme/site"],
            messageTimes: [now.addingTimeInterval(-86_400), now.addingTimeInterval(-30)])
        model.updateCloud([cloud])

        #expect(model.rows.map(\.id) == ["devin-cloud|s1", "a"])
        let row = model.rows[0]
        #expect(row.location == .cloud)
        #expect(row.usage == nil)
        #expect(row.duration == 86_370)
        #expect(row.session.status == "done")
        #expect(row.session.webURL == "https://app.devin.ai/sessions/s1")
        #expect(model.project(for: row.session) == "site")
        // No usage is not "accidental" for a harness the ledger doesn't carry.
        #expect(model.visible.map(\.id) == ["devin-cloud|s1", "a"])
        #expect(model.harnesses == ["devin-cloud", "gemini"])

        model.noteCloudSync(error: "Devin answered HTTP 500.")
        #expect(model.cloudError == "Devin answered HTTP 500.")
        model.noteCloudSync(error: nil)
        #expect(model.cloudError == nil)

        // A new list from swe keeps the cloud rows.
        await model.load()
        #expect(model.rows.map(\.id) == ["devin-cloud|s1", "a"])
        model.updateCloud([])
        #expect(model.rows.map(\.id) == ["a"])
    }
}
