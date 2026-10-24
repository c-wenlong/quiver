import Foundation
import Observation

enum Connection: Equatable { case connecting, online, offline }

/// App-wide state: the paired hubs, the sessions and inbox of the one
/// on screen, and the pairing flows that get a device there.
@MainActor @Observable
final class AppModel {
    /// Every hub this phone is paired with; `hub` is the one on screen.
    private(set) var hubs: HubList
    var hub: PairedHub? { hubs.active }
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
    @ObservationIgnored private let store: HubStore

    init(store: HubStore = KeychainHubStore()) {
        self.store = store
        hubs = store.load()
    }

    /// A model on `hub` without reading the Keychain, for tests.
    convenience init(hub: PairedHub?) {
        var list = HubList()
        if let hub { list.adopt(hub) }
        self.init(store: MemoryHubStore(list))
    }

    var client: HubClient? {
        hub.map { HubClient(baseURL: $0.baseURL, credential: $0.credential.credential) }
    }

    var hubName: String {
        machines.first?.displayName ?? hub.map { hubs.name(of: $0) } ?? "Hub"
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
        let pinMark = pins.mark
        do {
            async let s = client.sessions()
            async let i = client.inbox()
            let (sessionsBody, inboxBody) = try await (s, i)
            guard hub == pairedAtStart else { return }
            let fetched = pins.overlay(sessionsBody.sessions, fetchedFrom: pinMark)
            sessions = fetched
            let now = Date()
            pendingDetaches = pendingDetaches.filter { key, at in
                now.timeIntervalSince(at) < Self.detachWaitCap && fetched.first { $0.key == key }?.isAttached != false
            }
            machines = sessionsBody.machines ?? machines
            rememberName(machines.first?.displayName, of: pairedAtStart)
            inbox = inboxBody.items
            refreshedAt = Date()
            connection = .online
            loadError = nil
        } catch let error as HubError {
            guard hub == pairedAtStart else { return }
            if error.isUnauthorized { unpair(from: client); return }
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
            var row = r
            row.status = "approval"
            return row
        }
    }

    func session(_ key: String) -> SessionRow? { sessions.first { $0.key == key } }

    // MARK: Row actions

    /// `when_idle` detaches the hub is still waiting on, by key, with when
    /// they were asked for. One clears when the row stops being attached,
    /// when its `command.result` arrives in an open chat, or after the
    /// hub's 30 min cap.
    private(set) var pendingDetaches: [String: Date] = [:]
    static let detachWaitCap: TimeInterval = 31 * 60
    /// Pins chosen here that a list fetched too early must not undo.
    private(set) var pins = PinLedger()

    func isDetachPending(_ key: String, now: Date = Date()) -> Bool {
        guard let at = pendingDetaches[key] else { return false }
        return now.timeIntervalSince(at) < Self.detachWaitCap
    }

    /// The detach a chat heard the result of (`command.result`, op detach).
    func detachSettled(_ key: String) { pendingDetaches[key] = nil }

    /// Runs `call` against the hub paired now. Nil when a re-pair landed
    /// while it was out: its answer, or its error, belongs to a hub this
    /// phone no longer shows.
    func onSameHub<T>(_ call: (HubClient) async throws -> T) async throws -> T? {
        guard let client, let start = hub else { return nil }
        do {
            let value = try await call(client)
            return hub == start ? value : nil
        } catch {
            guard hub == start else { return nil }
            throw error
        }
    }

    /// Pins or unpins at once, then tells the hub. Returns what to say
    /// when the hub refused it and the row went back. `animate` wraps the
    /// moves between sections, both ways.
    func setPinned(_ key: String, _ pinned: Bool,
                   animate: (() -> Void) -> Void = { $0() }) async -> String? {
        guard let i = sessions.firstIndex(where: { $0.key == key }) else { return nil }
        let before = sessions[i]
        let version = pins.choose(key, pinned: pinned, now: Date().timeIntervalSince1970)
        animate {
            sessions[i].pinned = pinned
            sessions[i].pinnedAt = pins.choices[key]?.pinnedAt
        }
        do {
            guard let meta = try await onSameHub({ try await $0.pin(key, pinned: pinned) }) else { return nil }
            // A newer choice on this row is still out: this answer is stale.
            if pins.answered(key, version: version) { patch(key, meta: meta) }
            return nil
        } catch {
            guard pins.refused(key, version: version) else { return nil }
            if let j = sessions.firstIndex(where: { $0.key == key }) {
                animate {
                    sessions[j].pinned = before.pinned
                    sessions[j].pinnedAt = before.pinnedAt
                }
            }
            let reason = (error as? HubError)?.message ?? error.localizedDescription
            return "\(pinned ? "Couldn't pin it" : "Couldn't unpin it"). \(reason)"
        }
    }

