import Foundation
import Observation

// The chat's Queued tray: queue-mode sends the hub holds until the
// agent's turn ends, shown above the prompt box instead of in the
// transcript, each with Edit, Remove and Send now. A port of the iPhone
// app's tray (app/ios `ChatModel`, `QueuedSend`).

/// One send the hub still holds, as `GET /v1/sessions/{key}/queue`
/// lists it.
public struct HubWaitingSend: Decodable, Equatable, Sendable {
    public var clientId: String
    public var text: String?
    public var queuedAt: Double?

    public init(clientId: String, text: String? = nil, queuedAt: Double? = nil) {
        self.clientId = clientId
        self.text = text
        self.queuedAt = queuedAt
    }
}

/// `GET /v1/sessions/{key}/queue`.
public struct HubQueueResponse: Decodable, Sendable {
    public var sends: [HubWaitingSend]
}

/// A send waiting in the tray. It stays out of the transcript until it
/// goes out.
public struct QueuedSend: Identifiable, Equatable, Sendable {
    public enum Phase: Equatable, Sendable {
        case waiting
        /// Send now was clicked; its result or echo moves it to the
        /// transcript.
        case sendingNow
        /// Remove was clicked; hidden while the hub confirms.
        case removing
        /// The hub says it's no longer waiting (409 `not_waiting`); its
        /// result or echo is still to come.
        case left
    }

    public let clientId: String
    /// Changes when the message is edited in place.
    public var text: String
    public var phase: Phase
    /// Why the last action on it didn't go through, shown on its row.
    public var note: String?

    public var id: String { clientId }

    public init(clientId: String, text: String, phase: Phase = .waiting, note: String? = nil) {
        self.clientId = clientId
        self.text = text
        self.phase = phase
        self.note = note
    }
}

/// The tray and the prompt box's text, kept together because Edit swaps
/// a queued message into the box and Save or Cancel swaps it back.
@MainActor
@Observable
public final class QueuedTray {
    /// The prompt box's text.
    public var draft = ""
    /// Sends the hub holds, oldest first; ones being removed stay here,
    /// hidden, until the hub confirms. The tray shows `sends`.
    public private(set) var queued: [QueuedSend] = []
    /// The queued send whose text is in the prompt box; Send saves it.
    public private(set) var editingId: String?
    public private(set) var savingEdit = false
    /// Why an edit didn't save, in the bar above the prompt box.
    public private(set) var editNote: String?
    /// What was typed before Edit took the box; Cancel and Save put it back.
    private var draftBeforeEdit = ""
    /// Sends that left the tray (went out, failed or were removed): a
    /// queue list fetched before that can't bring them back.
    private var settled = Set<String>()
    /// How many sends `hold` has taken, and when each came: a list the
    /// hub answered before a send was held can't drop it.
    public private(set) var holds = 0
    private var heldAt: [String: Int] = [:]
    /// Rows a transcript echo already stands for: their result then
    /// needs no entry of its own.
    private var echoed = Set<String>()

    /// Acts on one waiting send: client id, `steer`, `cancel` or `edit`,
    /// and the new text for `edit`. SessionChatModel points it at the hub.
    @ObservationIgnored public var act: (String, String, String?) async throws -> Void = { _, _, _ in }

    public init() {}

    /// What the tray shows: everything queued but what's being removed.
    public var sends: [QueuedSend] { queued.filter { $0.phase != .removing } }

    public static func countLabel(_ n: Int) -> String {
        n == 1 ? "1 message queued" : "\(n) messages queued"
    }

    // MARK: What the hub and the transcript say

    /// The hub answered a queue-mode send with `waiting`: it holds it.
    public func hold(_ cid: String, text: String) {
        guard !settled.contains(cid), !queued.contains(where: { $0.clientId == cid }) else { return }
        heldAt[cid] = holds
        holds += 1
        queued.append(QueuedSend(clientId: cid, text: text))
    }

    /// Takes the hub's list after the chat opens or its stream recovers:
    /// what it holds shows in its order with the hub's text, keeping the
    /// state of each row known here; the rest leaves, except sends held
    /// since `asked` (the `holds` count when the list was asked for),
    /// which the list is too old to know. `known` are sends already shown
    /// in the transcript.
    public func merge(_ hub: [HubWaitingSend], known: Set<String> = [], asked: Int? = nil) {
        let skip = settled.union(known)
        var out: [QueuedSend] = []
        for send in hub where !skip.contains(send.clientId) && !out.contains(where: { $0.clientId == send.clientId }) {
            var row = queued.first { $0.clientId == send.clientId }
                ?? QueuedSend(clientId: send.clientId, text: send.text ?? "")
            // Another client may have edited it meanwhile.
            if let text = send.text { row.text = text }
            out.append(row)
        }
        let mark = asked ?? holds
        out += queued.filter { mine in
            !out.contains { $0.clientId == mine.clientId } && (heldAt[mine.clientId] ?? -1) >= mark
        }
        if let id = editingId, !out.contains(where: { $0.clientId == id }) { cancelEdit() }
        queued = out
    }

    /// The send's `command.result` arrived: it leaves the tray. Returns
    /// it, so the chat can show it in the transcript, unless it was in
    /// the tray at all; `echoed` says an echo already shows it.
    @discardableResult
    public func take(_ cid: String) -> (send: QueuedSend, echoed: Bool)? {
        guard let i = queued.firstIndex(where: { $0.clientId == cid }) else { return nil }
        if editingId == cid {
            // It went out mid-edit: the new text stays in the box to send
            // as a message of its own.
            editingId = nil
            draftBeforeEdit = ""
            editNote = "That message already went out"
        }
        settled.insert(cid)
        return (queued.remove(at: i), echoed.remove(cid) != nil)
    }

