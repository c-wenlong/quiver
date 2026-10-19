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
        /// The fix swe can run for us, when it has one.
        public let action: Action?

        /// Fixes swe runs for us (`action` in the row); an action a newer
        /// swe adds decodes as nil so this build just doesn't offer Run.
        public enum Action: String, Decodable, Sendable { case setup, `init`, discover }

        private enum CodingKeys: String, CodingKey {
            case check, severity, message, fix, harness, action
        }

        public init(check: String, severity: Severity, message: String,
                    fix: String? = nil, harness: String? = nil, action: Action? = nil) {
            self.check = check
            self.severity = severity
            self.message = message
            self.fix = fix
            self.harness = harness
            self.action = action
        }

        public init(from decoder: any Decoder) throws {
            let box = try decoder.container(keyedBy: CodingKeys.self)
            check = try box.decode(String.self, forKey: .check)
            severity = try box.decode(Severity.self, forKey: .severity)
            message = try box.decode(String.self, forKey: .message)
            fix = try box.decodeIfPresent(String.self, forKey: .fix)
            harness = try box.decodeIfPresent(String.self, forKey: .harness)
            action = try box.decodeIfPresent(String.self, forKey: .action)
                .flatMap(Action.init)
        }
    }
}

extension SweClient {
    /// Doctor exits 1 when it finds problems, so a non-zero status with a
    /// JSON body is a report, not a failure.
    public func doctor() async throws -> DoctorReport {
        try await call(["doctor", "--json"], allowFailure: true)
    }

    /// `swe harness discover --apply`: registers the high-confidence
    /// matches. The JSON is informational; only the exit matters.
    @discardableResult
    public func applyDiscovery() async throws -> String {
        try await runPlain(["harness", "discover", "--apply", "--json"])
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
    /// Findings whose Run is in flight, and what the last failure said.
    public private(set) var running: Set<DoctorReport.Finding.ID> = []
    public private(set) var runErrors: [DoctorReport.Finding.ID: String] = [:]
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

    /// Runs the finding's fix through swe, then re-reads the report.
    public func run(_ finding: DoctorReport.Finding) async {
        guard let action = finding.action, !running.contains(finding.id) else { return }
        running.insert(finding.id)
        runErrors[finding.id] = nil
        do {
            switch action {
            case .setup:
                _ = try await swe.applySetup(starred: [], adopt: nil)
            case .`init`:
                try await swe.runInit()
            case .discover:
                _ = try await swe.applyDiscovery()
            }
            await load()
        } catch {
            runErrors[finding.id] = error.localizedDescription
        }
        running.remove(finding.id)
    }
}
