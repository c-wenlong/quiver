import Foundation
import Observation
import SwiftUI

/// One entry in a chat transcript, already folded for display: tool
/// results are merged into their call, approvals carry their state.
struct ChatItem: Identifiable, Equatable {
    enum Kind: Equatable {
        case user(text: String, send: SendState?)
        case assistant(String)
        case thinking(String)
        case tool(ToolCall, result: String?)
        case approval(requestId: String, prompt: String, state: ApprovalState)
        case compaction
        case system(String)
        case error(String)
    }

    let id: String
    var kind: Kind
}

enum SendState: Equatable {
    case sending, queued(String), failed(String)
    /// The machine took it, but the transcript may never echo it (a slash
    /// command such as /rename isn't a user message), so don't wait for that.
    case delivered(String?)

    /// Still on its way: a late reply to the send may change it.
    var inFlight: Bool {
        switch self {
        case .sending, .queued: true
        case .failed, .delivered: false
        }
    }

    /// What a `command.result` for this send turns it into; nil leaves it.
    static func settled(_ r: CommandResult, current: SendState, agentActive: Bool) -> SendState? {
        guard current.inFlight else { return nil }
        if r.ok == false { return .failed(r.note ?? "Send failed") }
        if r.degraded == true { return .queued(r.note ?? "Queued: mid-turn input isn't supported here") }
        // Mid-turn input waits for the agent's next step (Claude Code reads
        // it between tool calls), so say so rather than look ignored.
        return .delivered(agentActive ? "Delivered. The agent reads it at its next step" : nil)
    }
}

enum ApprovalState: Equatable {
    case pending, submitting, queued, resolved(String?), failed(String)
}

/// The open chat: the newest page of history from `/messages`, older
/// pages as you scroll up, then live events from the session's SSE
/// stream, plus optimistic sends.
@MainActor @Observable
final class ChatModel {
    let key: String
    private(set) var items: [ChatItem] = []
    private(set) var live = false
    private(set) var loading = true
    private(set) var loadError: String?
    /// The hub holds messages older than the oldest one shown.
    private(set) var hasEarlier = false
    private(set) var loadingEarlier = false
    /// The view scrolled near the oldest loaded entry; it loads the next
    /// page once scrolling comes to rest.
    var wantsEarlier = false
    var draft = ""
    var mode: SendMode = .queue

    private weak var app: AppModel?
    private var seen = Set<Int>()
    private var cursor = 0
    /// Tool call id to the id of its card (ids, not positions: removing a
    /// send bubble would shift positions under a result still to come).
    private var toolCards: [String: String] = [:]
    /// Tool results whose call is on a page not loaded yet, by call id.
    private var orphanResults: [String: String] = [:]
    /// The seq to page back from: the oldest message loaded.
    private var nextBefore: Int?
    private var streamTask: Task<Void, Never>?
    /// Live events not yet shown. The hub polls a busy transcript about
    /// once a second, so several messages arrive together; they're shown
    /// one at a time instead of as a block.
    private var pending: [Envelope] = []
    private var revealTask: Task<Void, Never>?
    /// When the last live entry was shown.
    private var lastReveal: ContinuousClock.Instant?
    /// How the reveal waits between entries; tests swap in a gate.
    var pause: (Duration) async -> Void = { try? await Task.sleep(for: $0) }
    /// A send went through and nothing from the agent has come back yet.
    private(set) var awaitingReply = false
    /// Bumped by every start() and stop(), so a start() still fetching
    /// history doesn't open a stream after the chat was left.
    private var generation = 0

    enum SendMode: String, CaseIterable { case queue, steer }

    /// Messages per `/messages` page. A long session's 500-message page
    /// was ~850 KB and took seconds to lay out on the phone.
    static let pageSize = 100

    init(key: String, app: AppModel) {
        self.key = key
        self.app = app
    }

    var row: SessionRow? { app?.session(key) }

    /// First open loads history; a resume (back from the background) keeps
    /// what's on screen and replays events after `cursor`.
    func start() async {
        guard let client = app?.client else { return }
        generation += 1
        let gen = generation
        streamTask?.cancel()
        streamTask = nil
        if items.isEmpty || loadError != nil { await backfill(client) }
        guard gen == generation else { return }
        streamTask = Task { [weak self, cursor] in
            for await event in EventStream.events(client: client, key: self?.key ?? "", last: cursor) {
                guard let self else { return }
                switch event {
                case .connected: self.live = true
                case .disconnected: self.live = false
                case .unauthorized: self.app?.unpair(); return
                case .truncated:
                    self.dropPending()
                    await self.backfill(client)
                case .event(let env): self.enqueue(env)
                }
            }
        }
    }

    func stop() {
        generation += 1
        streamTask?.cancel()
        streamTask = nil
        flushPending()
        live = false
    }

