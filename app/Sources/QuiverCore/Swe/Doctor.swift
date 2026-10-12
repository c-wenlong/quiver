import Foundation
import Observation

/// `swe doctor --json`.
public struct DoctorReport: Decodable, Sendable, Equatable {
    public let ok: Bool
    public let findings: [Finding]

    public init(ok: Bool, findings: [Finding]) {
        self.ok = ok
        self.findings = findings
    }

    public struct Finding: Decodable, Sendable, Equatable, Identifiable {
        public enum Severity: String, Decodable, Sendable { case error, warning, info }

        public var id: String { check + message }
        public let check: String
        public let severity: Severity
        public let message: String
        public let fix: String?
        /// The harness the finding is about (an off-PATH install), when it
        /// is about one; older swe builds leave it out.
        public let harness: String?
    }
}

extension SweClient {
    /// Doctor exits 1 when it finds problems, so a non-zero status with a
    /// JSON body is a report, not a failure.
    public func doctor() async throws -> DoctorReport {
        try await call(["doctor", "--json"], allowFailure: true)
    }
}

/// Loading state shared by every page that reads from swe.
public enum Loadable<Value: Equatable & Sendable>: Equatable, Sendable {
    case idle
    case loading
    case loaded(Value)
    case failed(String)
}

@MainActor
@Observable
public final class DoctorModel {
    public private(set) var state: Loadable<DoctorReport> = .idle
    private let swe: SweClient
    /// Bumped by each load; a reply from an older load is dropped, so a slow
    /// earlier refresh cannot overwrite a newer one.
    private var generation = 0

    public init(swe: SweClient) {
        self.swe = swe
    }

    public func load() async {
        generation += 1
        let mine = generation
        state = .loading
        let next: Loadable<DoctorReport>
        do {
            next = .loaded(try await swe.doctor())
        } catch is CancellationError {
            return
        } catch {
            next = .failed(error.localizedDescription)
        }
        if mine == generation { state = next }
    }
}