    /// An empty `title` goes back to the harness's own name.
    func rename(_ key: String, title: String) async throws {
        guard let meta = try await onSameHub({ try await $0.rename(key, title: title) }) else { return }
        patch(key, meta: meta)
    }

    /// Returns true when the hub queued it for a satellite (202).
    @discardableResult
    func attach(_ key: String) async throws -> Bool {
        guard let queued = try await onSameHub({ try await $0.control(key, op: "attach") }) else { return false }
        // An attach also cancels a detach waiting on the turn.
        pendingDetaches[key] = nil
        if !queued { patch(key, ownership: "attached") }
        return queued
    }

    enum DetachOutcome: Equatable {
        /// Handed back already.
        case done
        /// A `when_idle` detach the hub runs once it sees the turn over.
        case waiting
        /// Forwarded to a satellite, which reports back later.
        case forwarded
        /// The phone paired with another hub while it was out.
        case stale
    }

    func detach(_ key: String, mode: DetachMode) async throws -> DetachOutcome {
        guard let accepted = try await onSameHub({ try await $0.detach(key, mode: mode) }) else { return .stale }
        if mode == .whenIdle {
            pendingDetaches[key] = Date()
            return .waiting
        }
        // A now or keep detach replaces a waiting one.
        pendingDetaches[key] = nil
        if accepted { return .forwarded }
        patch(key, ownership: "observed")
        return .done
    }

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
        if let status { sessions[i].status = status }
        if let ownership {
            sessions[i].ownership = ownership
            // Handed back: a detach waiting for the turn to end is done.
            if ownership != "attached" { pendingDetaches[key] = nil }
        }
    }

    /// A pin or rename answered or announced (`session.meta`).
    func patch(_ key: String, meta: SessionMeta) {
        guard let i = sessions.firstIndex(where: { $0.key == key }) else { return }
        sessions[i].apply(pins.overlay(meta, key: key))
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

    /// A newly paired hub joins the list (or replaces the one at the same
    /// address) and goes on screen; the other hubs stay paired.
    func adopt(_ hub: PairedHub) {
        store.saveCredential(hub)
        hubs.adopt(hub)
        store.saveIndex(hubs)
        pairing = PairingFlow()
        showHub()
    }

    /// Puts another paired hub on screen.
    func switchHub(to id: String) {
        guard id != hub?.id, hubs.hubs.contains(where: { $0.id == id }) else { return }
        hubs.activeId = id
        store.saveIndex(hubs)
        showHub()
    }

    /// Unpairs the hub on screen and shows the next one, if any. With
    /// `client`, only if that client still talks to the hub on screen: a
    /// refused answer from a hub this phone has switched away from must
    /// not unpair the one it switched to.
    func unpair(from client: HubClient? = nil) {
        guard let current = hub else { return }
        if let client, client.baseURL != current.baseURL || client.credential != current.credential.credential { return }
        store.deleteCredential(current.id)
        hubs.remove(current.id)
        store.saveIndex(hubs)
        if hub == nil {
            stopRefreshing()
            clearHubState()
        } else {
            showHub()
        }
    }

    /// Starts afresh on the hub now on screen: nothing from the last one
    /// may show, and the list loads again.
    private func showHub() {
        stopRefreshing()
        clearHubState()
        connection = .connecting
        startRefreshing()
    }

    private func clearHubState() {
        sessions = []
        machines = []
        inbox = []
        loadError = nil
        refreshedAt = nil
        pendingDetaches = [:]
        pins = PinLedger()
        dropChats()
        loadedOnce = false
    }

    /// Keeps the machine name a hub reports, so the menu can name it while
    /// another hub is on screen.
    private func rememberName(_ name: String?, of paired: PairedHub?) {
        guard let name, !name.isEmpty, let paired, hubs.names[paired.id] != name else { return }
        hubs.names[paired.id] = name
        store.saveIndex(hubs)
    }

    /// Opens pairing from the hub menu: an empty form, since the address
    /// filled in would be the hub already paired.
    func beginPairingAnother() {
        pairing = PairingFlow(host: "")
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
    var host: String = KeychainHubStore.lastHost ?? ""
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
