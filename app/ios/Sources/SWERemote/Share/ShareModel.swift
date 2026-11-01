import Foundation
import Observation

/// The hub calls the share sheet makes. `HubClient` is the real one;
/// tests pass a fake.
protocol ShareHub: Sendable {
    func sessions() async throws -> SessionsResponse
    func uploadAttachment(_ key: String, name: String, data: Data,
                          progress: @escaping @Sendable (Double) -> Void) async throws -> StagedAttachment
    func uploadAttachment(_ key: String, name: String, file: URL,
                          progress: @escaping @Sendable (Double) -> Void) async throws -> StagedAttachment
    func sendMessage(_ key: String, text: String, mode: String, clientId: String,
                     attachments: [String]) async throws -> SendAccepted
}

extension HubClient: ShareHub {}

/// The sessions the share sheet offers: the ones that take a message
/// (attached: the hub refuses a send to a session it only observes),
/// most recently active first.
enum ShareTargets {
    static func sessions(_ rows: [SessionRow]) -> [SessionRow] {
        rows.filter(\.isAttached).sorted { ($0.lastActiveAt ?? 0) > ($1.lastActiveAt ?? 0) }
    }

    /// The send's mode, as the chat picks it (`ChatModel.route`): queue,
    /// except a satellite's session mid-turn, which can't hold a send.
    static func mode(for row: SessionRow) -> String {
        row.sessionStatus == .active && !row.holdsQueuedSends ? "steer" : "queue"
    }
}

/// The share sheet: which hub and session, the message, and the send.
/// Files upload one at a time when Send is tapped (never side by side,
/// to stay inside an extension's memory), then the message goes with
/// their refs.
@MainActor @Observable
final class ShareModel {
    enum Phase: Equatable {
        case loading
        /// No hub to send to, with why.
        case notPaired(String)
        /// The hub didn't answer the session list, with why.
        case unreachable(String)
        case ready
        /// What's going on, e.g. "Uploading 2 of 3".
        case sending(String)
        case sent
    }

    private(set) var phase: Phase = .loading
    private(set) var hubs: HubList = HubList()
    private(set) var sessions: [SessionRow] = []
    var selectedKey: String?
    var message = ""
    private(set) var payloads: [SharedPayload] = []
    /// What couldn't be taken from the share, and the last send failure.
    private(set) var notices: [String] = []
    private(set) var sendError: String?
    /// The hub holds the sent message until the agent's turn ends.
    private(set) var heldForIdle = false

    @ObservationIgnored private let store: HubStore
    @ObservationIgnored private let client: (PairedHub) -> ShareHub
    /// Turns an image file into the JPEG that's uploaded; tests swap it.
    @ObservationIgnored var convert: @Sendable (URL) -> Data? = { PhotoPrep.jpeg(at: $0) }
    /// Refs already staged for the selected hub and session, by payload
    /// index, so a retry after a failed send doesn't upload them again.
    @ObservationIgnored private var staged: [Int: String] = [:]
    @ObservationIgnored private var stagedFor: String?
    /// The client id of a message whose answer never came, kept while the
    /// same message goes to the same place: a retry the hub still holds
    /// answers `duplicate_client_id` instead of queueing a second copy.
    @ObservationIgnored private var attempt: (target: String, text: String, refs: [String], clientId: String)?

    init(store: HubStore, client: @escaping (PairedHub) -> ShareHub) {
        self.store = store
        self.client = client
    }

    static let notPairedNote = "Pair this phone with a hub in SWE Remote first."
    /// Under "Sent": the hub took the message and delivers it itself, so
    /// a delivery that fails later is reported in the session, not here.
    var sentNote: String {
        heldForIdle ? "The hub holds it until the agent finishes its turn." : "The hub is passing it to the agent."
    }
    static let noSessionsNote = "No attached sessions on this hub. Attach one in SWE Remote, then share again."
    /// A message whose POST got no answer may have reached the hub.
    static let unconfirmedNote = "Can't reach the hub, so the message may not have gone. Check the session before sending again."

    var hub: PairedHub? { hubs.active }
    var selected: SessionRow? { sessions.first { $0.key == selectedKey } }
    var uploads: [SharedPayload] { payloads.filter(\.isUpload) }
    var outgoingText: String { ShareDraft.text(message: message, payloads: payloads) }

    /// Why Send is off, worded for under the form; nil when it's on.
    var blocker: String? {
        guard phase == .ready else { return nil }
        if selected == nil { return sessions.isEmpty ? Self.noSessionsNote : "Pick a session." }
        if outgoingText.isEmpty { return uploads.isEmpty ? "Nothing to send." : AttachmentRules.needsText }
        return nil
    }

