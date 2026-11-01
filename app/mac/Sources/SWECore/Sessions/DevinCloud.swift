import Foundation
import os

/// Devin cloud sessions, read through Devin's REST API (v3) with the
/// personal access token saved in Settings › Services. Only the sessions
/// the token's own user started are imported: an organization also holds
/// teammates' sessions and automation runs, which are not this user's work.
///
/// The API reports no tokens, and `acus_consumed` reads 0 on plans that
/// don't meter per session, so these sessions carry no usage. They stay
/// out of the usage ledger: zero-token events there would count as
/// responses on the Overview and as accidental sessions under the
/// "Hide accidental sessions" rule. The Sessions page and the calendar
/// read them from this store instead.
public enum DevinCloud {
    /// The harness id cloud sessions show under. Separate from `devin`
    /// (the CLI, scanned from its local database), so the CLI's
    /// missing-usage rule never applies to them.
    public static let harness = "devin-cloud"
    /// The calendar's account dim for cloud sessions; there is no folder.
    public static let accountHome = "devin-cloud"
    static let baseURL = URL(string: "https://api.devin.ai")!

    /// The directory SWE uses when the environment doesn't say.
    public static func defaultDirectory(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        home.appending(path: ".swe/data/devin-cloud")
    }

    /// `SWE_DEVIN_DATA_DIR` wins (`QUIVER_DEVIN_DATA_DIR` kept as a
    /// fallback), so a test build never touches the real data.
    public static func resolvedDirectory(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> URL {
        let environment = ProcessInfo.processInfo.environment
        if let override = environment["SWE_DEVIN_DATA_DIR"] ?? environment["QUIVER_DEVIN_DATA_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: override)
        }
        return defaultDirectory(home: home)
    }

    /// The API's integer timestamps are epoch seconds; anything past 1e12
    /// is taken as milliseconds, in case an endpoint switches unit.
    static func date(_ value: Double) -> Date {
        Date(timeIntervalSince1970: value > 1e12 ? value / 1000 : value)
    }
}

/// One cloud session as stored in `sessions.json`. Message text is never
/// stored: the times are all the calendar needs.
public struct DevinCloudSession: Codable, Sendable, Equatable, Identifiable {
    public var id: String
    public var title: String?
    /// The session in Devin's web app.
    public var url: String?
    /// `new`, `claimed`, `running`, `exit`, `error`, `suspended`, `resuming`.
    public var status: String
    /// Why it is running or suspended: `working`, `waiting_for_user`,
    /// `inactivity`, `usage_limit_exceeded`, ...
    public var statusDetail: String?
    public var createdAt: Date
    /// When the session last changed, which includes going to sleep long
    /// after the last message; `messageTimes` says when work happened.
    public var updatedAt: Date
    /// Where it was started: `webapp`, `desktop`, `slack`, `api`, ...
    public var origin: String?
    /// "owner/repo" of each pull request it opened, first seen first.
    public var repos: [String]
    /// Every message's time, user and Devin alike, earliest first.
    public var messageTimes: [Date]

    public init(id: String, title: String?, url: String?, status: String, statusDetail: String?,
                createdAt: Date, updatedAt: Date, origin: String?, repos: [String], messageTimes: [Date]) {
        self.id = id
        self.title = title
        self.url = url
        self.status = status
        self.statusDetail = statusDetail
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.origin = origin
        self.repos = repos
        self.messageTimes = messageTimes
    }

    /// From the first to the last message, or created to updated when no
    /// message was read.
    public var span: DateInterval {
        let start = messageTimes.first ?? createdAt
        return DateInterval(start: start, end: max(start, messageTimes.last ?? updatedAt))
    }

    /// The Sessions page's status vocabulary. A session Devin suspended
    /// for running out of quota is an error; one it put to sleep after
    /// finishing, or that the user stopped, is done.
    public var listStatus: String? {
        switch (status, statusDetail) {
        case ("error", _): "error"
        case ("suspended", "usage_limit_exceeded"), ("suspended", "out_of_credits"): "error"
        case ("running", "waiting_for_user"), ("running", "waiting_for_approval"): "followup"
        case ("running", "finished"), ("exit", _), ("suspended", _): "done"
        case ("new", _), ("claimed", _), ("running", _), ("resuming", _): "active"
        default: nil
        }
    }

    /// The project shown for the session: the repository of its first
    /// pull request, by name.
    public var project: String? {
        repos.first.map { String($0.split(separator: "/").last ?? Substring($0)) }
    }