    /// The agent is mid-turn, a send waits for its reply, or messages
    /// that already arrived are still being revealed.
    var working: Bool {
        row?.sessionStatus == .active || awaitingReply || !pending.isEmpty
    }

    // MARK: Pacing

    /// The pause after showing one live entry, given how many events are
    /// still waiting: a short beat for a few, shorter as more pile up, and
    /// nil (show the rest at once) for a replay after reconnecting.
    static func revealDelay(waiting: Int) -> Duration? {
        guard waiting > 0, waiting <= 24 else { return nil }
        return .milliseconds(min(180, 1200 / waiting))
    }

    func enqueue(_ env: Envelope) {
        pending.append(env)
        guard revealTask == nil else { return }
        revealTask = Task { [weak self] in
            // Events often arrive a few ms apart, so the queue empties
            // between them: keep the gap from the last entry shown. Only
            // for a new entry; a result or status change goes straight in.
            if let self, let first = self.pending.first, Self.addsEntry(first), let last = self.lastReveal,
               let delay = Self.revealDelay(waiting: self.pending.count) {
                let gap = delay - (ContinuousClock.now - last)
                if gap > .zero { await self.pause(gap) }
            }
            while let self, !Task.isCancelled, !self.pending.isEmpty {
                guard let delay = Self.revealDelay(waiting: self.pending.count) else {
                    self.flushPending()
                    break
                }
                let before = self.items.count
                let env = self.pending.removeFirst()
                withAnimation(.easeOut(duration: 0.25)) { self.apply(env) }
                // Only a new entry earns a pause; a status change or a
                // result landing on its card goes straight through.
                if self.items.count > before {
                    self.lastReveal = .now
                    if !self.pending.isEmpty { await self.pause(delay) }
                }
            }
            // A cancelled pass was already replaced, maybe by a newer one.
            if !Task.isCancelled { self?.revealTask = nil }
        }
    }

    /// Whether an event would add an entry to the transcript, rather than
    /// update one or change the session's status.
    static func addsEntry(_ env: Envelope) -> Bool {
        switch env.type {
        case "approval.requested": return true
        case "message.appended": return env.decode(MessageData.self)?.kind != "tool_result"
        default: return false
        }
    }

    /// Shows everything still waiting, unanimated.
    private func flushPending() {
        revealTask?.cancel()
        revealTask = nil
        let all = pending
        pending = []
        for env in all { apply(env) }
    }

    /// A backfill is about to reload history, which holds these.
    private func dropPending() {
        revealTask?.cancel()
        revealTask = nil
        pending = []
    }

    /// Reloads history. Approvals still open and sends not yet echoed
    /// aren't in `/messages`, so they carry over.
    private func backfill(_ client: HubClient) async {
        do {
            let body = try await client.messages(key, limit: Self.pageSize)
            let keep = items.filter { item in
                switch item.kind {
                case .approval(_, _, let state):
                    if case .resolved = state { return false }
                    return true
                case .user(_, let send?):
                    // Delivered ones are in history (or never will be).
                    if case .delivered = send { return false }
                    return true
                default: return false
                }
            }
            items = []
            seen = []
            toolCards = [:]
            orphanResults = [:]
            for env in body.messages { apply(env) }
            hasEarlier = body.hasMore ?? false
            nextBefore = body.nextBefore ?? body.messages.first?.seq
            for item in keep where !items.contains(where: { $0.id == item.id }) { items.append(item) }
            // Opened from the inbox while an approval waits: the request
            // may predate the stream's replay window, so show it now.
            if let rid = app?.inbox.first(where: { $0.key == key })?.requestId, !rid.isEmpty, !hasApproval(rid) {
                items.append(ChatItem(id: "approval-\(rid)", kind: .approval(requestId: rid, prompt: "Approval requested", state: .pending)))
            }
            loadError = nil
        } catch {
            loadError = (error as? HubError)?.message ?? error.localizedDescription
        }
        // The newest page held nothing to show (only results of older
        // calls): fetch back until something does, rather than "No
        // messages yet" over a session that has them.
        if items.isEmpty, hasEarlier { await loadEarlier() }
        loading = false
    }

    /// Prepends the page of history just older than what's loaded. One
    /// page at a time; returns whether anything was added.
    @discardableResult
    func loadEarlier() async -> Bool {
        guard hasEarlier, !loadingEarlier, nextBefore != nil, let client = app?.client else { return false }
        loadingEarlier = true
        defer { loadingEarlier = false }
        // A page of only tool results whose calls are further back adds
        // nothing to show, and nothing on screen would ask again: keep
        // going until a page shows something or history runs out.
        while hasEarlier, let before = nextBefore {
            let body: MessagesResponse
            do {
                body = try await client.messages(key, limit: Self.pageSize, before: before)
            } catch {
                // Leave hasEarlier set: the next scroll to the top tries again.
                return false
            }
            // A backfill (stream truncated) replaced the history meanwhile.
            guard nextBefore == before else { return false }
            if prepend(body) { return true }
        }
        return false
    }

