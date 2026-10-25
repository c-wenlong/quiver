import Foundation
import Testing
@testable import QuiverCore

/// The `swe hooks` payloads are captured from a real swe run against a
/// throwaway HOME; see swe-hooks*.json in Fixtures and SweContractTests.
@MainActor
@Suite struct HooksModelTests {
    let listJSON = #"""
    {"hooks": [
      {"script": "prose-guard.py", "harness": "claude",
       "source": "/h/.quiver/hooks/claude/prose-guard.py",
       "destination": "/h/.claude/hooks/prose-guard.py",
       "state": "linked", "detail": "",
       "declared": [{"event": "PreToolUse", "matcher": "Bash", "timeout": 10,
                     "command": "~/.claude/hooks/prose-guard.py",
                     "settings": "/h/.claude/settings.json"}]},
      {"script": "night-watch.py", "harness": "claude",
       "source": "/h/.quiver/hooks/claude/night-watch.py",
       "destination": "/h/.claude/hooks/night-watch.py",
       "state": "create", "detail": "", "declared": []},
      {"script": "block-rm.py", "harness": "codex",
       "source": "/h/.quiver/hooks/codex/block-rm.py",
       "destination": "/h/.codex/hooks/block-rm.py",
       "state": "create", "detail": "",
       "declared": [{"event": "SessionStart", "matcher": "", "timeout": null,
                     "command": "~/.codex/hooks/block-rm.py",
                     "settings": "/h/.codex/hooks.json"}]},
      {"script": "probe.py", "harness": "fixturetool",
       "source": "/h/.quiver/hooks/fixturetool/probe.py",
       "destination": "/h/.quiver/hooks/fixturetool/probe.py",
       "state": "skipped",
       "detail": "no hooks root, set capabilities.hooks.root in harness.json",
       "declared": []}
    ]}
    """#

    /// The same list as a newer swe sends it, with tier, enabled and
    /// events_supported added.
    let tieredJSON = #"""
    {"hooks": [
      {"script": "prose-guard.py", "harness": "shared",
       "source": "/h/.quiver/hooks/shared/prose-guard.py",
       "destination": "/h/.claude/hooks/prose-guard.py",
       "state": "linked", "detail": "", "declared": [],
       "tier": "shared", "enabled": true,
       "events_supported": ["PreToolUse", "PostToolUse"]},
      {"script": "beacon.py", "harness": "claude",
       "source": "/h/.quiver/hooks/claude/beacon.py",
       "destination": "/h/.claude/hooks/beacon.py",
       "state": "linked", "detail": "", "declared": [],
       "tier": "harness", "enabled": false},
      {"script": "stray.py", "harness": "codex",
       "source": "/h/.codex/hooks/stray.py",
       "destination": "/h/.codex/hooks/stray.py",
       "state": "keep", "detail": "", "declared": [],
       "tier": "foreign"}
    ]}
    """#

    let checkJSON = #"""
    {"findings": [
      {"kind": "declared-missing", "harness": "claude",
       "message": "declared ghost.py (PreToolUse:*) but no such script is on disk"},
      {"kind": "undeclared", "harness": "claude",
       "message": "beacon.py is linked into /h/.claude/hooks but no settings file declares it"},
      {"kind": "unlinked", "harness": "claude",
       "message": "night-watch.py is not linked into /h/.claude/hooks (swe init will link it)"}
    ]}
    """#

    /// A runner answering hooks list and check; pass `initReply` to also
    /// answer swe init.
    func runner(_ list: CommandResult, check: CommandResult? = nil,
                initReply: CommandResult? = nil) -> ScriptedRunner {
        var rules: [(prefix: [String], reply: CommandResult)] = [
            (["hooks", "list", "--json"], list),
            (["hooks", "check", "--json"], check ?? ok(#"{"findings": []}"#)),
        ]
        if let initReply { rules.append((["init"], initReply)) }
        return ScriptedRunner(rules)
    }

    @Test func loadsHooksAndFindingsFromSwe() async throws {
        let runner = self.runner(ok(listJSON), check: ok(checkJSON))
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.contains(["hooks", "list", "--json"]))
        #expect(runner.calls.contains(["hooks", "check", "--json"]))
        #expect(model.hooks.map(\.script) == ["prose-guard.py", "night-watch.py", "block-rm.py", "probe.py"])
        #expect(model.findings.map(\.kind) == ["declared-missing", "undeclared", "unlinked"])
        let guard_ = try #require(model.hooks.first)
        #expect(guard_.state == .linked)
        #expect(guard_.declared.first?.label == "PreToolUse:Bash")
        #expect(guard_.declared.first?.timeout == 10)
        let probe = try #require(model.hooks.last)
        #expect(probe.state == .skipped)
        #expect(probe.detail.contains("no hooks root"))
    }

    @Test func anEmptyInventoryReadsFine() async {
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner(ok(#"{"hooks": []}"#))), for: "/bin/swe")
        #expect(model.hooks.isEmpty)
        #expect(model.findings.isEmpty)
        guard case .loaded = model.state else { Issue.record("\(model.state)"); return }
    }

    @Test func aFailedLoadSaysWhy() async {
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner(
            CommandResult(status: 1, stdout: Data(), stderr: "Unknown command: 'hooks'"))),
            for: "/bin/swe")
        guard case .failed(let message) = model.state else { Issue.record("\(model.state)"); return }
        #expect(message.contains("Unknown command"))
    }

    @Test func aFailedCheckDoesNotHideTheList() async throws {
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner(
            ok(listJSON), check: CommandResult(status: 1, stdout: Data(), stderr: "check broke"))),
            for: "/bin/swe")
        guard case .loaded(let inventory) = model.state else { Issue.record("\(model.state)"); return }
        #expect(inventory.hooks.count == 4)
        #expect(inventory.findings.isEmpty)
        #expect(inventory.checkError != nil)
    }

    @Test func aCheckWithFindingsDecodesThroughItsNonzeroExit() async throws {
        // swe exits 1 while findings remain; the payload is still a report.
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner(
            ok(listJSON), check: json(checkJSON, status: 1))), for: "/bin/swe")
        #expect(model.findings.map(\.kind) == ["declared-missing", "undeclared", "unlinked"])
        #expect(model.findings[1].kindTitle == "Undeclared")
        let nightWatch = try #require(model.hooks.first { $0.script == "night-watch.py" })
        #expect(!nightWatch.isUndeclared)
    }

    @Test func rowsGroupByTierWhenTheListCarriesOne() async throws {
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner(ok(tieredJSON))), for: "/bin/swe")
        #expect(model.hasTiers)
        #expect(model.groups.map(\.key) == ["shared", "claude", "foreign"])
        let shared = try #require(model.groups.first)
        let hook = try #require(shared.hooks.first)
        #expect(hook.tier == .shared)
        #expect(hook.enabled == true)
        #expect(hook.eventsSupported == ["PreToolUse", "PostToolUse"])
        #expect(model.groups[1].hooks.first?.enabled == false)
    }

    @Test func rowsGroupByHarnessWithoutTiers() async throws {
        let model = HooksModel()
        await model.load(swe: SweClient(runner: runner(ok(listJSON))), for: "/bin/swe")
        #expect(!model.hasTiers)
        #expect(model.groups.map(\.key) == ["claude", "codex", "fixturetool"])
        #expect(model.groups.first?.hooks.map(\.script) == ["prose-guard.py", "night-watch.py"])
        // Fields a newer swe adds still decode to nil on the current one.
        #expect(model.hooks.first?.tier == nil)
        #expect(model.hooks.first?.enabled == nil)
        #expect(model.hooks.first?.eventsSupported == nil)
    }

    @Test func linkRunsSweInitThenReloads() async throws {
        let runner = self.runner(ok(listJSON),
                                 initReply: CommandResult(status: 0, stdout: Data(), stderr: ""))
        let model = HooksModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe, for: "/bin/swe")
        await model.link(swe: swe, for: "/bin/swe")
        #expect(runner.calls.contains(["init"]))
        #expect(!runner.calls.contains(["init", "--force"]))
        // The list is read again after the link.
        #expect(runner.calls.filter { $0 == ["hooks", "list", "--json"] }.count == 2)
        #expect(model.actionError == nil)
        #expect(!model.linking)
    }

    @Test func aForcedLinkPassesForce() async throws {
        let runner = self.runner(ok(listJSON),
                                 initReply: CommandResult(status: 0, stdout: Data(), stderr: ""))
        let model = HooksModel()
        await model.link(force: true, swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.first == ["init", "--force"])
    }

    @Test func newHookRunsSweHooksNewThenReloads() async throws {
        let runner = self.runner(ok(listJSON))
        runner.rules.insert((["hooks", "new"], ok(#"""
            {"path": "/h/.quiver/hooks/claude/guard.py", "harness": "claude",
             "command": "~/.claude/hooks/guard.py",
             "settings": ["/h/.claude/settings.json"],
             "stanza": "{ }"}
            """#)), at: 0)
        let model = HooksModel()
        let swe = SweClient(runner: runner)
        let reply = try await model.create(harness: "claude", name: "guard",
                                           swe: swe, for: "/bin/swe")
        #expect(runner.calls.first == ["hooks", "new", "claude", "guard", "--json"])
        #expect(reply.command == "~/.claude/hooks/guard.py")
        #expect(reply.settings == ["/h/.claude/settings.json"])
        #expect(!model.creating)
        #expect(runner.calls.contains(["hooks", "list", "--json"]))
    }

    @Test func linkRunsOneAtATime() async throws {
        let counting = CountingRunner(
            counts: { $0.first == "init" },
            reply: { args in
                args.first == "init" ? CommandResult(status: 0, stdout: Data(), stderr: "")
                    : args[1] == "check" ? ok(#"{"findings": []}"#) : ok(listJSON)
            })
        let model = HooksModel()
        let swe = SweClient(runner: counting)
        async let a: Void = model.link(swe: swe, for: "/bin/swe")
        async let b: Void = model.link(swe: swe, for: "/bin/swe")
        _ = await (a, b)
        #expect(counting.peak == 1)
        #expect(counting.calls.filter { $0.first == "init" }.count == 1)
    }

    @Test func aLinkFinishingAfterAnotherSweWasPickedDoesNotReplaceItsList() async throws {
        let old = HeldInitRunner(ScriptedRunner([
            (["hooks", "list", "--json"], ok(listJSON)),
            (["hooks", "check", "--json"], ok(#"{"findings": []}"#)),
            (["init"], CommandResult(status: 0, stdout: Data(), stderr: "")),
        ]))
        let new = ScriptedRunner([
            (["hooks", "list", "--json"], ok(#"{"hooks": []}"#)),
            (["hooks", "check", "--json"], ok(#"{"findings": []}"#)),
        ])
        let model = HooksModel()
        let oldSwe = SweClient(runner: old)
        await model.load(swe: oldSwe, for: "/old/swe")
        let link = Task { await model.link(swe: oldSwe, for: "/old/swe") }
        while !old.held { await Task.yield() }
        // Settings moves to another swe while init is running.
        await model.loadIfStale(swe: SweClient(runner: new), for: "/new/swe")
        old.release()
        await link.value
        // The old swe's reload was dropped: its list ran once, at the start.
        #expect(old.inner.calls.filter { $0 == ["hooks", "list", "--json"] }.count == 1)
        guard case .loaded(let shown) = model.state else { Issue.record("\(model.state)"); return }
        #expect(shown.hooks.isEmpty)
    }

    @Test func snapshotsKeepTheListBetweenLaunches() async throws {
        let store = MemorySnapshotStore<Snapshot<HookInventory>>()
        let clock = TestClock()
        let runner = self.runner(ok(listJSON), check: ok(checkJSON))
        let first = HooksModel(snapshots: store, now: { clock.now })
        await first.load(swe: SweClient(runner: runner), for: "/bin/swe")

        let next = HooksModel(snapshots: store, now: { clock.now })
        await next.restore()
        #expect(next.state == first.state)
        // Still fresh: opening the page asks swe nothing.
        await next.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.count == 2)
    }
}

/// Holds `init` until released; everything else answers at once.
final class HeldInitRunner: CommandRunning, @unchecked Sendable {
    let inner: ScriptedRunner
    private let lock = NSLock()
    private var waiter: CheckedContinuation<Void, Never>?
    init(_ inner: ScriptedRunner) { self.inner = inner }

    func run(_ arguments: [String]) async throws -> CommandResult {
        if arguments.first == "init" {
            await withCheckedContinuation { continuation in
                lock.withLock { waiter = continuation }
            }
        }
        return try await inner.run(arguments)
    }

    var held: Bool { lock.withLock { waiter != nil } }

    func release() {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Never>? in
            defer { self.waiter = nil }
            return self.waiter
        }
        waiter?.resume()
    }
}