    public var sessionInfo: SessionInfo {
        var info = SessionInfo(
            id: id, harness: DevinCloud.harness, agent: "Devin", cwd: nil, title: title,
            titleSource: nil, lastActiveAt: span.end, status: listStatus, resume: nil)
        info.webURL = url
        return info
    }
}

/// `sessions.json`: every session the syncs have seen for this user and
/// org, plus why the latest one failed. A session the API stops listing
/// is kept, and a failed sync keeps the sessions it had.
public struct DevinCloudSnapshot: Codable, Sendable, Equatable {
    public var schema = 1
    public var userId: String?
    public var orgId: String?
    public var syncedAt: Date?
    /// The last sync's failure, cleared by the next success.
    public var error: String?
    public var sessions: [DevinCloudSession] = []
    public init() {}
}

/// Keeps `sessions.json` in step with the API: on a timer, and whenever
/// the Services key changes. Each sync lists the user's sessions and reads
/// messages again only for a session whose `updated_at` moved.
public actor DevinCloudSync {
    /// Devin allows 200 per page; 50 pages is 10,000 sessions.
    static let pageSize = 200
    private let maxPages: Int

    public struct Credentials: Sendable {
        public var token: String
        /// Settings' org id; nil uses the one the token reports.
        public var orgId: String?
        public init(token: String, orgId: String?) {
            self.token = token
            self.orgId = orgId
        }
    }

    public let directory: URL
    private let credentials: @Sendable () async -> Credentials?
    private let transport: any HTTPTransport
    private let now: @Sendable () -> Date
    private let onChanged: (@Sendable ([DevinCloudSession]) async -> Void)?
    /// Runs after every sync that got an answer, with its error or nil,
    /// so a page can say why the list stopped updating.
    private let onStatus: (@Sendable (String?) async -> Void)?
    private var snapshot: DevinCloudSnapshot
    /// `sessions.json` was there but couldn't be read or decoded (locked,
    /// corrupt, a newer schema). Saving would replace the only copy of the
    /// archived sessions with whatever this run fetched, so nothing is
    /// written until the app opens a file it can read.
    private let storeUnreadable: Bool
    /// Bumped by every sync; a reply from an older one is dropped, so a
    /// slow sync can't overwrite a newer one's result.
    private var generation = 0
    private static let log = Logger(subsystem: "com.cwenlong.swe", category: "devin-cloud")

    public init(directory: URL,
                credentials: @escaping @Sendable () async -> Credentials?,
                transport: any HTTPTransport,
                now: @escaping @Sendable () -> Date = Date.init,
                onChanged: (@Sendable ([DevinCloudSession]) async -> Void)? = nil,
                onStatus: (@Sendable (String?) async -> Void)? = nil,
                maxPages: Int = 50) {
        self.directory = directory
        self.maxPages = maxPages
        self.credentials = credentials
        self.transport = transport
        self.now = now
        self.onChanged = onChanged
        self.onStatus = onStatus
        PrivateFile.tighten(directory.appending(path: "sessions.json"))
        (self.snapshot, self.storeUnreadable) = Self.read(in: directory)
    }

    /// What `sessions.json` in `directory` holds; empty when missing or unreadable.
    public static func stored(in directory: URL) -> DevinCloudSnapshot {
        read(in: directory).snapshot
    }

    /// The stored snapshot, and whether a file was there that couldn't be
    /// read: missing is a fresh start, unreadable is not.
    private static func read(in directory: URL) -> (snapshot: DevinCloudSnapshot, unreadable: Bool) {
        let file = directory.appending(path: "sessions.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return (DevinCloudSnapshot(), false) }
        guard let data = try? Data(contentsOf: file),
              let snapshot = try? decoder.decode(DevinCloudSnapshot.self, from: data)
        else { return (DevinCloudSnapshot(), true) }
        return (snapshot, false)
    }

    /// The stored sessions, newest activity first.
    public var sessions: [DevinCloudSession] { snapshot.sessions }
    public var lastError: String? { snapshot.error }
    public var syncedAt: Date? { snapshot.syncedAt }

    /// Sync on a timer until cancelled: once at launch, then every
    /// `interval` (15 minutes in the app).
    public func keepFresh(every interval: Duration = .seconds(900)) async {
        // What the last launch stored shows before the first sync answers.
        if !snapshot.sessions.isEmpty { await onChanged?(snapshot.sessions) }
        if let error = snapshot.error { await onStatus?(error) }
        while !Task.isCancelled {
            await sync()
            do { try await Task.sleep(for: interval) } catch { return }
        }
    }

    /// One sync. With no token saved it fetches nothing and keeps the
    /// stored sessions, but drops a past failure: there is nothing left
    /// to fix. Any failure keeps the stored sessions and records why.
    public func sync() async {
        generation += 1
        let mine = generation
        guard let credentials = await credentials(), !credentials.token.isEmpty else {
            guard mine == generation, snapshot.error != nil else { return }
            snapshot.error = nil
            save()
            await onStatus?(nil)
            return
        }
        do {
            let me = try await get(Self.SelfResponse.self, path: "/v3/self", token: credentials.token)
            guard mine == generation else { return }
            // Without the user's id the listing would be the whole org's,
            // teammates and automations included.
            guard let userId = me.userId, !userId.isEmpty else { throw SyncError.noUser }
            guard let orgId = credentials.orgId.flatMap({ $0.isEmpty ? nil : $0 }) ?? me.orgId else {
                throw SyncError.noOrganization
            }
            let listed = try await listSessions(org: orgId, user: userId, token: credentials.token)
            guard mine == generation else { return }
            // A different user or org starts over: the stored sessions
            // belong to whoever the old token named.
            let sameOwner = snapshot.userId == userId && snapshot.orgId == orgId
            let stored = sameOwner
                ? Dictionary(snapshot.sessions.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
                : [:]
            var sessions: [DevinCloudSession] = []
            for item in listed {
                let updatedAt = DevinCloud.date(item.updatedAt)
                var times = stored[item.sessionId].flatMap { $0.updatedAt == updatedAt ? $0.messageTimes : nil }
                if times == nil {
                    times = try await messageTimes(org: orgId, session: item.sessionId, token: credentials.token)
                    guard mine == generation else { return }
                }
                sessions.append(item.session(updatedAt: updatedAt, messageTimes: times ?? []))
            }
            // Sessions the listing no longer returns stay: Devin drops
            // deleted and archived ones, and their history is still real.
            let listedIDs = Set(listed.map(\.sessionId))
            sessions += stored.values.filter { !listedIDs.contains($0.id) }
            sessions.sort { ($0.span.end, $0.id) > ($1.span.end, $1.id) }
            let changed = sessions != snapshot.sessions
            snapshot.userId = userId
            snapshot.orgId = orgId
            snapshot.syncedAt = now()
            snapshot.error = nil
            snapshot.sessions = sessions
            save()
            if changed { await onChanged?(sessions) }
            // A newer sync may have finished, or failed, during onChanged;
            // its outcome is the one to show.
            guard mine == generation else { return }
            await onStatus?(nil)
        } catch {
            guard mine == generation else { return }
            snapshot.error = Self.describe(error)
            Self.log.error("devin cloud sync failed: \(self.snapshot.error ?? "")")
            save()
            await onStatus?(snapshot.error)
        }
    }

    // MARK: - API

    enum SyncError: LocalizedError, Equatable {
        case http(Int)
        case noOrganization
        case noUser
        case incomplete
        var errorDescription: String? {
            switch self {
            case .http(401): "Devin rejected the token (HTTP 401). Check it in Settings › Services."
            case .http(403): "The token can't read this organization's sessions (HTTP 403)."
            case .http(let code): "Devin answered HTTP \(code)."
            case .noOrganization: "No Devin organization id. Add it in Settings › Services."
            case .noUser: "Devin didn't say which user the token belongs to, so nothing was imported."
            case .incomplete: "Devin returned more pages than SWE reads; the stored sessions were kept."
            }
        }
    }

    static func describe(_ error: Error) -> String {
        (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }

    struct SelfResponse: Decodable {
        var userId: String?
        var orgId: String?
    }

    struct Page<Item: Decodable>: Decodable {
        var items: [Item]
        var hasNextPage: Bool?
        var endCursor: String?
    }

    struct SessionItem: Decodable {
        struct PullRequest: Decodable { var prUrl: String? }
        var sessionId: String
        var title: String?
        var url: String?
        var status: String
        var statusDetail: String?
        var createdAt: Double
        var updatedAt: Double
        var origin: String?
        var pullRequests: [PullRequest]?

        func session(updatedAt: Date, messageTimes: [Date]) -> DevinCloudSession {
            var repos: [String] = []
            for pr in pullRequests ?? [] {
                if let repo = pr.prUrl.flatMap(DevinCloudSync.repo(fromPullRequest:)), !repos.contains(repo) {
                    repos.append(repo)
                }
            }
            return DevinCloudSession(
                id: sessionId, title: title, url: url, status: status, statusDetail: statusDetail,
                createdAt: DevinCloud.date(createdAt), updatedAt: updatedAt, origin: origin,
                repos: repos, messageTimes: messageTimes)
        }
    }

    struct MessageItem: Decodable {
        var createdAt: Double
    }

    /// "owner/repo" out of a GitHub-style pull request URL.
    static func repo(fromPullRequest url: String) -> String? {
        guard let parts = URL(string: url)?.pathComponents.filter({ $0 != "/" }),
              parts.count >= 2 else { return nil }
        return "\(parts[0])/\(parts[1])"
    }

    /// Every page, or `incomplete` past `maxPages`: a partial list would
    /// drop the sessions it didn't reach from the store. A page that says
    /// more follow but hands no cursor is incomplete the same way: the
    /// sessions behind it cannot be reached.
    private func listSessions(org: String, user: String, token: String) async throws -> [SessionItem] {
        var items: [SessionItem] = []
        var after: String?
        for _ in 0..<maxPages {
            var query = [URLQueryItem(name: "first", value: String(Self.pageSize)),
                         URLQueryItem(name: "user_ids", value: user)]
            if let after { query.append(URLQueryItem(name: "after", value: after)) }
            let page = try await get(Page<SessionItem>.self,
                                     path: "/v3/organizations/\(org)/sessions", query: query, token: token)
            items += page.items
            guard page.hasNextPage == true else { return items }
            guard let next = page.endCursor else { throw SyncError.incomplete }
            after = next
        }
        throw SyncError.incomplete
    }

    private func messageTimes(org: String, session: String, token: String) async throws -> [Date] {
        var times: [Date] = []
        var after: String?
        for _ in 0..<maxPages {
            var query = [URLQueryItem(name: "first", value: String(Self.pageSize))]
            if let after { query.append(URLQueryItem(name: "after", value: after)) }
            let page = try await get(Page<MessageItem>.self,
                                     path: "/v3/organizations/\(org)/sessions/\(session)/messages",
                                     query: query, token: token)
            times += page.items.map { DevinCloud.date($0.createdAt) }
            guard page.hasNextPage == true else { return times.sorted() }
            guard let next = page.endCursor else { throw SyncError.incomplete }
            after = next
        }
        // Partial times would be reused until the session changes again.
        throw SyncError.incomplete
    }

    private func get<T: Decodable>(_ type: T.Type, path: String, query: [URLQueryItem] = [],
                                   token: String) async throws -> T {
        var components = URLComponents(url: DevinCloud.baseURL.appending(path: path),
                                       resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }
        var request = URLRequest(url: components.url!)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await transport.send(request)
        guard (200..<300).contains(response.statusCode) else { throw SyncError.http(response.statusCode) }
        return try Self.apiDecoder.decode(T.self, from: data)
    }

    // MARK: - Storage

    private static let apiDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return decoder
    }()

    static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }()

    private func save() {
        guard !storeUnreadable else {
            Self.log.error("devin cloud: sessions.json couldn't be read, so it is left as it is")
            return
        }
        do {
            // Session titles and folders: this user's only.
            try PrivateFile.write(Self.encoder.encode(snapshot),
                                  to: directory.appending(path: "sessions.json"))
        } catch {
            Self.log.error("devin cloud: couldn't save sessions.json: \(error.localizedDescription)")
        }
    }
}

/// The calendar's view of the cloud sessions: one block per run of
/// messages no more than `gap` apart, so a session left open for days
/// shows on the days work happened, not as one long bar.
///
/// It reads `sessions.json` itself rather than asking the sync, so the
/// calendar can be built before the sync that reloads it.
public struct DevinCloudActivity: ActivityProviding {
    private let directory: URL

    public init(directory: URL) {
        self.directory = directory
    }

    public func activityBlocks(in range: DateInterval, gap: TimeInterval,
                               force: Bool) async throws -> [ActivityBlock] {
        Self.blocks(for: DevinCloudSync.stored(in: directory).sessions, in: range, gap: gap)
    }

    static func blocks(for sessions: [DevinCloudSession], in range: DateInterval,
                       gap: TimeInterval) -> [ActivityBlock] {
        sessions.flatMap { session -> [ActivityBlock] in
            let times = session.messageTimes.isEmpty ? [session.createdAt] : session.messageTimes
            let blocks = ActivityBlocks.make(
                harness: DevinCloud.harness, accountHome: DevinCloud.accountHome,
                sessionId: session.id, cwd: nil, timestamps: times, range: range, gap: gap)
            // No folder to resolve; a session that opened a pull request
            // groups under that repository.
            guard let project = session.project, let repo = session.repos.first else { return blocks }
            return blocks.map {
                ActivityBlock(harness: $0.harness, accountHome: $0.accountHome, project: project,
                              sessions: $0.sessions, start: $0.start, end: $0.end,
                              responses: $0.responses, projectKey: "github:\(repo)")
            }
        }
    }
}
