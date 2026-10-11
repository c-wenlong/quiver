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
