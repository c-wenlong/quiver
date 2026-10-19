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
    /// `internal(set)` so a test can stage one in flight.
    public internal(set) var running: Set<DoctorReport.Finding.ID> = []
    public internal(set) var runErrors: [DoctorReport.Finding.ID: String] = [:]
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

    /// One repair at a time: a run in flight keeps the others waiting.
    public func run(_ finding: DoctorReport.Finding) async {
        guard let action = finding.action, running.isEmpty else { return }
        running.insert(finding.id)
        runErrors[finding.id] = nil
        do {
            switch action {
            case .setup:
                let outcome = try await swe.applySetup(starred: nil, adopt: nil)
                // A linking pass that stops early still counts as a
                // repair for the report, but its reason shows on the row.
                if let stopped = outcome.links?.error {
                    runErrors[finding.id] = stopped
                }
            case .`init`:
                try await swe.runInit()
            case .discover:
                _ = try await swe.applyDiscovery()
            }
        } catch {
            runErrors[finding.id] = error.localizedDescription
        }
        // Even a failed run may have fixed something (a partial `swe
        // init`), so re-read the report either way.
        await load()
        running.remove(finding.id)
    }

    /// A run the page did itself (Run in Terminal) still reports under
    /// the row.
    public func noteError(_ message: String, for id: DoctorReport.Finding.ID) {
        runErrors[id] = message
    }

    /// The line Run in Terminal types: a leading bare `swe` becomes the
    /// quoted executable the page would use itself, so the bundled one works.
    public static func terminalLine(fix: String, swe: String) -> String {
        guard fix.hasPrefix("swe") else { return fix }
        return LaunchPlanner.shellQuote(swe) + fix.dropFirst(3)
    }
}
