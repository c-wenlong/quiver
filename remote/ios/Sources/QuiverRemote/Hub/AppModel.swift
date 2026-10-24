import Foundation
import Observation

enum Connection: Equatable { case connecting, online, offline }

/// App-wide state: the paired hub, its sessions and inbox, and the
/// pairing flows that get a device there.
@MainActor @Observable
final class AppModel {
    private(set) var hub: PairedHub?
    private(set) var sessions: [SessionRow] = []
    private(set) var machines: [Machine] = []
    private(set) var inbox: [InboxItem] = []
    private(set) var connection: Connection = .connecting
    private(set) var loadError: String?
    private(set) var loadedOnce = false
    /// When the lists last loaded; an open search reruns when it moves.
    private(set) var refreshedAt: Date?
    var pairing = PairingFlow()

    private var refreshTask: Task<Void, Never>?

    init() {
        hub = PairingStore.load()
    }

    var client: HubClient? {
        hub.map { HubClient(baseURL: $0.baseURL, credential: $0.credential.credential) }
    }

    var hubName: String {
        machines.first?.displayName ?? hub?.baseURL.host ?? "Hub"
    }

    // MARK: Refresh

    /// Polls sessions and inbox every 15 s while the app is in front,
    /// the same cadence as the PWA.
    func startRefreshing() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }

    func stopRefreshing() {
        refreshTask?.cancel()
        refreshTask = nil
    }

    func refresh() async {
        guard let client else { return }
        // A re-pair can land while this is in flight; answers from the old
        // hub must not overwrite (or unpair) the new one.
        let pairedAtStart = hub
        do {
            async let s = client.sessions()
            async let i = client.inbox()
            let (sessionsBody, inboxBody) = try await (s, i)
            guard hub == pairedAtStart else { return }
            sessions = sessionsBody.sessions
            machines = sessionsBody.machines ?? machines
            inbox = inboxBody.items
            refreshedAt = Date()
            connection = .online
            loadError = nil
        } catch let error as HubError {
            guard hub == pairedAtStart else { return }
            if error.isUnauthorized { unpair(); return }
            connection = .offline
            loadError = error.message
        } catch {
            guard hub == pairedAtStart else { return }
            connection = .offline
        }
        loadedOnce = true
    }

    /// Sessions as the list shows them: a session with an approval open is
    /// "approval" even while its transcript status still says active (the
    /// hub marks that only on inbox items).
    var displaySessions: [SessionRow] {
        let waiting = Set(inbox.filter { $0.sessionStatus == .approval }.map(\.key))
        guard !waiting.isEmpty else { return sessions }
        return sessions.map { r in
            guard waiting.contains(r.key) else { return r }
            return SessionRow(key: r.key, harness: r.harness, agent: r.agent, cwd: r.cwd, title: r.title,
                              lastActiveAt: r.lastActiveAt, status: "approval", machineId: r.machineId,
                              ownership: r.ownership, attachable: r.attachable, remote: r.remote)
        }
    }

    func session(_ key: String) -> SessionRow? { sessions.first { $0.key == key } }

    // MARK: Chats

    /// Chats opened recently, newest last. Going back into one shows what
    /// it already had and replays only the events since, instead of
    /// fetching and laying out the transcript again.
    private var chats: [ChatModel] = []
    static let chatCacheSize = 8

    func chat(_ key: String) -> ChatModel {
        if let i = chats.firstIndex(where: { $0.key == key }) {
            let chat = chats.remove(at: i)
            chats.append(chat)
            return chat
        }
        let chat = ChatModel(key: key, app: self)
        chats.append(chat)
        if chats.count > Self.chatCacheSize { chats.removeFirst().stop() }
        return chat
    }

    /// A session's status or ownership changed on the live stream; patch
    /// the cached row so the list agrees before the next poll.
    func patch(_ key: String, status: String? = nil, ownership: String? = nil) {
        guard let i = sessions.firstIndex(where: { $0.key == key }) else { return }
        let r = sessions[i]
        sessions[i] = SessionRow(key: r.key, harness: r.harness, agent: r.agent, cwd: r.cwd, title: r.title,
                                 lastActiveAt: r.lastActiveAt, status: status ?? r.status, machineId: r.machineId,
                                 ownership: ownership ?? r.ownership, attachable: r.attachable, remote: r.remote)
    }

    // MARK: Inbox

    /// Items still waiting on you. The hub's inbox has no age cutoff, so
    /// follow-ups past `Staleness.window` don't count here.
    var waitingOnYou: [InboxItem] {
        inbox.filter {
            ($0.sessionStatus == .approval || ($0.sessionStatus == .followup && Staleness.isFresh($0.lastActiveAt)))
                && !dismissals.isDismissed($0.key, at: $0.lastActiveAt)
        }
    }

    /// Inbox rows cleared on this phone; shared by the Inbox page and the
    /// tab badge so both agree.
    let dismissals = InboxDismissals()

    var inboxBadge: Int { waitingOnYou.count }

    // MARK: Pairing

    func adopt(_ hub: PairedHub) {
        PairingStore.save(hub)
        self.hub = hub
        sessions = []
        inbox = []
        dropChats()
        loadedOnce = false
        connection = .connecting
        pairing = PairingFlow()
        startRefreshing()
    }

    func unpair() {
        stopRefreshing()
        PairingStore.clear()
        hub = nil
        sessions = []
        machines = []
        inbox = []
        dropChats()
        loadedOnce = false
    }

    private func dropChats() {
        for chat in chats { chat.stop() }
        chats = []
    }

    /// `quiver://pair` deep link or a scanned QR.
    func handle(_ url: URL) async {
        guard let link = PairLink(url) else { return }
        await pair(with: link)
    }

    func pair(with link: PairLink) async {
        pairing.busy = true
        pairing.error = nil
        defer { pairing.busy = false }
        let client = HubClient(baseURL: link.baseURL, credential: nil)
        do {
            let cred = try await client.pair(token: link.token, name: link.name ?? pairing.deviceName)
            adopt(PairedHub(baseURL: link.baseURL, credential: cred))
        } catch {
            pairing.error = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    func pair(token: String) async {
        guard let base = hubURL(from: pairing.host) else {
            pairing.error = "Enter the hub's Tailscale name or address."
            return
        }
        pairing.busy = true
        pairing.error = nil
        defer { pairing.busy = false }
        do {
            let cred = try await HubClient(baseURL: base, credential: nil)
                .pair(token: token.trimmingCharacters(in: .whitespaces), name: pairing.deviceName)
            adopt(PairedHub(baseURL: base, credential: cred))
        } catch {
            pairing.error = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    /// Tailnet-identity pairing: ask the hub for a code, then poll until
    /// another device approves or denies it.
    func startTailscalePairing() async {
        guard let base = hubURL(from: pairing.host) else {
            pairing.error = "Enter the hub's Tailscale name or address."
            return
        }
        pairing.busy = true
        pairing.error = nil
        let client = HubClient(baseURL: base, credential: nil)
        do {
            let started = try await client.startPairRequest(name: pairing.deviceName)
            pairing.busy = false
            pairing.pending = PendingPairing(baseURL: base, started: started,
                                             expiresAt: Date().addingTimeInterval(TimeInterval(started.expiresInS ?? 300)))
            await pollPairing(client: client, started: started)
        } catch {
            pairing.busy = false
            pairing.error = (error as? HubError)?.message ?? error.localizedDescription
        }
    }

    private func pollPairing(client: HubClient, started: PairRequestStarted) async {
        while pairing.pending?.started.requestId == started.requestId {
            try? await Task.sleep(for: .seconds(2))
            guard pairing.pending?.started.requestId == started.requestId else { return }
            do {
                let poll = try await client.pollPairRequest(id: started.requestId, secret: started.pollSecret)
                // Cancelled, or replaced by a new request, while that poll
                // was out: its answer belongs to nothing on screen now.
                guard pairing.pending?.started.requestId == started.requestId else { return }
                switch poll.status {
                case "approved":
                    guard let deviceId = poll.deviceId, let credential = poll.credential else { continue }
                    adopt(PairedHub(baseURL: client.baseURL,
                                    credential: Credential(deviceId: deviceId, credential: credential, name: poll.name)))
                    return
                case "denied":
                    pairing.pending?.outcome = .denied
                    return
                default:
                    if let s = poll.expiresInS { pairing.pending?.expiresAt = Date().addingTimeInterval(TimeInterval(s)) }
                }
            } catch let error as HubError where error.status == 404 {
                guard pairing.pending?.started.requestId == started.requestId else { return }
                pairing.pending?.outcome = .expired
                return
            } catch {
                // A dropped poll is retried; the countdown says when to give up.
            }
        }
    }

    func cancelPairing() {
        pairing.pending = nil
    }
}

struct PairingFlow {
    var host: String = PairingStore.lastHost ?? ""
    var deviceName: String = PairingFlow.defaultDeviceName
    var busy = false
    var error: String?
    var pending: PendingPairing?

    static var defaultDeviceName: String { "iPhone" }
}

struct PendingPairing {
    enum Outcome { case denied, expired }
    let baseURL: URL
    let started: PairRequestStarted
    var expiresAt: Date
    var outcome: Outcome?

    /// "482913" shown as "482 913".
    var spacedCode: String {
        let c = started.code
        guard c.count == 6 else { return c }
        return "\(c.prefix(3)) \(c.suffix(3))"
    }
}

// MARK: Grouping

struct SessionGroup: Identifiable {
    let id: String
    let title: String
    let rows: [SessionRow]
}

enum SessionFilter: String, CaseIterable, Identifiable {
    case all, active, needsYou
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: "All"
        case .active: "Active"
        case .needsYou: "Needs you"
        }
    }

    func includes(_ row: SessionRow) -> Bool {
        switch self {
        case .all: true
        case .active: row.sessionStatus == .active
        case .needsYou: row.needsYouNow || (row.sessionStatus == .error && Staleness.isFresh(row.lastActiveAt))
        }
    }
}

enum SessionGrouping {
    /// Sessions active in the last day go under their project folder;
    /// everything older folds into "Earlier", newest first.
    static func groups(_ rows: [SessionRow], machines: [Machine], now: Double = Date().timeIntervalSince1970) -> [SessionGroup] {
        let recentCutoff = now - 86_400
        let sorted = rows.sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
        let multiMachine = Set(rows.map { $0.machineId ?? "local" }).count > 1
        // Keyed by machine and full folder; only the heading is shortened.
        var order: [String] = []
        var buckets: [String: [SessionRow]] = [:]
        var earlier: [SessionRow] = []
        for row in sorted {
            let isLive = row.sessionStatus == .active || row.needsYouNow
            guard isLive || (row.lastActiveAt ?? 0) >= recentCutoff else { earlier.append(row); continue }
            let key = "\(row.machineId ?? "local")|\(row.cwd ?? "")"
            if buckets[key] == nil { order.append(key) }
            buckets[key, default: []].append(row)
        }
        // Two folders that shorten alike ("a/projects/api", "b/projects/api")
        // keep their full paths so the headings differ.
        let short = Dictionary(grouping: order) { shortDir(buckets[$0]![0].cwd) }
        var out = order.map { key -> SessionGroup in
            let row = buckets[key]![0]
            var title = shortDir(row.cwd)
            if (short[title]?.count ?? 0) > 1 { title = abbreviateHome(row.cwd ?? title) }
            if multiMachine {
                let m = machines.first { $0.id == row.machineId }?.displayName ?? row.machineId ?? "local"
                title = "\(m) · \(title)"
            }
            return SessionGroup(id: key, title: title, rows: buckets[key]!)
        }
        if !earlier.isEmpty { out.append(SessionGroup(id: "earlier", title: "Earlier", rows: earlier)) }
        return out
    }

    static func abbreviateHome(_ path: String) -> String {
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        // /Users/<name>/... and /home/<name>/... read as ~/...
        if parts.count > 3, parts[1] == "Users" || parts[1] == "home" {
            return "~/" + parts[3...].joined(separator: "/")
        }
        return path
    }

    /// The last two path components, as the PWA shows them.
    static func shortDir(_ path: String?) -> String {
        guard let path, !path.isEmpty else { return "No folder" }
        let parts = path.split(separator: "/")
        return parts.suffix(2).joined(separator: "/")
    }
}

enum RelativeTime {
    static func short(_ ts: Double?, now: Double = Date().timeIntervalSince1970) -> String {
        guard let ts, ts > 0 else { return "" }
        let s = max(0, now - ts)
        if s < 60 { return "now" }
        if s < 3600 { return "\(Int(s / 60))m" }
        if s < 86_400 { return "\(Int(s / 3600))h" }
        return "\(Int(s / 86_400))d"
    }
}
