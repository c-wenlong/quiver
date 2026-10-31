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

    @ObservationIgnored private let store: HubStore
    @ObservationIgnored private let client: (PairedHub) -> ShareHub
    /// Turns an image file into the JPEG that's uploaded; tests swap it.
    @ObservationIgnored var convert: @Sendable (URL) -> Data? = { PhotoPrep.jpeg(at: $0) }
    /// Refs already staged for the selected session, by payload index,
    /// so a retry after a failed send doesn't upload them again.
    @ObservationIgnored private var staged: [Int: String] = [:]
    @ObservationIgnored private var stagedFor: String?

    init(store: HubStore, client: @escaping (PairedHub) -> ShareHub) {
        self.store = store
        self.client = client
    }

    static let notPairedNote = "Pair this phone with a hub in Quiver Remote first."
    static let noSessionsNote = "No attached sessions on this hub. Attach one in Quiver Remote, then share again."

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
        if stagedFor != row.key {
            staged = [:]
            stagedFor = row.key
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
            phase = .sending("Sending")
            _ = try await api.sendMessage(row.key, text: text, mode: ShareTargets.mode(for: row),
                                          clientId: UUID().uuidString, attachments: refs)
            phase = .sent
            return true
        } catch {
            sendError = (error as? HubError).map { $0.code == "network" ? HubError.networkMessage : $0.message }
                ?? (error as? ShareInbox.Refusal)?.message ?? "Send failed."
            phase = .ready
            return false
        }
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
