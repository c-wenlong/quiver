import Foundation
import Observation

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
}

enum ApprovalState: Equatable {
    case pending, submitting, queued, resolved(String?), failed(String)
}

/// The open chat: history from `/messages`, then live events from the
/// session's SSE stream, plus optimistic sends.
@MainActor @Observable
final class ChatModel {
    let key: String
    private(set) var items: [ChatItem] = []
    private(set) var live = false
    private(set) var loading = true
    private(set) var loadError: String?
    var draft = ""
    var mode: SendMode = .queue

    private weak var app: AppModel?
    private var seen = Set<Int>()
    private var cursor = 0
    /// Tool call id to the id of its card (ids, not positions: removing a
    /// send bubble would shift positions under a result still to come).
    private var toolCards: [String: String] = [:]
    private var streamTask: Task<Void, Never>?
    /// Bumped by every start() and stop(), so a start() still fetching
    /// history doesn't open a stream after the chat was left.
    private var generation = 0

    enum SendMode: String, CaseIterable { case queue, steer }

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
                case .truncated: await self.backfill(client)
                case .event(let env): self.apply(env)
                }
            }
        }
    }

    func stop() {
        generation += 1
        streamTask?.cancel()
        streamTask = nil
        live = false
    }

    /// Reloads history. Approvals still open and sends not yet echoed
    /// aren't in `/messages`, so they carry over.
    private func backfill(_ client: HubClient) async {
        do {
            let body = try await client.messages(key)
            let keep = items.filter { item in
                switch item.kind {
                case .approval(_, _, let state):
                    if case .resolved = state { return false }
                    return true
                case .user(_, let send): return send != nil
                default: return false
                }
            }
            items = []
            seen = []
            toolCards = [:]
            for env in body.messages { apply(env) }
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
        loading = false
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
            app?.patch(key, status: env.decode(StatusData.self)?.status ?? "")
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
        let id = "m\(seq)"
        let text = msg.text ?? ""
        switch msg.kind ?? "assistant" {
        case "user":
            // The hub echoes a queued send back as a user message: drop the
            // optimistic bubble it replaces (attachments prefix "@path ").
            if let i = items.firstIndex(where: {
                if case .user(let t, let s) = $0.kind, s != nil, !Self.isFailed(s) { return text == t || text.hasSuffix(t) }
                return false
            }) { items.remove(at: i) }
            items.append(ChatItem(id: id, kind: .user(text: text, send: nil)))
        case "thinking":
            items.append(ChatItem(id: id, kind: .thinking(text)))
        case "tool_use":
            guard let tool = msg.tool else { return }
            if let callId = tool.callId { toolCards[callId] = id }
            items.append(ChatItem(id: id, kind: .tool(tool, result: nil)))
        case "tool_result":
            if let ref = msg.resultRef, let cardId = toolCards[ref], let i = items.firstIndex(where: { $0.id == cardId }),
               case .tool(let call, _) = items[i].kind {
                items[i].kind = .tool(call, result: String(text.prefix(8000)))
            }
        case "compaction":
            items.append(ChatItem(id: id, kind: .compaction))
        case "system":
            items.append(ChatItem(id: id, kind: .system(text)))
        case "error":
            items.append(ChatItem(id: id, kind: .error(text)))
        default:
            guard !text.isEmpty else { return }
            items.append(ChatItem(id: id, kind: .assistant(text)))
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
           case .user(let text, _) = items[i].kind {
            if r.ok == false {
                items[i].kind = .user(text: text, send: .failed(r.note ?? "Send failed"))
            } else if r.degraded == true {
                items[i].kind = .user(text: text, send: .queued(r.note ?? "Queued: mid-turn input isn't supported here"))
            }
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
        items.append(ChatItem(id: id, kind: .user(text: text, send: .sending)))
        do {
            let accepted = try await client.sendMessage(key, text: text, mode: mode.rawValue, clientId: cid)
            if accepted.waiting == true, let i = items.firstIndex(where: { $0.id == id }) {
                items[i].kind = .user(text: text, send: .queued("Queued until the agent is idle"))
            }
        } catch {
            if let i = items.firstIndex(where: { $0.id == id }) {
                items[i].kind = .user(text: text, send: .failed((error as? HubError)?.message ?? "Send failed"))
            }
        }
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