    var canSend: Bool { phase == .ready && blocker == nil }

    /// Takes what the share carried, then lists the hub's sessions.
    func start(payloads: [SharedPayload], notices: [String]) async {
        let (kept, cut) = ShareDraft.capped(payloads)
        self.payloads = kept
        self.notices = notices + (cut.map { [$0] } ?? [])
        hubs = store.load()
        await refresh()
    }

    func selectHub(_ id: String) async {
        guard hubs.hubs.contains(where: { $0.id == id }), hubs.activeId != id else { return }
        hubs.activeId = id
        await refresh()
    }

    /// Lists the active hub's sessions; the first is picked.
    func refresh() async {
        guard let hub else {
            phase = .notPaired(Self.notPairedNote)
            return
        }
        phase = .loading
        do {
            let rows = try await client(hub).sessions().sessions
            guard hubs.active?.id == hub.id else { return }
            sessions = ShareTargets.sessions(rows)
            if selected == nil { selectedKey = sessions.first?.key }
            phase = .ready
        } catch let error as HubError where error.isUnauthorized {
            phase = .notPaired(error.message)
        } catch {
            let e = error as? HubError
            phase = .unreachable(e.map { HubError.detail(underUnreachableTitle: $0.message) }
                                 ?? "Check that Tailscale is on, then try again.")
        }
    }

    /// Uploads each file in turn, then sends the message with their refs.
    /// True once the hub took it. On a failure the sheet stays, with why,
    /// and a second Send reuses the files already staged.
    @discardableResult
    func send() async -> Bool {
        guard canSend, let hub, let row = selected else { return false }
        let api = client(hub)
        let text = outgoingText
        sendError = nil
        let target = "\(hub.id) \(row.key)"
        if stagedFor != target {
            staged = [:]
            stagedFor = target
        }
        let files = payloads.enumerated().filter { $0.element.isUpload }
        var refs: [String] = []
        do {
            for (n, (i, payload)) in files.enumerated() {
                if let ref = staged[i] {
                    refs.append(ref)
                    continue
                }
                phase = .sending(files.count == 1 ? "Uploading" : "Uploading \(n + 1) of \(files.count)")
                let ref = try await upload(payload, to: row.key, api: api).ref
                staged[i] = ref
                refs.append(ref)
            }
        } catch {
            return failed(error)
        }
        phase = .sending("Sending")
        if let a = attempt, a.target != target || a.text != text || a.refs != refs { attempt = nil }
        let clientId = attempt?.clientId ?? UUID().uuidString
        attempt = (target, text, refs, clientId)
        do {
            let accepted = try await api.sendMessage(row.key, text: text, mode: ShareTargets.mode(for: row),
                                                     clientId: clientId, attachments: refs)
            heldForIdle = accepted.waiting == true
        } catch let error as HubError where error.code == "duplicate_client_id" {
            // The first try reached the hub, which still holds it.
            heldForIdle = true
        } catch let error as HubError where error.code == "network" {
            return failed(error, note: Self.unconfirmedNote)
        } catch {
            // The hub answered no: nothing is waiting under this id.
            attempt = nil
            return failed(error)
        }
        attempt = nil
        phase = .sent
        return true
    }

    private func failed(_ error: Error, note: String? = nil) -> Bool {
        sendError = note ?? (error as? HubError).map { $0.code == "network" ? HubError.networkMessage : $0.message }
            ?? (error as? ShareInbox.Refusal)?.message ?? "Send failed."
        phase = .ready
        return false
    }

    private func upload(_ payload: SharedPayload, to key: String, api: ShareHub) async throws -> StagedAttachment {
        switch payload {
        case .image(let name, let file):
            // One image is decoded at a time, off the main thread, and only
            // as far as the capped thumbnail.
            let convert = self.convert
            guard let jpeg = await Task.detached(operation: { convert(file) }).value else {
                throw ShareInbox.Refusal(message: "\(name) couldn't be read as an image.")
            }
            if let why = AttachmentRules.refusal(name: name, size: jpeg.count) { throw ShareInbox.Refusal(message: why) }
            return try await api.uploadAttachment(key, name: name, data: jpeg, progress: { _ in })
        case .imageData(let name, let data):
            if let why = AttachmentRules.refusal(name: name, size: data.count) { throw ShareInbox.Refusal(message: why) }
            return try await api.uploadAttachment(key, name: name, data: data, progress: { _ in })
        case .file(let name, let file, _):
            return try await api.uploadAttachment(key, name: name, file: file, progress: { _ in })
        case .text, .link:
            throw CancellationError()
        }
    }
}
