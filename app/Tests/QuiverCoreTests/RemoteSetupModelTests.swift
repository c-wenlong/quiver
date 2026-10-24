import Foundation
import Testing
@testable import QuiverCore

// The swe-remote-setup-*.json fixtures are `setup.evaluate` output for
// made-up facts (the Python tests' `facts()` with pieces taken away), so
// their shape is exactly what src/quiver/remote/setup.py emits without
// needing a real tailnet: fresh (no Tailscale), todo (certificate not
// fetched), pair (everything up, no phone) and ready.

/// A reply the test lets go of by hand, so a newer answer can land first.
final class HeldReply: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<CommandResult, Never>?
    private var released: CommandResult?
    private var arrived = false
    private var arrivalWaiters: [CheckedContinuation<Void, Never>] = []

    func wait() async -> CommandResult {
        await withCheckedContinuation { c in
            lock.lock()
            arrived = true
            let waiters = arrivalWaiters
            arrivalWaiters = []
            if let released {
                lock.unlock()
                c.resume(returning: released)
            } else {
                continuation = c
                lock.unlock()
            }
            waiters.forEach { $0.resume() }
        }
    }

    /// Returns once the command has been asked for.
    func untilArrived() async {
        await withCheckedContinuation { c in
            lock.lock()
            if arrived {
                lock.unlock()
                c.resume()
            } else {
                arrivalWaiters.append(c)
                lock.unlock()
            }
        }
    }

    func release(_ result: CommandResult) {
        lock.lock()
        let c = continuation
        continuation = nil
        if c == nil { released = result }
        lock.unlock()
        c?.resume(returning: result)
    }
}

/// Answers each call with the next scripted reply; past the script, the
/// last immediate reply again. Records arguments and deadlines.
final class SetupScriptRunner: CommandRunning, @unchecked Sendable {
    enum Reply {
        case now(CommandResult)
        case held(HeldReply)
    }

    private let lock = NSLock()
    private var script: [Reply]
    private var fallback: CommandResult?
    private var _calls: [[String]] = []
    private var _timeouts: [Duration?] = []

    init(_ script: [Reply]) { self.script = script }

    var calls: [[String]] { lock.withLock { _calls } }
    var timeouts: [Duration?] { lock.withLock { _timeouts } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        try await run(arguments, input: nil, timeout: nil)
    }

    func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
        try await run(arguments, input: input, timeout: nil)
    }

    func run(_ arguments: [String], input: Data?, timeout: Duration?) async throws -> CommandResult {
        let reply: Reply? = lock.withLock {
            _calls.append(arguments)
            _timeouts.append(timeout)
            guard !script.isEmpty else { return fallback.map { .now($0) } }
            let next = script.removeFirst()
            if case .now(let result) = next { fallback = result }
            return next
        }
        switch reply {
        case .now(let result): return result
        case .held(let held): return await held.wait()
        case nil: return CommandResult(status: 1, stdout: Data(), stderr: "no reply scripted")
        }
    }
}

/// Stands in for the poll's sleep: each one waits for `tick()`, and a
/// cancelled task's sleep throws, as `Task.sleep` does.
final class Ticker: @unchecked Sendable {
    private let lock = NSLock()
    private var sleeper: CheckedContinuation<Void, any Error>?
    private var started = 0
    private var waiters: [(count: Int, continuation: CheckedContinuation<Void, Never>)] = []

    var sleeps: Int { lock.withLock { started } }

    func sleep(_: Duration) async throws {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, any Error>) in
                lock.lock()
                if Task.isCancelled {
                    lock.unlock()
                    c.resume(throwing: CancellationError())
                    return
                }
                started += 1
                sleeper = c
                let due = waiters.filter { $0.count <= started }
                waiters.removeAll { $0.count <= started }
                lock.unlock()
                due.forEach { $0.continuation.resume() }
            }
        } onCancel: {
            lock.lock()
            let c = sleeper
            sleeper = nil
            lock.unlock()
            c?.resume(throwing: CancellationError())
        }
    }

    /// Returns once `count` sleeps have started.
    func untilSleeping(_ count: Int) async {
        await withCheckedContinuation { c in
            lock.lock()
            if started >= count {
                lock.unlock()
                c.resume()
            } else {
                waiters.append((count, c))
                lock.unlock()
            }
        }
    }

    func tick() {
        lock.lock()
        let c = sleeper
        sleeper = nil
        lock.unlock()
        c?.resume()
    }
}