    /// The transcript echoed a user message that may be a queued send
    /// going out. Text alone can't say which send it was (another client
    /// can send the same words), so the row stays until its own result;
    /// the oldest unclaimed row with that text is marked, and its result
    /// then adds no second entry. `echoes(echo, sent)` is the chat's rule.
    @discardableResult
    public func noteEcho(_ echo: String, echoes: (String, String) -> Bool) -> Bool {
        guard let row = queued.first(where: { !echoed.contains($0.clientId) && echoes(echo, $0.text) })
        else { return false }
        echoed.insert(row.clientId)
        return true
    }

    // MARK: Row actions

    /// Send now: the hub stops holding it and writes it into the running
    /// turn. The row says "Sending now" until its result or echo arrives.
    public func sendNow(_ cid: String) async {
        guard let i = queued.firstIndex(where: { $0.clientId == cid }), queued[i].phase == .waiting else { return }
        queued[i].phase = .sendingNow
        queued[i].note = nil
        if editingId == cid { cancelEdit() }
        do {
            try await act(cid, "steer", nil)
        } catch {
            guard let j = queued.firstIndex(where: { $0.clientId == cid }), queued[j].phase == .sendingNow else { return }
            queued[j].phase = (error as? HubError)?.code == "not_waiting" ? .left : .waiting
            queued[j].note = Self.note(for: error)
        }
    }

    /// Remove: gone from the tray at once, back with a note if the hub
    /// refuses. Its `command.result` says `cancelled`, never a failure.
    public func remove(_ cid: String) async {
        guard let i = queued.firstIndex(where: { $0.clientId == cid }) else { return }
        switch queued[i].phase {
        case .sendingNow, .removing: return
        case .left:
            // The hub no longer holds it: nothing to ask.
            drop(cid)
            return
        case .waiting: break
        }
        if editingId == cid { cancelEdit() }
        queued[i].phase = .removing
        queued[i].note = nil
        do {
            try await act(cid, "cancel", nil)
            drop(cid)
        } catch {
            guard let j = queued.firstIndex(where: { $0.clientId == cid }), queued[j].phase == .removing else { return }
            let code = (error as? HubError)?.code
            // The hub never held it, or forgot it: nothing to remove.
            if code == "unknown_send" { return drop(cid) }
            queued[j].phase = code == "not_waiting" ? .left : .waiting
            queued[j].note = Self.note(for: error)
        }
    }

    /// Remove all: each row the hub still holds, as its own remove.
    public func removeAll() async {
        await withTaskGroup(of: Void.self) { group in
            for send in sends { group.addTask { await self.remove(send.clientId) } }
        }
    }

    private func drop(_ cid: String) {
        settled.insert(cid)
        queued.removeAll { $0.clientId == cid }
    }

    // MARK: Editing

    /// Edit: the message's text goes into the prompt box, and Send saves
    /// it back. Only a send the hub still holds can be edited.
    @discardableResult
    public func beginEdit(_ cid: String) -> Bool {
        guard let send = queued.first(where: { $0.clientId == cid }), send.phase == .waiting else { return false }
        if editingId == nil { draftBeforeEdit = draft }
        editingId = cid
        draft = send.text
        editNote = nil
        return true
    }

    /// Puts back what was typed before Edit.
    public func cancelEdit() {
        guard editingId != nil else { return }
        editingId = nil
        draft = draftBeforeEdit
        draftBeforeEdit = ""
        editNote = nil
    }

    /// Clears the note an edit left, once something new is sent.
    public func clearEditNote() { editNote = nil }

    /// Saves the prompt box's text over the message being edited.
    public func saveEdit() async {
        guard let cid = editingId, !savingEdit else { return }
        let typed = draft
        let text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        guard queued.contains(where: { $0.clientId == cid && $0.phase == .waiting }) else {
            return editWentOut(cid, text: text, typed: typed)
        }
        savingEdit = true
        defer { savingEdit = false }
        do {
            try await act(cid, "edit", text)
            if let i = queued.firstIndex(where: { $0.clientId == cid }) {
                queued[i].text = text
                queued[i].note = nil
            }
            guard editingId == cid else { return }
            if draft == typed {
                cancelEdit()
            } else {
                // Typed on while it saved: the edit ends, the typing stays.
                editingId = nil
                draftBeforeEdit = ""
                editNote = nil
            }
        } catch {
            guard editingId == cid else { return }
            if (error as? HubError)?.code == "not_waiting" {
                editWentOut(cid, text: text, typed: typed)
            } else {
                editNote = Self.note(for: error)
            }
        }
    }

    /// Too late to edit: the new text stays in the box to send as a new
    /// message (and anything typed since the save stays as typed).
    private func editWentOut(_ cid: String, text: String, typed: String) {
        if let i = queued.firstIndex(where: { $0.clientId == cid }), queued[i].phase == .waiting {
            queued[i].phase = .left
            queued[i].note = "Already sent"
        }
        editingId = nil
        draftBeforeEdit = ""
        if draft == typed { draft = text }
        editNote = "That message already went out"
    }

    /// The short note a row shows when the hub refuses Send now, Remove
    /// or an edit.
    public static func note(for error: Error) -> String {
        guard let e = error as? HubError else { return "Something went wrong" }
        switch e.code {
        case "not_waiting":
            let why = e.message.lowercased()
            if why.contains("removed") { return "Already removed" }
            if why.contains("failed") { return "Already failed" }
            return "Already sent"
        case "not_attached": return "Attach to send now"
        case "queue_unsupported": return "Not available on this machine"
        case "backlog_full": return "Too many waiting. Try again shortly"
        case "unknown_send": return "The hub lost this message"
        case "network": return "Can't reach the hub"
        default: return e.message
        }
    }
}
