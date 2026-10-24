import Foundation
import Observation

/// One entry of a chat transcript, already folded for display: a tool's
/// output sits on its call, an approval carries its outcome.
public struct HubChatItem: Identifiable, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case user(String)
        case assistant(String)
        case thinking(String)
        case tool(HubToolCall, result: String?)
        /// `decision` is nil while the approval is still open.
        case approval(requestId: String, prompt: String, decision: String?, resolved: Bool)
        case compaction
        case system(String)
        case error(String)
    }

    public let id: String
    public var kind: Kind

    public init(id: String, kind: Kind) {
        self.id = id
        self.kind = kind
    }
}

/// The Sessions page's chat: the newest page of history from the hub,
/// older pages on request, then the session's live events. A port of the
/// web app's `static/pages/chat.js` and the iPhone app's `ChatModel`.
@MainActor
@Observable
public final class SessionChatModel {
    public enum Connection: Equatable, Sendable { case connecting, live, reconnecting }

    public let key: String
    public private(set) var items: [HubChatItem] = []
    /// The hub's row for the session: status and ownership, kept current
    /// by the stream.
    public private(set) var row: HubSessionRow?
    public private(set) var connection: Connection = .connecting
    public private(set) var loading = true
    public private(set) var loadError: String?
    /// The hub holds messages older than the oldest one shown.
    public private(set) var hasEarlier = false
    public private(set) var loadingEarlier = false
    /// The hub doesn't list the session (an older one, or one it never
    /// read): the page opens its details instead.
    public private(set) var missing = false

    /// Messages per `/messages` page.
    public static let pageSize = 100

    private let hub: LocalHub
    private var seen = Set<Int>()
    private var cursor = 0
    /// Tool call id to the id of its card.
    private var toolCards: [String: String] = [:]
    /// Tool results whose call is on a page not loaded yet, by call id.
    private var orphanResults: [String: Orphan] = [:]
    /// Stream events up to this seq are history already on screen, replayed
    /// only to find the ask of an approval the hub still waits on.
    private var replayFloor = 0
    /// The seq to page back from: the oldest message loaded.
    private var nextBefore: Int?
    private var streamTask: Task<Void, Never>?
    /// Bumped by every start() and stop(), so a start() still fetching
    /// history doesn't open a stream after the chat was closed.
    private var generation = 0

    struct Orphan: Equatable {
        var seq: Int
        var text: String
    }

    public init(key: String, hub: LocalHub) {
        self.key = key
        self.hub = hub
    }

    /// The agent is mid-turn.
    public var working: Bool { row?.status == "active" }

    /// Loads the row and history, then follows the live stream until
    /// `stop()`.
    public func start() async {
        generation += 1
        let gen = generation
        streamTask?.cancel()
        streamTask = nil
        loading = items.isEmpty
        do {
            let rows = try await hub.withClient { try await $0.sessions() }
            guard gen == generation else { return }
            guard let row = rows.first(where: { $0.key == key }) else {
                missing = true
                throw HubError(status: 404, code: "unknown_session", message: HubError.friendly(
                    code: "unknown_session", fallback: nil, status: 404))
            }
            self.row = row
            let page = try await hub.withClient { [key] in try await $0.messages(key, limit: Self.pageSize) }
            guard gen == generation else { return }
            load(page)
            loadError = nil
            // An approval asked before the newest message isn't in the
            // page or after it (codex journals the ask before its tool
            // call): replay the journal from the start for its ask, taking
            // nothing else from the part already shown.
            if let rid = try? await hub.withClient({ [key] in try await $0.pendingApproval(key) }),
               gen == generation, !hasApproval(rid) {
                items.append(HubChatItem(id: "approval-\(rid)", kind: .approval(
                    requestId: rid, prompt: "Approval requested", decision: nil, resolved: false)))
                replayFloor = cursor
            }
        } catch is CancellationError {
            return
        } catch {
            guard gen == generation else { return }
            loadError = error.localizedDescription
            loading = false
            return
        }
        loading = false
        follow(gen, from: replayFloor > 0 ? 0 : cursor)
    }

    public func stop() {
        generation += 1
        streamTask?.cancel()
        streamTask = nil
        connection = .connecting
    }