    /// Puts an older page in front of what's loaded; returns whether it
    /// added anything to show.
    func prepend(_ body: MessagesResponse) -> Bool {
        var older: [ChatItem] = []
        var cards: [String: String] = [:]
        for env in body.messages where env.type == "message.appended" {
            guard let seq = env.seq, !seen.contains(seq), let msg = env.decode(MessageData.self) else { continue }
            seen.insert(seq)
            Self.add(msg, id: "m\(seq)", to: &older, cards: &cards, orphans: &orphanResults)
        }
        // A call on this page may still be running; its live result has
        // to find the card.
        toolCards.merge(cards) { newer, _ in newer }
        items.insert(contentsOf: older, at: 0)
        hasEarlier = body.hasMore ?? false
        nextBefore = body.nextBefore ?? body.messages.first?.seq
        return !older.isEmpty
    }

    // MARK: Events

    func apply(_ env: Envelope) {
        if let seq = env.seq {
            if env.type == "message.appended" {
                guard !seen.contains(seq) else { return }
                seen.insert(seq)
            }
            cursor = max(cursor, seq)
        }
        switch env.type {
        case "message.appended":
            if let msg = env.decode(MessageData.self) { append(msg, seq: env.seq ?? 0) }
        case "session.status":
            let status = env.decode(StatusData.self)?.status ?? ""
            app?.patch(key, status: status)
            // The turn ended (or stopped to ask) without a message to show.
            if !["", "active"].contains(status) { awaitingReply = false }
        case "session.ownership":
            app?.patch(key, ownership: env.decode(OwnershipData.self)?.ownership)
            attachQueued = false
        case "approval.requested":
            guard let req = env.decode(ApprovalRequested.self), let rid = req.requestId else { return }
            if let i = items.firstIndex(where: { $0.id == "approval-\(rid)" }) {
                // The inbox placeholder learns its real prompt.
                if case .approval(_, _, let state) = items[i].kind {
                    items[i].kind = .approval(requestId: rid, prompt: req.prompt ?? "Approval requested", state: state)
                }
                return
            }
            items.append(ChatItem(id: "approval-\(rid)", kind: .approval(requestId: rid, prompt: req.prompt ?? "Approval requested", state: .pending)))
        case "approval.resolved":
            let res = env.decode(ApprovalResolved.self)
            resolveApprovals(requestId: res?.requestId, decision: res?.decision)
        case "command.result":
            if let result = env.decode(CommandResult.self) { settle(result) }
        default:
            break
        }
    }

    private func append(_ msg: MessageData, seq: Int) {
        if msg.kind == "user", let text = msg.text {
            // The hub echoes a queued send back as a user message: drop the
            // optimistic bubble it replaces (attachments prefix "@path ").
            if let i = items.firstIndex(where: {
                if case .user(let t, let s) = $0.kind, s != nil, !Self.isFailed(s) { return text == t || text.hasSuffix(t) }
                return false
            }) {
                // Still the newest entry: settle the bubble where it is, so
                // it doesn't leave and come back in.
                if i == items.count - 1 {
                    items[i].kind = .user(text: text, send: nil)
                    return
                }
                items.remove(at: i)
            }
        } else if msg.kind != "tool_result" {
            awaitingReply = false
        }
        Self.add(msg, id: "m\(seq)", to: &items, cards: &toolCards, orphans: &orphanResults)
    }

    /// Adds one message to the end of `list`. A tool result lands on its
    /// call's card; one whose call is on an older page not loaded yet
    /// waits in `orphans` until that page brings the call.
    static func add(_ msg: MessageData, id: String, to list: inout [ChatItem],
                    cards: inout [String: String], orphans: inout [String: String]) {
        let text = msg.text ?? ""
        switch msg.kind ?? "assistant" {
        case "user":
            list.append(ChatItem(id: id, kind: .user(text: text, send: nil)))
        case "thinking":
            list.append(ChatItem(id: id, kind: .thinking(text)))
        case "tool_use":
            guard let tool = msg.tool else { return }
            var result: String?
            if let callId = tool.callId {
                cards[callId] = id
                result = orphans.removeValue(forKey: callId)
            }
            list.append(ChatItem(id: id, kind: .tool(tool, result: result)))
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
            list.append(ChatItem(id: id, kind: .compaction))
        case "system":
            list.append(ChatItem(id: id, kind: .system(text)))
        case "error":
            list.append(ChatItem(id: id, kind: .error(text)))
        default:
            guard !text.isEmpty else { return }
            list.append(ChatItem(id: id, kind: .assistant(text)))
        }
    }