@MainActor
@Suite struct RemoteSetupModelTests {
    static let ids = ["tailscale.installed", "tailscale.signed_in", "tailscale.magicdns",
                      "tailscale.https", "tailscale.cert", "hub.service", "hub.reachable",
                      "phone.paired"]

    func reply(_ name: String, status: Int32 = 0) throws -> CommandResult {
        CommandResult(status: status, stdout: try fixture(name), stderr: "")
    }

    func model(_ runner: CommandRunning, ticker: Ticker = Ticker()) -> RemoteSetupModel {
        let swe = SweClient(runner: runner)
        return RemoteSetupModel(swe: { swe }, sleep: { try await ticker.sleep($0) })
    }

    // MARK: decoding

    @Test func aFreshMacPointsAtTheDownloadAndBlocksTheRest() async throws {
        let report = try await SweClient(runner: FakeRunner(reply("swe-remote-setup-fresh"))).remoteSetup()
        #expect(report.ready == false)
        #expect(report.platform == "darwin")
        #expect(report.url == "")
        #expect(report.steps.map(\.id) == Self.ids)
        let first = try #require(report.steps.first)
        #expect(first.state == .needsYou)
        #expect(first.action == .init(kind: .open, label: "Download Tailscale",
                                      url: "https://tailscale.com/download/mac"))
        #expect(report.steps.dropFirst().allSatisfy { $0.state == .blocked && $0.action == nil })
        #expect(report.current?.id == "tailscale.installed")
        #expect(report.canApply == false)
    }

    @Test func aStepQuiverCanDoOffersApply() async throws {
        let report = try await SweClient(runner: FakeRunner(reply("swe-remote-setup-todo"))).remoteSetup()
        #expect(report.current?.id == "tailscale.cert")
        #expect(report.current?.state == .todo)
        #expect(report.current?.action?.kind == .apply)
        #expect(report.canApply)
        #expect(report.applyLabels == ["Fetch certificate"])
        #expect(report.url == "https://mac.tail1234.ts.net:8477")
    }

    @Test func aHubWithNoPhoneOffersPairing() async throws {
        let report = try await SweClient(runner: FakeRunner(reply("swe-remote-setup-pair"))).remoteSetup()
        #expect(report.current?.id == "phone.paired")
        #expect(report.current?.state == .needsYou)
        #expect(report.current?.action?.kind == .pair)
        #expect(report.canApply == false)
    }

    @Test func aReadyReportHasNothingLeft() async throws {
        let report = try await SweClient(runner: FakeRunner(reply("swe-remote-setup-ready"))).remoteSetup()
        #expect(report.ready)
        #expect(report.current == nil)
        #expect(report.steps.allSatisfy { $0.state == .ok })
        #expect(report.steps.last?.detail == "2 devices")
    }

    @Test func aNewerSwesStatesAndKindsDecodeSafely() async throws {
        let body = Data(#"""
        {"schema": 1, "data": {"ready": false, "platform": "darwin", "url": "",
         "steps": [{"id": "x.new", "title": "Something new", "state": "skipped", "detail": "",
                    "action": {"kind": "teleport", "label": "Beam up"}},
                   {"id": "tailscale.signed_in", "title": "Signed in", "state": "needs_you",
                    "detail": "Open it", "action": {"kind": "launch", "label": "Open Tailscale",
                                                    "app": "Tailscale"}},
                   {"id": "hub.service", "title": "Hub", "state": "needs_you", "detail": "",
                    "action": {"kind": "command", "label": "Copy command",
                               "command": "swe remote serve --public-url=https://x"}}]}}
        """#.utf8)
        let report = try await SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))).remoteSetup()
        #expect(report.steps[0].state == .blocked)
        #expect(report.steps[0].action?.kind == .unknown)
        #expect(report.steps[1].action?.app == "Tailscale")
        #expect(report.steps[2].action?.command == "swe remote serve --public-url=https://x")
    }

    /// Shapes from #683's review fixes, built to the coordinator's
    /// description: a shell hub holding the port, a LaunchAgent to move,
    /// and phones stranded by a move.
    @Test func aHubToMoveAndAShellHubReadAsTheirOwnActions() async throws {
        let body = Data(#"""
        {"schema": 1, "data": {"ready": false, "platform": "darwin",
         "url": "https://mac.tail1234.ts.net:8477",
         "steps": [{"id": "hub.service", "title": "Hub running in the background", "state": "needs_you",
                    "detail": "A hub started from a shell holds port 8477. Stop it.",
                    "action": {"kind": "command", "command": "pkill -f 'swe remote serve'"}},
                   {"id": "hub.service", "title": "Hub running in the background", "state": "todo",
                    "detail": "Runs on its tailnet address today; phones paired before must pair again",
                    "action": {"kind": "apply", "label": "Move hub"}},
                   {"id": "phone.paired", "title": "A phone paired", "state": "needs_you",
                    "detail": "The hub moved to https://mac.tail1234.ts.net:8477. Pair your phones again.",
                    "action": {"kind": "pair", "label": "Pair a phone"}}]}}
        """#.utf8)
        let report = try await SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))).remoteSetup()
        #expect(report.steps[0].action?.kind == .command)
        #expect(report.steps[0].action?.command == "pkill -f 'swe remote serve'")
        #expect(report.steps[0].action?.label == "Copy command")
        #expect(report.applyLabels == ["Move hub"])
        #expect(report.steps[2].state == .needsYou)
        #expect(report.steps[2].action?.kind == .pair)
    }

    /// A hub that keeps stopping is `failed` with "Restart hub", which
    /// `--apply` retries: Set up must still be offered.
    @Test func aFailedStepWithAnApplyCanStillBeApplied() {
        let report = RemoteSetupReport(ready: false, platform: "darwin", url: "https://x", steps: [
            .init(id: "tailscale.installed", title: "Tailscale installed", state: .ok),
            .init(id: "hub.service", title: "Hub running in the background", state: .failed,
                  detail: "The hub keeps stopping.", action: .init(kind: .apply, label: "Restart hub")),
            .init(id: "hub.reachable", title: "Reachable", state: .blocked),
        ])
        #expect(report.canApply)
        #expect(report.applyLabels == ["Restart hub"])
        // A failure with only a page to open is the person's to fix.
        let certFailed = RemoteSetupReport(ready: false, platform: "darwin", url: "", steps: [
            .init(id: "tailscale.cert", title: "Certificate", state: .failed, detail: "lookup timeout",
                  action: .init(kind: .open, label: "Open admin console", url: "https://login.tailscale.com/admin/dns")),
        ])
        #expect(certFailed.canApply == false)
    }

    // MARK: commands

    @Test func applyAsksForApplyWithALongerDeadline() async throws {
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-todo")),
                                     .now(try reply("swe-remote-setup-pair"))])
        let setup = model(runner)
        await setup.load()
        await setup.apply()
        #expect(runner.calls == [["remote", "setup", "--json"], ["remote", "setup", "--apply", "--json"]])
        #expect(runner.timeouts == [nil, SweClient.remoteSetupApplyTimeout])
        #expect(setup.report?.current?.id == "phone.paired")
    }

    @Test func anApplyThatStopsShortStillShowsItsReport() async throws {
        // --apply exits 1 when the machine still isn't ready.
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-pair", status: 1))])
        let setup = model(runner)
        await setup.apply()
        #expect(setup.applyError == nil)
        #expect(setup.report?.current?.id == "phone.paired")
        #expect(setup.applying == false)
    }

    @Test func aFailedApplySaysWhyAndKeepsTheReport() async throws {
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-todo")),
                                     .now(.init(status: 1, stdout: Data(), stderr: "launchctl said no"))])
        let setup = model(runner)
        await setup.load()
        await setup.apply()
        #expect(setup.applyError?.contains("launchctl said no") == true)
        #expect(setup.report?.current?.id == "tailscale.cert")
    }

    @Test func aFailedLookKeepsTheLastReport() async throws {
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-todo")),
                                     .now(.init(status: 1, stdout: Data(), stderr: "unknown subcommand: setup"))])
        let setup = model(runner)
        await setup.load()
        await setup.load()
        #expect(setup.loadError?.contains("unknown subcommand") == true)
        #expect(setup.report?.current?.id == "tailscale.cert")
        #expect(setup.loading == false)
    }

    @Test func applyRunsOneAtATime() async throws {
        let held = HeldReply()
        let runner = SetupScriptRunner([.held(held)])
        let setup = model(runner)
        let first = Task { await setup.apply() }
        await held.untilArrived()
        #expect(setup.applying)
        await setup.apply()
        #expect(runner.calls.count == 1)
        held.release(try reply("swe-remote-setup-ready"))
        await first.value
        #expect(setup.applying == false)
        #expect(setup.isReady)
    }

    // MARK: polling

    @Test func aReadyReportNeedsNoPolling() async throws {
        let ticker = Ticker()
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-ready"))])
        await model(runner, ticker: ticker).watch()
        #expect(runner.calls.count == 1)
        #expect(ticker.sleeps == 0)
    }

    @Test func itPollsUntilTheReportIsReady() async throws {
        let ticker = Ticker()
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-todo")),
                                     .now(try reply("swe-remote-setup-pair")),
                                     .now(try reply("swe-remote-setup-ready"))])
        let setup = model(runner, ticker: ticker)
        let watching = Task { await setup.watch() }
        await ticker.untilSleeping(1)
        #expect(runner.calls.count == 1)
        #expect(setup.report?.current?.id == "tailscale.cert")
        ticker.tick()
        await ticker.untilSleeping(2)
        #expect(runner.calls.count == 2)
        #expect(setup.report?.current?.id == "phone.paired")
        ticker.tick()
        await watching.value
        #expect(runner.calls.count == 3)
        #expect(setup.isReady)
        #expect(ticker.sleeps == 2)
    }

    @Test func closingTheSheetStopsThePoll() async throws {
        let ticker = Ticker()
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-todo"))])
        let setup = model(runner, ticker: ticker)
        let watching = Task { await setup.watch() }
        await ticker.untilSleeping(1)
        watching.cancel()
        await watching.value
        #expect(runner.calls.count == 1)
        #expect(ticker.sleeps == 1)
    }

    @Test func thePollWaitsWhileApplyRuns() async throws {
        let ticker = Ticker()
        let applying = HeldReply()
        let runner = SetupScriptRunner([.now(try reply("swe-remote-setup-todo")), .held(applying)])
        let setup = model(runner, ticker: ticker)
        let watching = Task { await setup.watch() }
        await ticker.untilSleeping(1)
        let apply = Task { await setup.apply() }
        await applying.untilArrived()
        // This tick falls while --apply runs: no look of its own.
        ticker.tick()
        await ticker.untilSleeping(2)
        #expect(runner.calls == [["remote", "setup", "--json"], ["remote", "setup", "--apply", "--json"]])
        applying.release(try reply("swe-remote-setup-ready"))
        await apply.value
        #expect(setup.isReady)
        ticker.tick()
        await watching.value
        #expect(runner.calls.count == 2)
    }

    // MARK: stale answers

    @Test func aSlowOlderLookNeverReplacesANewerOne() async throws {
        let slow = HeldReply()
        let runner = SetupScriptRunner([.held(slow), .now(try reply("swe-remote-setup-ready"))])
        let setup = model(runner)
        let older = Task { await setup.load() }
        await slow.untilArrived()
        await setup.load()
        #expect(setup.isReady)
        slow.release(try reply("swe-remote-setup-fresh"))
        await older.value
        #expect(setup.isReady)
        #expect(setup.loading == false)
    }

    @Test func aLookInFlightNeverReplacesAnApply() async throws {
        let look = HeldReply()
        let apply = HeldReply()
        let runner = SetupScriptRunner([.held(look), .held(apply)])
        let setup = model(runner)
        let looking = Task { await setup.load() }
        await look.untilArrived()
        let applying = Task { await setup.apply() }
        await apply.untilArrived()
        #expect(setup.loading == false)
        apply.release(try reply("swe-remote-setup-ready"))
        await applying.value
        look.release(try reply("swe-remote-setup-todo"))
        await looking.value
        #expect(setup.isReady)
    }

    @Test func aFailureFromAnOlderLookIsDroppedToo() async throws {
        let slow = HeldReply()
        let runner = SetupScriptRunner([.held(slow), .now(try reply("swe-remote-setup-pair"))])
        let setup = model(runner)
        let older = Task { await setup.load() }
        await slow.untilArrived()
        await setup.load()
        slow.release(.init(status: 1, stdout: Data(), stderr: "timed out"))
        await older.value
        #expect(setup.loadError == nil)
        #expect(setup.report?.current?.id == "phone.paired")
    }
}