    private func follow(_ gen: Int, from last: Int) {
        streamTask?.cancel()
        streamTask = Task { [weak self, key] in
            guard let client = try? await self?.hub.client() else { return }
            for await event in client.events(key, last: last) {
                guard let self, gen == self.generation else { return }
                switch event {
                case .connected: self.connection = .live
                case .disconnected: self.connection = .reconnecting
                case .unauthorized:
                    // Pairs again and reloads; a task of its own, since
                    // start() cancels this one.
                    self.hub.refused(client)
                    self.connection = .reconnecting
                    Task { await self.start() }
                    return
                case .truncated:
                    // The journal dropped events past the cursor and now
                    // replays what it kept, oldest first: show the newest
                    // page instead and follow on from its end.
                    guard let page = try? await client.messages(key, limit: Self.pageSize),
                          gen == self.generation else { continue }
                    self.load(page)
                    self.follow(gen, from: self.cursor)
                    return
                case .event(let env): self.apply(env)
                }
            }
        }
    }

    /// Replaces what's shown with the newest page of history.
    func load(_ page: HubMessagesPage) {
        items = []
        seen = []
        toolCards = [:]
        orphanResults = [:]
        replayFloor = 0
        // Before the messages: whether a result without its call can still
        // find it on an older page.
        hasEarlier = page.hasMore ?? false
        for env in page.messages { apply(env) }
        nextBefore = page.nextBefore ?? page.messages.first?.seq
        showOrphans()
    }

    /// Prepends the page of history just older than what's loaded,
    /// skipping pages that hold only results of calls further back.
    public func loadEarlier() async {
        guard hasEarlier, !loadingEarlier else { return }
        loadingEarlier = true
        defer { loadingEarlier = false }
        while hasEarlier, let before = nextBefore {
            guard let page = try? await hub.withClient({ [key] in
                try await $0.messages(key, limit: Self.pageSize, before: before)
            }) else { return }
            // A truncation reload replaced the history meanwhile.
            guard nextBefore == before else { return }
            if prepend(page) { return }
        }
    }

    /// Puts an older page in front; returns whether it added anything.
    func prepend(_ page: HubMessagesPage) -> Bool {
        var older: [HubChatItem] = []
        var cards: [String: String] = [:]
        for env in page.messages where env.type == "message.appended" {
            guard let seq = env.seq, !seen.contains(seq), let msg = env.decode(HubMessage.self) else { continue }
            seen.insert(seq)
            Self.add(msg, seq: seq, to: &older, cards: &cards, orphans: &orphanResults)
        }
        // A call on this page may still be running; its live result has
        // to find the card.
        toolCards.merge(cards) { newer, _ in newer }
        items.insert(contentsOf: older, at: 0)
        hasEarlier = page.hasMore ?? false
        nextBefore = page.nextBefore ?? page.messages.first?.seq
        let shown = items.count
        showOrphans()
        return !older.isEmpty || items.count > shown
    }

    /// With no older page left, a result whose call the hub no longer
    /// keeps shows on its own card, where it was in the transcript.
    private func showOrphans() {
        guard !hasEarlier else { return }
        for orphan in orphanResults.values.sorted(by: { $0.seq < $1.seq }) {
            insert(HubChatItem(id: "m\(orphan.seq)", kind: .tool(HubToolCall(name: "Tool output"), result: orphan.text)),
                   seq: orphan.seq)
        }
        orphanResults = [:]
    }

    /// Puts `item` before the first message newer than `seq`.
    private func insert(_ item: HubChatItem, seq: Int) {
        let at = items.firstIndex { Self.seq(of: $0).map { $0 > seq } ?? false } ?? items.endIndex
        items.insert(item, at: at)
    }

    /// The journal seq of a message's entry (ids are "m<seq>").
    private static func seq(of item: HubChatItem) -> Int? {
        item.id.hasPrefix("m") ? Int(item.id.dropFirst()) : nil
    }

    private func hasApproval(_ rid: String) -> Bool {
        items.contains { $0.id == "approval-\(rid)" }
    }

    // MARK: Events