    private func hasApproval(_ rid: String) -> Bool {
        items.contains { if case .approval(rid, _, _) = $0.kind { true } else { false } }
    }

    private static func isFailed(_ s: SendState?) -> Bool {
        if case .failed = s { return true }
        return false
    }

    private func settle(_ r: CommandResult) {
        if r.op == "message", let cid = r.clientId, let i = items.firstIndex(where: { $0.id == "send-\(cid)" }),
           case .user(let text, let send?) = items[i].kind {
            // An echo already settled it if `send` is nil.
            guard let next = SendState.settled(r, current: send, agentActive: row?.sessionStatus == .active) else { return }
            if case .failed = next { awaitingReply = false }
            items[i].kind = .user(text: text, send: next)
            return
        }
        if r.op == "attach" || (r.op == "control" && attachQueued) {
            if r.ok == false {
                attachQueued = false
                attachError = r.note ?? "Attach failed"
            }
            return
        }
        if r.op == "approval", let rid = r.requestId {
            updateApproval(rid) { r.ok == false ? .failed(r.note ?? "The machine rejected it") : .resolved(nil) }
            return
        }
        if r.ok == false {
            items.append(ChatItem(id: UUID().uuidString, kind: .system("\(r.op ?? "Command") failed: \(r.note ?? "unknown error")")))
        }
    }

    private func resolveApprovals(requestId: String?, decision: String?) {
        for i in items.indices {
            guard case .approval(let rid, let prompt, _) = items[i].kind else { continue }
            if requestId == nil || requestId == "" || requestId == rid {
                items[i].kind = .approval(requestId: rid, prompt: prompt, state: .resolved(decision))
            }
        }
    }

    private func updateApproval(_ rid: String, _ state: () -> ApprovalState) {
        guard let i = items.firstIndex(where: { if case .approval(rid, _, _) = $0.kind { true } else { false } }),
              case .approval(_, let prompt, let old) = items[i].kind else { return }
        if case .resolved = old { return }
        items[i].kind = .approval(requestId: rid, prompt: prompt, state: state())
    }

    // MARK: Actions

    func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        await send(text)
    }

    private func send(_ text: String) async {
        guard let client = app?.client else { return }
        let cid = UUID().uuidString
        let id = "send-\(cid)"
        withAnimation(.easeOut(duration: 0.25)) {
            items.append(ChatItem(id: id, kind: .user(text: text, send: .sending)))
        }
        awaitingReply = true
        do {
            let accepted = try await client.sendMessage(key, text: text, mode: mode.rawValue, clientId: cid)
            if accepted.waiting == true, let i = pendingSend(id) {
                items[i].kind = .user(text: text, send: .queued("Queued until the agent is idle"))
            }
        } catch {
            if let i = pendingSend(id) {
                awaitingReply = false
                items[i].kind = .user(text: text, send: .failed((error as? HubError)?.message ?? "Send failed"))
            }
        }
    }

    /// The bubble of a send the hub hasn't echoed yet. An echoed one keeps
    /// its id but is delivered, so a late reply to the POST leaves it alone.
    private func pendingSend(_ id: String) -> Int? {
        items.firstIndex { if case .user(_, let s?) = $0.kind, s.inFlight { $0.id == id } else { false } }
    }

    func retry(_ item: ChatItem) async {
        guard case .user(let text, .failed) = item.kind else { return }
        items.removeAll { $0.id == item.id }
        await send(text)
    }

    func decide(_ requestId: String, decision: String) async {
        guard let client = app?.client else { return }
        updateApproval(requestId) { .submitting }
        do {
            let queued = try await client.approve(key, requestId: requestId, decision: decision)
            updateApproval(requestId) { queued ? .queued : .resolved(decision) }
        } catch {
            updateApproval(requestId) { .failed((error as? HubError)?.message ?? "Approval failed") }
        }
    }

    func interrupt() async {
        guard let client = app?.client else { return }
        do { try await client.control(key, op: "interrupt") } catch {
            items.append(ChatItem(id: UUID().uuidString, kind: .system((error as? HubError)?.message ?? "Interrupt failed")))
        }
    }

    private(set) var attaching = false
    /// A satellite took the attach (202) but hasn't confirmed it; the
    /// composer waits for its `session.ownership` event.
    private(set) var attachQueued = false
    private(set) var attachError: String?

    func attach() async {
        guard let client = app?.client else { return }
        attaching = true
        attachError = nil
        defer { attaching = false }
        do {
            if try await client.control(key, op: "attach") {
                attachQueued = true
            } else {
                app?.patch(key, ownership: "attached")
            }
        } catch {
            attachError = (error as? HubError)?.message ?? "Attach failed"
        }
    }
}
