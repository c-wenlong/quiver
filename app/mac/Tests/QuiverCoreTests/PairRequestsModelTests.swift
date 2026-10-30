import Foundation
import Testing
@testable import QuiverCore

@MainActor
@Suite struct PairRequestsModelTests {
    private static func ok(_ json: String) -> CommandResult {
        CommandResult(status: 0, stdout: Data(json.utf8), stderr: "")
    }

    /// Two pending rows as `swe remote devices --requests --json` prints them.
    static let twoRequests = ok(#"""
    {"schema": 1, "data": {"requests": [
      {"request_id": "r1", "name": "Kai's iPhone", "node_name": "iphone.tail1.ts.net",
       "login": "kai@example.com", "peer_ip": "100.64.0.9",
       "created_at": 1760000000, "expires_in_s": 240},
      {"request_id": "r2", "name": "device", "node_name": "", "login": "",
       "peer_ip": "100.64.0.10", "created_at": 1760000100, "expires_in_s": 290}]}}
    """#)

    static let noRequests = ok(#"{"schema": 1, "data": {"requests": []}}"#)

    private func model(_ rules: [(prefix: [String], reply: CommandResult)]) -> (PairRequestsModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (PairRequestsModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func refreshDecodesTheRequestRows() async throws {
        let (model, runner) = model([(["remote", "devices", "--requests"], Self.twoRequests)])
        #expect(model.loaded == false)
        await model.refresh()
        #expect(runner.calls == [["remote", "devices", "--requests", "--json"]])
        #expect(model.loaded)
        #expect(model.requests.map(\.id) == ["r1", "r2"])
        let first = try #require(model.requests.first)
        #expect(first.name == "Kai's iPhone")
        #expect(first.nodeName == "iphone.tail1.ts.net")
        #expect(first.login == "kai@example.com")
        #expect(first.createdAt == Date(timeIntervalSince1970: 1760000000))
        #expect(first.expiresInS == 240)
    }

    @Test func aFailedRefreshKeepsTheLastListAndSaysWhy() async {
        let (model, runner) = model([(["remote", "devices", "--requests"], Self.twoRequests)])
        await model.refresh()
        runner.rules = [(["remote"], CommandResult(status: 1, stdout: Data(), stderr: "boom"))]
        await model.refresh()
        #expect(model.requests.count == 2)
        #expect(model.loadError?.contains("boom") == true)
        runner.rules = [(["remote"], Self.noRequests)]
        await model.refresh()
        #expect(model.loadError == nil)
        #expect(model.requests.isEmpty)
    }

    @Test func codesKeepSixDigitsOnly() {
        #expect(PairRequestsModel.sanitize("123 456") == "123456")
        #expect(PairRequestsModel.sanitize("12a3-4") == "1234")
        #expect(PairRequestsModel.sanitize("12345678") == "123456")
        // Non-ASCII digits (Arabic-Indic here) are not pair codes.
        #expect(PairRequestsModel.sanitize("١٢٣") == "")
    }

    @Test func decidingNeedsAWholeCode() async {
        let (model, runner) = model([(["remote", "devices", "--requests"], Self.twoRequests)])
        await model.refresh()
        let request = model.requests[0]
        #expect(model.canDecide(request) == false)
        model.setCode("12345", for: request)
        #expect(model.canDecide(request) == false)
        #expect(await model.decide(request, approve: true) == false)
        #expect(runner.calls.count == 1, "a short code never reaches swe")
        model.setCode("123456", for: request)
        #expect(model.canDecide(request))
        // Each row has its own field.
        #expect(model.code(for: model.requests[1]) == "")
    }

    @Test func approvingPinsTheCodeToItsRow() async {
        let (model, runner) = model([
            (["remote", "devices", "--requests"], Self.twoRequests),
            (["remote", "devices", "--approve=123456"],
             Self.ok(#"{"schema": 1, "data": {"ok": true, "device_id": "d1", "name": "Kai's iPhone", "node_name": "iphone"}}"#)),
        ])
        await model.refresh()
        let request = model.requests[0]
        model.setCode("123456", for: request)
        #expect(await model.decide(request, approve: true))
        #expect(runner.calls.last == ["remote", "devices", "--approve=123456", "--request-id=r1", "--json"])
        #expect(model.requests.map(\.id) == ["r2"])
        #expect(model.code(for: request) == "")
        #expect(model.deciding.isEmpty)
    }

    @Test func denyingRunsDeny() async {
        let (model, runner) = model([
            (["remote", "devices", "--requests"], Self.twoRequests),
            (["remote", "devices", "--deny=654321"], Self.ok(#"{"schema": 1, "data": {"ok": true}}"#)),
        ])
        await model.refresh()
        let request = model.requests[1]
        model.setCode("654321", for: request)
        #expect(await model.decide(request, approve: false))
        #expect(runner.calls.last == ["remote", "devices", "--deny=654321", "--request-id=r2", "--json"])
        #expect(model.requests.map(\.id) == ["r1"])
    }

    /// A wrong code exits 1 with `{"ok": false}`: the row stays, says so,
    /// and the field clears for another try.
    @Test func aWrongCodeStaysOnItsRow() async {
        let (model, _) = model([
            (["remote", "devices", "--requests"], Self.twoRequests),
            (["remote", "devices", "--approve=000000"],
             CommandResult(status: 1, stdout: Data(#"{"schema": 1, "data": {"ok": false}}"#.utf8), stderr: "")),
        ])
        await model.refresh()
        let request = model.requests[0]
        model.setCode("000000", for: request)
        #expect(await model.decide(request, approve: true) == false)
        #expect(model.requests.count == 2)
        #expect(model.errors["r1"] == "That code doesn't match this request.")
        #expect(model.code(for: request) == "")
        model.setCode("1", for: request)
        #expect(model.errors["r1"] == nil, "typing again clears the error")
    }

    @Test func aSweFailureShowsOnTheRow() async {
        let (model, _) = model([
            (["remote", "devices", "--requests"], Self.twoRequests),
            (["remote", "devices", "--approve=123456"],
             CommandResult(status: 1, stdout: Data(), stderr: "unknown flag: --request-id")),
        ])
        await model.refresh()
        let request = model.requests[0]
        model.setCode("123456", for: request)
        #expect(await model.decide(request, approve: true) == false)
        #expect(model.errors["r1"]?.contains("--request-id") == true)
    }

    /// Rows that leave the list (approved on the phone, expired, locked
    /// out) take their typed code and error with them.
    @Test func goneRowsDropTheirCodesAndErrors() async {
        let (model, runner) = model([(["remote", "devices", "--requests"], Self.twoRequests)])
        await model.refresh()
        model.setCode("12", for: model.requests[0])
        model.setCode("34", for: model.requests[1])
        runner.rules = [(["remote"], Self.ok(#"""
        {"schema": 1, "data": {"requests": [{"request_id": "r2", "created_at": 1760000100}]}}
        """#))]
        await model.refresh()
        #expect(model.codes == ["r2": "34"])
    }

    @Test func pollingStopsWhenCancelled() async throws {
        let runner = PollCountingRunner(reply: Self.noRequests)
        let model = PairRequestsModel(swe: SweClient(runner: runner))
        let task = Task { await model.poll(every: .milliseconds(10)) }
        while runner.count < 2 { try await Task.sleep(for: .milliseconds(5)) }
        task.cancel()
        await task.value
        let seen = runner.count
        try await Task.sleep(for: .milliseconds(50))
        #expect(runner.count == seen)
        #expect(model.loaded)
    }

    @Test func ageReadsLikeAClock() {
        let created = Date(timeIntervalSince1970: 1760000000)
        let request = PairRequest(requestId: "r", createdAt: created)
        #expect(PairRequestsModel.age(of: request, at: created.addingTimeInterval(3)) == "just now")
        #expect(PairRequestsModel.age(of: request, at: created.addingTimeInterval(42)) == "42s ago")
        #expect(PairRequestsModel.age(of: request, at: created.addingTimeInterval(185)) == "3m ago")
        #expect(PairRequestsModel.age(of: request, at: created.addingTimeInterval(7300)) == "2h ago")
        #expect(PairRequestsModel.age(of: PairRequest(requestId: "x"), at: created) == nil)
    }
}

/// Counts calls under a lock: the poll loop runs swe off the test's actor.
private final class PollCountingRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var calls = 0
    private let reply: CommandResult
    var count: Int { lock.withLock { calls } }

    init(reply: CommandResult) { self.reply = reply }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { calls += 1 }
        return reply
    }
}