    func apply(_ env: HubEnvelope) {
        if let seq = env.seq, seq <= replayFloor {
            replayApproval(env, seq: seq)
            return
        }
        if let seq = env.seq {
            if env.type == "message.appended" {
                guard !seen.contains(seq) else { return }
                seen.insert(seq)
            }
            cursor = max(cursor, seq)
        }
        switch env.type {
        case "message.appended":
            if let msg = env.decode(HubMessage.self) {
                Self.add(msg, seq: env.seq ?? 0, to: &items, cards: &toolCards, orphans: &orphanResults)
                showOrphans()
            }
        case "session.status":
            row?.status = env.decode(HubStatusData.self)?.status ?? ""
        case "session.ownership":
            if let ownership = env.decode(HubOwnershipData.self)?.ownership { row?.ownership = ownership }
        case "approval.requested":
            guard let req = env.decode(HubApprovalRequested.self), let rid = req.requestId else { return }
            let prompt = req.prompt ?? "Approval requested"
            if let i = items.firstIndex(where: { $0.id == "approval-\(rid)" }) {
                if case .approval(_, _, let decision, let resolved) = items[i].kind {
                    items[i].kind = .approval(requestId: rid, prompt: prompt, decision: decision, resolved: resolved)
                }
                return
            }
            items.append(HubChatItem(id: "approval-\(rid)",
                                     kind: .approval(requestId: rid, prompt: prompt, decision: nil, resolved: false)))
        case "approval.resolved":
            let res = env.decode(HubApprovalResolved.self)
            for i in items.indices {
                guard case .approval(let rid, let prompt, _, _) = items[i].kind else { continue }
                if (res?.requestId ?? "").isEmpty || res?.requestId == rid {
                    items[i].kind = .approval(requestId: rid, prompt: prompt, decision: res?.decision, resolved: true)
                }
            }
        default:
            break
        }
    }

    /// A replayed event from history already shown: an approval's ask
    /// goes where it was asked, with its prompt; its answer settles it.
    private func replayApproval(_ env: HubEnvelope, seq: Int) {
        cursor = max(cursor, seq)
        switch env.type {
        case "approval.requested":
            guard let req = env.decode(HubApprovalRequested.self), let rid = req.requestId else { return }
            var resolved = (decision: String?.none, done: false)
            if let i = items.firstIndex(where: { $0.id == "approval-\(rid)" }) {
                if case .approval(_, _, let decision, let done) = items[i].kind { resolved = (decision, done) }
                items.remove(at: i)
            }
            insert(HubChatItem(id: "approval-\(rid)", kind: .approval(
                requestId: rid, prompt: req.prompt ?? "Approval requested",
                decision: resolved.decision, resolved: resolved.done)), seq: seq)
        case "approval.resolved":
            let res = env.decode(HubApprovalResolved.self)
            guard let rid = res?.requestId, let i = items.firstIndex(where: { $0.id == "approval-\(rid)" }),
                  case .approval(_, let prompt, _, _) = items[i].kind else { return }
            items[i].kind = .approval(requestId: rid, prompt: prompt, decision: res?.decision, resolved: true)
        default:
            break
        }
    }

    /// Adds one message to the end of `list`. A tool result lands on its
    /// call's card; one whose call is on an older page not loaded yet
    /// waits in `orphans` until that page brings the call.
    static func add(_ msg: HubMessage, seq: Int, to list: inout [HubChatItem],
                    cards: inout [String: String], orphans: inout [String: Orphan]) {
        let id = "m\(seq)"
        let text = msg.text ?? ""
        switch msg.kind ?? "assistant" {
        case "user":
            list.append(HubChatItem(id: id, kind: .user(text)))
        case "thinking":
            list.append(HubChatItem(id: id, kind: .thinking(text)))
        case "tool_use":
            guard let tool = msg.tool else { return }
            var result: String?
            if let callId = tool.callId {
                cards[callId] = id
                result = orphans.removeValue(forKey: callId)?.text
            }
            list.append(HubChatItem(id: id, kind: .tool(tool, result: result)))
        case "tool_result":
            guard let ref = msg.resultRef else { return }
            let result = String(text.prefix(8000))
            if let cardId = cards[ref], let i = list.firstIndex(where: { $0.id == cardId }),
               case .tool(let call, _) = list[i].kind {
                list[i].kind = .tool(call, result: result)
            } else {
                orphans[ref] = Orphan(seq: seq, text: result)
            }
        case "compaction":
            list.append(HubChatItem(id: id, kind: .compaction))
        case "system":
            list.append(HubChatItem(id: id, kind: .system(text)))
        case "error":
            list.append(HubChatItem(id: id, kind: .error(text)))
        default:
            guard !text.isEmpty else { return }
            list.append(HubChatItem(id: id, kind: .assistant(text)))
        }
    }
}
