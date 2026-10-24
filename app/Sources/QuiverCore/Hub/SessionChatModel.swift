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

    /// Messages per `/messages` page.
    public static let pageSize = 100

    private let hub: LocalHub
    private var seen = Set<Int>()
    private var cursor = 0
    /// Tool call id to the id of its card.
    private var toolCards: [String: String] = [:]
    /// Tool results whose call is on a page not loaded yet, by call id.
    private var orphanResults: [String: String] = [:]
    /// The seq to page back from: the oldest message loaded.
    private var nextBefore: Int?
    private var streamTask: Task<Void, Never>?
    /// Bumped by every start() and stop(), so a start() still fetching
    /// history doesn't open a stream after the chat was closed.
    private var generation = 0

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
                throw HubError(status: 404, code: "unknown_session", message: HubError.friendly(
                    code: "unknown_session", fallback: nil, status: 404))
            }
            self.row = row
            let page = try await hub.withClient { [key] in try await $0.messages(key, limit: Self.pageSize) }
            guard gen == generation else { return }
            load(page)
            loadError = nil
        } catch is CancellationError {
            return
        } catch {
            guard gen == generation else { return }
            loadError = error.localizedDescription
            loading = false
            return
        }
        loading = false
        follow(gen)
    }

    public func stop() {
        generation += 1
        streamTask?.cancel()
        streamTask = nil
        connection = .connecting
    }

    private func follow(_ gen: Int) {
        streamTask = Task { [weak self, key, cursor] in
            guard let client = try? await self?.hub.client() else { return }
            for await event in client.events(key, last: cursor) {
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
                    if let page = try? await client.messages(key, limit: Self.pageSize), gen == self.generation {
                        self.load(page)
                    }
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
        for env in page.messages { apply(env) }
        hasEarlier = page.hasMore ?? false
        nextBefore = page.nextBefore ?? page.messages.first?.seq
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
            Self.add(msg, id: "m\(seq)", to: &older, cards: &cards, orphans: &orphanResults)
        }
        // A call on this page may still be running; its live result has
        // to find the card.
        toolCards.merge(cards) { newer, _ in newer }
        items.insert(contentsOf: older, at: 0)
        hasEarlier = page.hasMore ?? false
        nextBefore = page.nextBefore ?? page.messages.first?.seq
        return !older.isEmpty
    }

    // MARK: Events

    func apply(_ env: HubEnvelope) {
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
                Self.add(msg, id: "m\(env.seq ?? 0)", to: &items, cards: &toolCards, orphans: &orphanResults)
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

    /// Adds one message to the end of `list`. A tool result lands on its
    /// call's card; one whose call is on an older page not loaded yet
    /// waits in `orphans` until that page brings the call.
    static func add(_ msg: HubMessage, id: String, to list: inout [HubChatItem],
                    cards: inout [String: String], orphans: inout [String: String]) {
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
                result = orphans.removeValue(forKey: callId)
            }
            list.append(HubChatItem(id: id, kind: .tool(tool, result: result)))
        case "tool_result":
            guard let ref = msg.resultRef else { return }
            let result = String(text.prefix(8000))
            if let cardId = cards[ref], let i = list.firstIndex(where: { $0.id == cardId }),
               case .tool(let call, _) = list[i].kind {
                list[i].kind = .tool(call, result: result)
            } else {
                orphans[ref] = result
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
