import Foundation
import Observation

/// One row of `swe remote devices --requests --json`: a phone that asked
/// the hub to pair by its Tailscale identity and is showing a 6-digit
/// code. The list never carries the code itself; the person approving
/// types the one the phone shows.
public struct PairRequest: Decodable, Sendable, Equatable, Identifiable {
    public let requestId: String
    /// What the phone asked to be called, else its tailnet node's short name.
    public let name: String?
    /// The tailnet node the request came from (`phone.tail1234.ts.net`).
    public let nodeName: String?
    /// The Tailscale login that owns that node; empty for a tagged node.
    public let login: String?
    public let peerIp: String?
    public let createdAt: Date?
    /// Seconds left before the hub drops the request.
    public let expiresInS: Int?

    public var id: String { requestId }

    public init(requestId: String, name: String? = nil, nodeName: String? = nil,
                login: String? = nil, peerIp: String? = nil,
                createdAt: Date? = nil, expiresInS: Int? = nil) {
        self.requestId = requestId
        self.name = name
        self.nodeName = nodeName
        self.login = login
        self.peerIp = peerIp
        self.createdAt = createdAt
        self.expiresInS = expiresInS
    }
}

/// `swe remote devices --approve=<code>|--deny=<code> --json`. A code
/// that matches nothing answers `{"ok": false}`: `--approve` exits 1,
/// `--deny` exits 0, so callers read `ok`, never the exit status.
public struct PairDecision: Decodable, Sendable, Equatable {
    public let ok: Bool
    public let deviceId: String?
    public let name: String?
}

extension SweClient {
    public func remotePairRequests() async throws -> [PairRequest] {
        struct List: Decodable { let requests: [PairRequest] }
        let list: List = try await call(["remote", "devices", "--requests", "--json"])
        return list.requests
    }

    /// Approves or denies one request. `--request-id` holds the code to
    /// that row, so a code typed against one request never decides
    /// another. Wrong approve codes count toward that row's lockout;
    /// wrong deny codes don't.
    public func remoteDecidePairRequest(_ requestID: String, code: String,
                                        approve: Bool) async throws -> PairDecision {
        let verb = approve ? "--approve" : "--deny"
        return try await call(
            ["remote", "devices", "\(verb)=\(code)", "--request-id=\(requestID)", "--json"],
            allowFailure: true)
    }
}

/// Settings › Integrations › Remote › Pending requests: the hub's open
/// Tailscale pair requests, polled while the section is on screen, each
/// with its own code field and Approve / Deny.
///
/// This reads and writes the hub on this Mac through `swe`; a hub on
/// another machine is out of scope (#681).
@MainActor
@Observable
public final class PairRequestsModel {
    /// Digits in a pair code.
    public static let codeLength = 6
    /// How often `poll` asks swe again.
    public static let pollInterval: Duration = .seconds(3)

    public private(set) var requests: [PairRequest] = []
    /// False until the first answer, so the view can show a spinner
    /// instead of "no requests" while swe starts.
    public private(set) var loaded = false
    /// Why the last refresh failed; cleared by the next good one.
    public private(set) var loadError: String?
    /// What the user has typed into each row's code field.
    public private(set) var codes: [String: String] = [:]
    /// Request ids with an approve or deny in flight.
    public private(set) var deciding: Set<String> = []
    /// The last failure per request id, shown on its row.
    public private(set) var errors: [String: String] = [:]

    private let swe: SweClient
    private var generation = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    /// Re-reads the request list. A slower, older refresh can't overwrite a
    /// newer one, and code fields and errors of rows that went away (approved
    /// elsewhere, denied, expired) are dropped with them.
    public func refresh() async {
        generation += 1
        let mine = generation
        do {
            let rows = try await swe.remotePairRequests()
            guard mine == generation else { return }
            requests = rows
            loadError = nil
            let live = Set(rows.map(\.id))
            codes = codes.filter { live.contains($0.key) }
            errors = errors.filter { live.contains($0.key) }
        } catch is CancellationError {
            return
        } catch {
            guard mine == generation else { return }
            loadError = error.localizedDescription
        }
        loaded = true
    }

    /// Refreshes every `interval` until the calling task is cancelled, which
    /// SwiftUI does when the section leaves the screen.
    public func poll(every interval: Duration = PairRequestsModel.pollInterval) async {
        while !Task.isCancelled {
            await refresh()
            do { try await Task.sleep(for: interval) } catch { return }
        }
    }

    public func code(for request: PairRequest) -> String {
        codes[request.id] ?? ""
    }

    /// Keeps digits only, at most `codeLength` of them, so a pasted
    /// "123 456" still fits. Typing clears the row's last error.
    public func setCode(_ text: String, for request: PairRequest) {
        codes[request.id] = Self.sanitize(text)
        errors[request.id] = nil
    }

    public static func sanitize(_ text: String) -> String {
        String(text.filter(\.isASCII).filter(\.isNumber).prefix(codeLength))
    }

    /// Approve and Deny need a whole code, and one decision at a time.
    public func canDecide(_ request: PairRequest) -> Bool {
        code(for: request).count == Self.codeLength && !deciding.contains(request.id)
    }

    /// Runs the decision. True when the hub took it; the row then leaves
    /// the list at once rather than at the next poll.
    @discardableResult
    public func decide(_ request: PairRequest, approve: Bool) async -> Bool {
        guard canDecide(request) else { return false }
        let code = code(for: request)
        deciding.insert(request.id)
        errors[request.id] = nil
        defer { deciding.remove(request.id) }
        do {
            let result = try await swe.remoteDecidePairRequest(
                request.id, code: code, approve: approve)
            guard result.ok else {
                // The field stays editable while swe runs. If the user has
                // already typed a correction, this answer is about a code
                // that is gone: leave their new one and say nothing.
                if codes[request.id] == code {
                    errors[request.id] = "That code doesn't match this request."
                    codes[request.id] = ""
                }
                return false
            }
            // A poll that started before the decision would put the row
            // back for a beat; its answer is stale now.
            generation += 1
            requests.removeAll { $0.id == request.id }
            codes[request.id] = nil
            return true
        } catch is CancellationError {
            return false
        } catch {
            errors[request.id] = error.localizedDescription
            return false
        }
    }

    /// "just now", "40s ago", "3m ago": how long the phone has been waiting.
    public static func age(of request: PairRequest, at now: Date) -> String? {
        guard let created = request.createdAt else { return nil }
        let seconds = max(0, Int(now.timeIntervalSince(created)))
        if seconds < 10 { return "just now" }
        if seconds < 60 { return "\(seconds)s ago" }
        if seconds < 3600 { return "\(seconds / 60)m ago" }
        return "\(seconds / 3600)h ago"
    }
}
