import Foundation
import Testing
@testable import QuiverCore

@MainActor
@Suite struct DoctorModelTests {
    let problems = Data(#"""
    {"schema": 1, "data": {"ok": false, "findings": [
      {"check": "path", "severity": "error", "message": "~/.local/bin is not on PATH",
       "fix": "export PATH=\"$HOME/.local/bin:$PATH\""},
      {"check": "drift", "severity": "warning", "message": "claude skills dir is a real folder", "fix": null}
    ]}}
    """#.utf8)

    @Test func findingsLoadEvenThoughDoctorExitsNonZero() async {
        // swe doctor exits 1 when it finds problems; that is a result, not a failure.
        let model = DoctorModel(swe: SweClient(runner: FakeRunner(.init(status: 1, stdout: problems, stderr: ""))))
        await model.load()
        guard case .loaded(let report) = model.state else {
            Issue.record("expected loaded, got \(model.state)")
            return
        }
        #expect(report.ok == false)
        #expect(report.findings.map(\.severity) == [.error, .warning])
        #expect(report.findings[0].fix == #"export PATH="$HOME/.local/bin:$PATH""#)
        #expect(report.findings[1].fix == nil)
        #expect(report.findings.map(\.harness) == [nil, nil])
    }

    /// An off-PATH finding names its harness, so the page can show its logo.
    @Test func aFindingAboutOneHarnessNamesIt() async {
        let offPath = Data(#"""
        {"schema": 1, "data": {"ok": false, "findings": [
          {"check": "off-path", "severity": "error", "message": "codex (codex) is at /tmp/codex [nvm], not on PATH",
           "fix": "swe install codex", "harness": "codex"},
          {"check": "node", "severity": "warning", "message": "node not found on PATH", "fix": null}
        ]}}
        """#.utf8)
        let model = DoctorModel(swe: SweClient(runner: FakeRunner(.init(status: 1, stdout: offPath, stderr: ""))))
        await model.load()
        guard case .loaded(let report) = model.state else {
            Issue.record("expected loaded, got \(model.state)")
            return
        }
        #expect(report.findings.map(\.harness) == ["codex", nil])
    }

    @Test func aCleanReportIsAllClear() async {
        let clean = Data(#"{"schema": 1, "data": {"ok": true, "findings": []}}"#.utf8)
        let model = DoctorModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: clean, stderr: ""))))
        await model.load()
        #expect(model.state == .loaded(DoctorReport(ok: true, findings: [])))
    }

    @Test func aMissingSweIsShownAsAnError() async {
        let model = DoctorModel(swe: SweClient(runner: FakeRunner(.init(status: 127, stdout: Data(), stderr: "env: swe: No such file or directory"))))
        await model.load()
        guard case .failed(let message) = model.state else {
            Issue.record("expected failed, got \(model.state)")
            return
        }
        #expect(message.contains("No such file"))
    }

    @Test func doctorAsksForJSON() async {
        let runner = FakeRunner(.init(status: 0, stdout: Data(#"{"schema":1,"data":{"ok":true,"findings":[]}}"#.utf8), stderr: ""))
        await DoctorModel(swe: SweClient(runner: runner)).load()
        #expect(runner.calls == [["doctor", "--json"]])
    }
}

/// Replies to each call in turn, after that call's delay.
final class SequencedRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var replies: [(Duration, CommandResult)]

    init(_ replies: [(Duration, CommandResult)]) { self.replies = replies }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let (delay, reply) = lock.withLock { replies.removeFirst() }
        try await Task.sleep(for: delay)
        return reply
    }
}

@MainActor
@Suite struct DoctorModelOverlapTests {
    func report(_ check: String) -> CommandResult {
        .init(status: 1, stdout: Data(#"{"schema":1,"data":{"ok":false,"findings":[{"check":"\#(check)","severity":"warning","message":"m","fix":null}]}}"#.utf8), stderr: "")
    }

    @Test func aSlowerEarlierRefreshCannotOverwriteANewerOne() async {
        let runner = SequencedRunner([(.milliseconds(400), report("old")), (.milliseconds(10), report("new"))])
        let model = DoctorModel(swe: SweClient(runner: runner))
        async let first: Void = model.load()
        try? await Task.sleep(for: .milliseconds(50))
        async let second: Void = model.load()
        _ = await (first, second)
        guard case .loaded(let report) = model.state else {
            Issue.record("expected loaded, got \(model.state)")
            return
        }
        #expect(report.findings.map(\.check) == ["new"])
    }
}

/// `action` on a doctor finding: decode, unknown → nil, and `run` argv.
@MainActor
@Suite struct DoctorActionTests {
    let findings = Data(#"""
    {"schema": 1, "data": {"ok": false, "findings": [
      {"check": "setup.registry", "severity": "warning", "message": "No harnesses registered",
       "fix": "swe harness discover --apply", "action": "discover"},
      {"check": "setup.future", "severity": "warning", "message": "new thing",
       "fix": "swe fix-future", "action": "warp"},
      {"check": "plain", "severity": "warning", "message": "no action", "fix": null}
    ]}}
    """#.utf8)

    @Test func actionDecodesAndUnknownBecomesNil() async throws {
        let model = DoctorModel(swe: SweClient(runner: FakeRunner(.init(status: 1, stdout: findings, stderr: ""))))
        await model.load()
        guard case .loaded(let report) = model.state else {
            Issue.record("expected loaded"); return
        }
        #expect(report.findings.map(\.action) == [.discover, nil, nil])
    }

    @Test func runCallsTheActionsSweCommand() async throws {
        let runner = RoutingRunner([
            (["doctor"], .init(status: 0, stdout: findings, stderr: "")),
            (["init"], .init(status: 0, stdout: Data(), stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        let finding = DoctorReport.Finding(check: "setup.unlinked", severity: .warning,
                                         message: "not linked", fix: "swe init", action: .`init`)
        await model.run(finding)
        #expect(runner.calls.contains(["init"]))
        // A successful run re-reads the report.
        #expect(runner.calls.last == ["doctor", "--json"])
        #expect(model.running.isEmpty)
        #expect(model.runErrors.isEmpty)
    }

    @Test func discoverActionRunsDiscoverApply() async throws {
        let runner = RoutingRunner([
            (["doctor"], .init(status: 0, stdout: findings, stderr: "")),
            (["harness", "discover"], .init(status: 0, stdout: Data("ok".utf8), stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        await model.run(DoctorReport.Finding(
            check: "setup.registry", severity: .warning, message: "none",
            fix: "swe harness discover --apply", action: .discover))
        #expect(runner.calls.contains(["harness", "discover", "--apply", "--json"]))
    }

    @Test func setupActionLeavesTheStarsAlone() async throws {
        let runner = RoutingRunner([
            (["doctor"], .init(status: 0, stdout: findings, stderr: "")),
            (["setup"], .init(status: 0, stdout: Data(#"{"schema": 1, "data": {"applied": {}}}"#.utf8), stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        await model.run(DoctorReport.Finding(
            check: "setup.agents-md", severity: .warning, message: "none",
            fix: "swe setup --apply", action: .setup))
        // No --starred: an empty one would unstar every harness.
        #expect(runner.calls.contains(["setup", "--apply", "--json"]))
    }

    @Test func aFailureRecordsTheErrorAndClearsRunning() async throws {
        let runner = RoutingRunner([
            (["init"], .init(status: 1, stdout: Data(), stderr: "link would overwrite")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        let finding = DoctorReport.Finding(check: "setup.unlinked", severity: .warning,
                                         message: "not linked", fix: "swe init", action: .`init`)
        await model.run(finding)
        #expect(model.running.isEmpty)
        #expect(model.runErrors[finding.id] == "link would overwrite")
    }
}
