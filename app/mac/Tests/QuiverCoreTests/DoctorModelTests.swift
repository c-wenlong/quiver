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

    /// Run state and errors are keyed by id, so two findings whose check
    /// and message only differ in where they split must not share one.
    @Test func findingIdsKeepCheckAndMessageApart() {
        let a = DoctorReport.Finding(check: "ab", severity: .error, message: "c")
        let b = DoctorReport.Finding(check: "a", severity: .error, message: "bc")
        #expect(a.id != b.id)
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
        #expect(message.contains("Couldn't find swe"))
        #expect(message.contains("Settings"))
    }

    @Test func doctorAsksForJSON() async {
        let runner = FakeRunner(.init(status: 0, stdout: Data(#"{"schema":1,"data":{"ok":true,"findings":[]}}"#.utf8), stderr: ""))
        await DoctorModel(swe: SweClient(runner: runner)).load()
        #expect(runner.calls == [["doctor", "--json"]])
    }
}

/// Replies to each call in turn.
final class SequencedRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var replies: [CommandResult]

    init(_ replies: [CommandResult]) { self.replies = replies }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { replies.removeFirst() }
    }
}

@MainActor
@Suite struct DoctorModelOverlapTests {
    func report(_ check: String) -> CommandResult {
        .init(status: 1, stdout: Data(#"{"schema":1,"data":{"ok":false,"findings":[{"check":"\#(check)","severity":"warning","message":"m","fix":null}]}}"#.utf8), stderr: "")
    }

    @Test(.timeLimit(.minutes(1))) func aSlowerEarlierRefreshCannotOverwriteANewerOne() async {
        let runner = GatedReplies([report("old"), report("new")])
        let model = DoctorModel(swe: SweClient(runner: runner))
        let first = Task { await model.load() }
        #expect(await runner.gates[0].arrived())
        let second = Task { await model.load() }
        #expect(await runner.gates[1].arrived())
        // The newer refresh answers first; the older one lands after it.
        runner.gates[1].open()
        await second.value
        runner.gates[0].open()
        await first.value
        guard case .loaded(let report) = model.state else {
            Issue.record("expected loaded, got \(model.state)")
            return
        }
        #expect(report.findings.map(\.check) == ["new"])
    }

    /// A load cancelled with its page puts the previous state back: left
    /// at .loading, the spinner and isBusy stayed on for good.
    @Test(.timeLimit(.minutes(1))) func aCancelledLoadDoesNotSpinForever() async throws {
        // No replies: the one call hangs until the load is cancelled.
        let runner = GatedReplies([])
        let model = DoctorModel(swe: SweClient(runner: runner))
        let task = Task { await model.load() }
        #expect(await runner.hanging())
        task.cancel()
        await task.value
        #expect(model.state == .idle)
        #expect(!model.isBusy)
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
        let runner = ScriptedRunner([
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
        let runner = ScriptedRunner([
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
        let runner = ScriptedRunner([
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

    @Test func setupAndDiscoverWaitForTheRegistryGate() async throws {
        for action in [DoctorReport.Finding.Action.setup, .discover] {
            let runner = RoutingRunner([
                (["doctor"], .init(status: 0, stdout: findings, stderr: "")),
                (["setup"], .init(status: 0, stdout: Data(#"{"schema": 1, "data": {"applied": {}}}"#.utf8), stderr: "")),
                (["harness", "discover"], .init(status: 0, stdout: Data("ok".utf8), stderr: "")),
            ])
            let model = DoctorModel(swe: SweClient(runner: runner))
            // A Harnesses page edit holds the gate.
            await HarnessesModel.registryWrites.acquire()
            let run = Task { await model.run(DoctorReport.Finding(
                check: "setup.registry", severity: .warning, message: "none",
                fix: nil, action: action)) }
            // Either the run queues at the gate or, without it, reaches swe.
            while HarnessesModel.registryWrites.waiters == 0, runner.calls.isEmpty {
                await Task.yield()
            }
            #expect(runner.calls.isEmpty, "\(action) ran while the gate was held")
            HarnessesModel.registryWrites.release()
            await run.value
            #expect(!runner.calls.isEmpty)
        }
    }

    @Test func aFailureRecordsTheErrorAndClearsRunning() async throws {
        let runner = ScriptedRunner([
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

/// `install`: swe doctor names the method; Run installs in the app.
@MainActor
@Suite struct DoctorInstallTests {
    let offPath = Data(#"""
    {"schema": 1, "data": {"ok": false, "findings": [
      {"check": "off-path", "severity": "error", "message": "codex (codex) is at /tmp/codex [nvm], not on PATH",
       "fix": "swe install codex --via=npm", "harness": "codex", "action": "install", "via": "npm"},
      {"check": "off-path", "severity": "error", "message": "jules (jules) is at /tmp/jules [nvm], not on PATH",
       "fix": "swe install jules", "harness": "jules", "action": "install"}
    ]}}
    """#.utf8)
    let clean = Data(#"{"schema": 1, "data": {"ok": true, "findings": []}}"#.utf8)
    let codex = DoctorReport.Finding(check: "off-path", severity: .error, message: "codex off PATH",
                                     fix: "swe install codex --via=npm", harness: "codex",
                                     action: .install, via: "npm")

    @Test func installDecodesWithItsMethodAndNeedsOne() async {
        let model = DoctorModel(swe: SweClient(runner: FakeRunner(.init(status: 1, stdout: offPath, stderr: ""))))
        await model.load()
        guard case .loaded(let report) = model.state else { Issue.record("expected loaded"); return }
        #expect(report.findings.map(\.action) == [.install, nil])
        #expect(report.findings.map(\.via) == ["npm", nil])
    }

    @Test func runInstallsInTheAppAndKeepsWhatItPrinted() async {
        let runner = ScriptedRunner([
            (["install"], .init(status: 0, stdout: Data("added 1 package\nRegistered codex".utf8), stderr: "")),
            (["doctor"], .init(status: 0, stdout: clean, stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        await model.run(codex)
        #expect(runner.calls.contains(["install", "codex", "--via=npm"]))
        #expect(runner.calls.last == ["doctor", "--json"])
        #expect(model.installed?.harness == "codex")
        #expect(model.installed?.output == "added 1 package\nRegistered codex")
        #expect(model.runErrors.isEmpty)
        #expect(model.runStarted == nil)
        model.dismissInstalled()
        #expect(model.installed == nil)
    }

    @Test func aFailedInstallKeepsItsOutputOnTheRow() async {
        let runner = ScriptedRunner([
            (["install"], .init(status: 1, stdout: Data("==> Downloading".utf8), stderr: "sudo: a password is required")),
            (["doctor"], .init(status: 1, stdout: offPath, stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        await model.run(codex)
        #expect(model.installed == nil)
        #expect(model.runErrors[codex.id] == "swe install codex exited 1.")
        #expect(model.installOutput[codex.id] == "==> Downloading\nsudo: a password is required")
        #expect(model.running.isEmpty)
    }

    @Test func onlyTheEndOfALongLogIsKept() async {
        let log = (1...500).map { "line \($0)" }.joined(separator: "\n")
        let runner = ScriptedRunner([
            (["install"], .init(status: 1, stdout: Data(log.utf8), stderr: "")),
            (["doctor"], .init(status: 1, stdout: offPath, stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        await model.run(codex)
        let kept = model.installOutput[codex.id]?.split(separator: "\n") ?? []
        #expect(kept.count == 40)
        #expect(kept.last == "line 500")
    }

    @Test func theTailKeepsTheLastLines() {
        let text = (1...50).map(String.init).joined(separator: "\n")
        #expect(DoctorModel.outputTail(text, lines: 3) == "48\n49\n50")
    }
}

/// The run guard, failure reload, and the terminal line rewrite.
@MainActor
@Suite struct DoctorRunDetailsTests {
    private func doctorData() -> Data {
        Data(#"{"schema": 1, "data": {"ok": true, "findings": []}}"#.utf8)
    }

    @Test func aSecondRunWaitsWhileOneIsInFlight() async throws {
        // Not directly observable without a suspendable runner; the guard
        // is `running.isEmpty`, so a finding queued mid-run returns at once.
        let runner = ScriptedRunner([
            (["init"], .init(status: 0, stdout: Data(), stderr: "")),
            (["doctor"], .init(status: 0, stdout: doctorData(), stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        model.running = ["setup.unlinked x"]
        await model.run(DoctorReport.Finding(
            check: "setup.unlinked", severity: .warning, message: "y",
            fix: "swe init", action: .`init`))
        #expect(!runner.calls.contains(["init"]))
    }

    @Test func aFailedRunStillReloadsAndKeepsTheError() async throws {
        let runner = ScriptedRunner([
            (["init"], .init(status: 1, stdout: Data(), stderr: "boom")),
            (["doctor"], .init(status: 0, stdout: doctorData(), stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        let finding = DoctorReport.Finding(check: "setup.unlinked", severity: .warning,
                                         message: "x", fix: "swe init", action: .`init`)
        await model.run(finding)
        #expect(runner.calls.last == ["doctor", "--json"])
        #expect(model.runErrors[finding.id] == "boom")
        #expect(model.running.isEmpty)
    }

    @Test func aSetupWithALinkErrorRecordsIt() async throws {
        let setupData = Data(#"""
        {"schema": 1, "data": {"applied": {"harness": [], "mcp": [],
         "links": {"adopted": null, "error": "codex AGENTS.md is a real file", "rows": []}}}}
        """#.utf8)
        let runner = ScriptedRunner([
            (["setup"], .init(status: 1, stdout: setupData, stderr: "")),
            (["doctor"], .init(status: 0, stdout: doctorData(), stderr: "")),
        ])
        let model = DoctorModel(swe: SweClient(runner: runner))
        let finding = DoctorReport.Finding(check: "setup.agents-md", severity: .warning,
                                         message: "x", fix: "swe setup --apply", action: .setup)
        await model.run(finding)
        #expect(model.runErrors[finding.id] == "codex AGENTS.md is a real file")
    }

    @Test func terminalLineSwapsSweForTheQuotedExecutable() {
        #expect(DoctorModel.terminalLine(fix: "swe init", swe: "/a b/swe")
            == "'/a b/swe' init")
        #expect(DoctorModel.terminalLine(fix: "swe doctor", swe: "/opt/swe")
            == "'/opt/swe' doctor")
        #expect(DoctorModel.terminalLine(fix: "export PATH=x", swe: "/opt/swe")
            == "export PATH=x")
    }
}
