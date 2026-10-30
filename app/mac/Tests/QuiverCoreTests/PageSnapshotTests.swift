import Foundation
import Testing
@testable import QuiverCore

/// The pages that keep their last result between visits and launches.

@MainActor
@Suite struct PluginsSnapshotTests {
    let start = Date(timeIntervalSince1970: 1_791_700_000)
    let drift = json(#"""
    {"sources": 3, "drift": [
      {"id": "claude:stale:tool@mk", "harness": "claude", "marketplace": "mk", "plugin": "tool",
       "ref": "tool@mk", "kind": "stale", "detail": "installed 1.0 vs source 1.1, 2 files differ",
       "fix": "claude plugin update tool@mk", "runnable": true, "paths": ["a.md", "b.md"]}
    ]}
    """#)

    func model(_ store: MemorySnapshotStore<Snapshot<PluginDriftReport>>, clock: TestClock) -> PluginsModel {
        PluginsModel(snapshots: store, maxAge: 60, now: { clock.now })
    }

    @Test func theLastReportShowsAtOnceOnTheNextLaunch() async throws {
        let store = MemorySnapshotStore<Snapshot<PluginDriftReport>>()
        let clock = TestClock(start)
        let runner = RoutingRunner([(["find", "plugins", "--json"], drift)])
        let first = model(store, clock: clock)
        await first.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(await store.saves == 1)

        let next = model(store, clock: clock)
        await next.restore()
        #expect(next.state == first.state)
        guard case .loaded(let report) = next.state else { Issue.record("\(next.state)"); return }
        #expect(report.drift.first?.paths == ["a.md", "b.md"])
        // Still fresh: opening the page asks swe nothing.
        await next.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.count == 1)
    }

    @Test func aReportIsCheckedAgainOnceItIsOld() async throws {
        let clock = TestClock(start)
        let runner = RoutingRunner([(["find", "plugins", "--json"], drift)])
        let model = model(MemorySnapshotStore(), clock: clock)
        let swe = SweClient(runner: runner)
        await model.loadIfStale(swe: swe, for: "/bin/swe")
        await model.loadIfStale(swe: swe, for: "/bin/swe")
        #expect(runner.calls.count == 1)
        clock.advance(61)
        await model.loadIfStale(swe: swe, for: "/bin/swe")
        #expect(runner.calls.count == 2)
        // Refresh always checks.
        await model.load(swe: swe)
        #expect(runner.calls.count == 3)
    }

    @Test func aFixAlwaysChecksAgainAndSavesWhatItFinds() async throws {
        let store = MemorySnapshotStore<Snapshot<PluginDriftReport>>()
        let runner = RoutingRunner([
            (["find", "plugins", "--json"], drift),
            (["find", "plugins", "--fix=claude:stale:tool@mk", "--json"], json(#"""
            {"fixed": {"id": "claude:stale:tool@mk", "harness": "claude", "marketplace": "mk", "plugin": "tool",
             "ref": "tool@mk", "kind": "stale", "detail": "d", "fix": "claude plugin update tool@mk",
             "runnable": true, "paths": []}}
            """#)),
        ])
        let model = model(store, clock: TestClock(start))
        let swe = SweClient(runner: runner)
        await model.loadIfStale(swe: swe, for: "/bin/swe")
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        runner.set(["find", "plugins", "--json"], json(#"{"sources": 3, "drift": []}"#))
        await model.fix(report.drift[0], swe: swe)
        #expect(runner.calls.count == 3)
        #expect(await store.saved?.value.drift.isEmpty == true)
        #expect(await store.saved?.source == "/bin/swe")
    }

    @Test func aFixFinishingAfterAnotherSweWasPickedDoesNotReplaceItsReport() async throws {
        let a = RoutingRunner([
            (["find", "plugins", "--json"], drift),
            (["find", "plugins", "--fix="], json(#"""
            {"fixed": {"id": "claude:stale:tool@mk", "harness": "claude", "marketplace": "mk", "plugin": "tool",
             "ref": "tool@mk", "kind": "stale", "detail": "d", "fix": "claude plugin update tool@mk",
             "runnable": true, "paths": []}}
            """#)),
        ])
        let b = RoutingRunner([(["find", "plugins", "--json"], json(#"{"sources": 0, "drift": []}"#))])
        let model = model(MemorySnapshotStore(), clock: TestClock(start))
        await model.load(swe: SweClient(runner: a), for: "/a/swe")
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        await model.loadIfStale(swe: SweClient(runner: b), for: "/b/swe")
        await model.fix(report.drift[0], swe: SweClient(runner: a), for: "/a/swe")
        #expect(a.calls.count == 2)
        guard case .loaded(let shown) = model.state else { Issue.record("\(model.state)"); return }
        #expect(shown.sources == 0)
    }
}

/// Synthetic secrets only: SECRET marks what must never reach the disk.
@MainActor
@Suite struct McpSnapshotTests {
    let start = Date(timeIntervalSince1970: 1_791_700_000)
    let listing = json(#"""
    {"harnesses": ["claude", "codex"], "servers": [
      {"name": "github", "transport": "stdio", "command": "npx --token SECRET1", "args": ["gh", "SECRETpos", "--api-key=SECRET2", "KEY=SECRET3"],
       "url": null, "env_keys": ["GITHUB_TOKEN"], "header_keys": [], "harnesses": ["claude", "codex"],
       "conflict": false, "variants": [["claude", "codex"]]},
      {"name": "docs", "transport": "http", "command": null, "args": [],
       "url": "https://me:SECRET4@docs.example/s/SECRET9ab/mcp?key=SECRET5#SECRET6",
       "env_keys": [], "header_keys": ["Authorization"], "harnesses": ["claude"],
       "conflict": false, "variants": [["claude"]]}
    ]}
    """#)
    let status = json(#"""
    {"harnesses": ["claude"], "servers": [
      {"name": "docs", "transport": "http", "command": null, "args": [], "url": "https://docs.example/mcp?key=SECRET7",
       "env_keys": [], "header_keys": [], "harnesses": ["claude"], "conflict": false, "variants": [["claude"]],
       "health": "error", "detail": "401 from https://docs.example/mcp?key=SECRET8"}]}
    """#)

    func model(_ store: any SnapshotStoring<Snapshot<McpInventory>>, clock: TestClock) -> McpModel {
        McpModel(snapshots: store, maxAge: 60, now: { clock.now })
    }

    func inventory(_ model: McpModel) -> McpInventory? {
        if case .loaded(let inventory) = model.state { return inventory }
        Issue.record("expected loaded, got \(model.state)")
        return nil
    }

    @Test func theLastListShowsAtOnceOnTheNextLaunch() async throws {
        let store = MemorySnapshotStore<Snapshot<McpInventory>>()
        let clock = TestClock(start)
        let runner = RoutingRunner([(["mcp", "list", "--json"], listing)])
        let first = model(store, clock: clock)
        await first.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        let live = try #require(inventory(first))

        let next = model(store, clock: clock)
        await next.restore()
        let restored = try #require(inventory(next))
        #expect(restored.harnesses == live.harnesses)
        #expect(restored.servers.map(\.name) == ["github", "docs"])
        // A saved summary keeps only the program or host: a short key in an
        // argument or URL path can slip past both scrubbers.
        #expect(restored.servers.map(\.summary) == ["npx …", "https://…@docs.example/…"])
        #expect(restored.servers.map(\.envKeys) == live.servers.map(\.envKeys))
        #expect(restored.servers.map(\.headerKeys) == live.servers.map(\.headerKeys))
        #expect(restored.servers.map(\.groups) == live.servers.map(\.groups))
        // Still fresh: opening the page asks swe nothing.
        await next.loadIfStale(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.count == 1)
        // Refresh always reads again.
        await next.load(swe: SweClient(runner: runner), for: "/bin/swe")
        #expect(runner.calls.count == 2)
    }

    @Test func theSavedFileHoldsNoCommandLineURLOrHealthDetail() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let url = temp.path("cache/mcp-servers.json")
        let file = SnapshotFile<Snapshot<McpInventory>>(url: url)
        let runner = RoutingRunner([(["mcp", "list", "--json"], listing), (["mcp", "status", "--json"], status)])
        let model = model(file, clock: TestClock(start))
        await model.load(swe: SweClient(runner: runner), for: "/bin/swe")
        var text = try String(contentsOf: url, encoding: .utf8)
        #expect(!text.contains("SECRET"), "\(text)")
        #expect(text.contains("GITHUB_TOKEN"))

        await model.load(swe: SweClient(runner: runner), for: "/bin/swe", health: true)
        #expect(model.hasHealth)
        text = try String(contentsOf: url, encoding: .utf8)
        #expect(!text.contains("SECRET"), "\(text)")
        #expect(!text.contains("401"))

        // Health is a probe of the moment: a restored list has none.
        let next = McpModel(snapshots: file, now: { [start] in start })
        await next.restore()
        #expect(!next.hasHealth)
        #expect(inventory(next)?.servers.first?.health == nil)
    }

    @Test func aRestoredServerCanStillBeSynced() async throws {
        let store = MemorySnapshotStore<Snapshot<McpInventory>>()
        let runner = RoutingRunner([(["mcp", "list", "--json"], listing)])
        await model(store, clock: TestClock(start)).load(swe: SweClient(runner: runner), for: "/bin/swe")
        let next = model(store, clock: TestClock(start))
        await next.restore()
        let docs = try #require(next.server(named: "docs"))
        let targets = next.targets(for: docs, from: "claude", in: ["claude", "codex"])
        #expect(targets.missing == ["codex"])
        #expect(docs.onlyPattern == "docs")
    }
}

@MainActor
@Suite struct SkillsSnapshotTests {
    let start = Date(timeIntervalSince1970: 1_791_700_000)

    func model(_ runner: ScriptedRunner, skills: MemorySnapshotStore<Snapshot<SkillInventory>>,
               clock: TestClock) -> SkillsModel {
        SkillsModel(swe: SweClient(runner: runner), snapshots: skills, maxAge: 60, now: { clock.now })
    }

    @Test func theLastSkillsShowAtOnceOnTheNextLaunch() async throws {
        let skills = MemorySnapshotStore<Snapshot<SkillInventory>>()
        let clock = TestClock(start)
        let runner = ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])
        let first = model(runner, skills: skills, clock: clock)
        await first.loadIfStale(for: "/bin/swe")

        let next = model(ScriptedRunner([]), skills: skills, clock: clock)
        next.plugins = .unavailable("not read")
        await next.restore()
        #expect(next.inventory == first.inventory)
        #expect(next.roots.map(\.link) == first.roots.map(\.link))
        // Placed at once too, not only once swe answers.
        #expect(next.listedSkills.count == 4)
    }

    @Test func freshListsAreNotFetchedAgain() async throws {
        let clock = TestClock(start)
        let runner = ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])
        let model = model(runner, skills: MemorySnapshotStore(), clock: clock)
        await model.loadIfStale(for: "/bin/swe")
        await model.loadIfStale(for: "/bin/swe")
        #expect(runner.calls == [["skills", "--json"]])
        clock.advance(61)
        await model.loadIfStale(for: "/bin/swe")
        #expect(runner.calls.count == 2)
        // Refresh always lists.
        await model.load()
        #expect(runner.calls.count == 3)
    }

    @Test func aLinkListsAgainAndSavesTheResult() async throws {
        let skills = MemorySnapshotStore<Snapshot<SkillInventory>>()
        let runner = ScriptedRunner([
            (["skills", "link"], ok(#"{"action": "link"}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        let model = model(runner, skills: skills, clock: TestClock(start))
        await model.loadIfStale(for: "/bin/swe")
        let hermes = try #require(model.roots.first { $0.name == "hermes" })
        runner.rules.insert((["skills", "--json"], ok(inventoryJSON.replacingOccurrences(
            of: #""path": "/h/.hermes/skills", "state": "create""#, with: #""path": "/h/.hermes/skills", "state": "linked""#))),
            at: 0)
        await model.link(hermes)
        #expect(runner.calls.filter { $0 == ["skills", "--json"] }.count == 2)
        #expect(await skills.saved?.value.harnesses.first { $0.name == "hermes" }?.state == "linked")
        #expect(await skills.saved?.source == "/bin/swe")
    }

    @Test func aNewSweIsUsedFromTheNextLoad() async throws {
        let old = ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])
        let new = ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])
        let model = model(old, skills: MemorySnapshotStore(), clock: TestClock(start))
        await model.loadIfStale(for: "/old/swe")
        model.use(SweClient(runner: new), for: "/new/swe")
        await model.loadIfStale(for: "/new/swe")
        #expect(old.calls.count == 1)
        #expect(new.calls.count == 1)
    }

    @Test func aLinkFinishingAfterAnotherSweWasPickedDoesNotReplaceItsList() async throws {
        let old = HeldLinkRunner(ScriptedRunner([
            (["skills", "link"], ok(#"{"action": "link"}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ]))
        let new = ScriptedRunner([(["skills", "--json"], ok(#"{"shared_dir": "/n", "harnesses": [], "skills": []}"#))])
        let model = SkillsModel(swe: SweClient(runner: old), snapshots: MemorySnapshotStore())
        model.use(SweClient(runner: old), for: "/old/swe")
        await model.loadIfStale(for: "/old/swe")
        let hermes = try #require(model.roots.first { $0.name == "hermes" })
        let link = Task { await model.link(hermes) }
        try #require(await old.held())
        // Settings moves to another swe while the link is running.
        model.use(SweClient(runner: new), for: "/new/swe")
        await model.loadIfStale(for: "/new/swe")
        old.release()
        await link.value
        #expect(old.inner.calls.filter { $0 == ["skills", "--json"] }.count == 1)
        guard case .loaded(let shown) = model.inventory else { Issue.record("\(model.inventory)"); return }
        #expect(shown.sharedDir == "/n")
    }

    @Test func anActionFinishingAfterASwitchIsDroppedEvenWithThePageClosed() async throws {
        let old = HeldLinkRunner(ScriptedRunner([
            (["skills", "link"], ok(#"{"action": "link"}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ]))
        let model = SkillsModel(swe: SweClient(runner: old), snapshots: MemorySnapshotStore())
        model.use(SweClient(runner: old), for: "/old/swe")
        await model.loadIfStale(for: "/old/swe")
        let hermes = try #require(model.roots.first { $0.name == "hermes" })
        let link = Task { await model.link(hermes) }
        try #require(await old.held())
        // Settings moves on while Skills is closed: nothing lists the new swe yet.
        model.use(SweClient(runner: ScriptedRunner([])), for: "/new/swe")
        old.release()
        await link.value
        #expect(old.inner.calls.filter { $0 == ["skills", "--json"] }.count == 1)
    }

    @Test func aFilterTheNewListLacksIsCleared() async throws {
        let codexOnly = #"""
        {"shared_dir": "/n", "harnesses": [], "skills": [
          {"name": "x", "path": "/n/x", "root": "codex", "source": "harness", "harnesses": ["codex"]}]}
        """#
        let model = SkillsModel(swe: SweClient(runner: ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])))
        model.plugins = .unavailable("not read")
        model.use(model.swe, for: "/a/swe")
        await model.loadIfStale(for: "/a/swe")
        model.harness = "codex"
        model.source = .harness
        // Another list that still has both keeps them.
        model.use(SweClient(runner: ScriptedRunner([(["skills", "--json"], ok(codexOnly))])), for: "/b/swe")
        await model.loadIfStale(for: "/b/swe")
        #expect(model.harness == "codex")
        #expect(model.source == .harness)
        model.harness = "claude"
        model.source = .shared
        await model.load(for: "/b/swe")
        #expect(model.harness == nil)
        #expect(model.source == nil)
        #expect(model.visibleSkills.map(\.name) == ["x"])
    }

    @Test func aFailedRefreshKeepsTheFilters() async throws {
        let runner = ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])
        let model = SkillsModel(swe: SweClient(runner: runner))
        await model.load(for: "/bin/swe")
        model.harness = "codex"
        model.source = .harness
        runner.rules = [(["skills", "--json"], CommandResult(status: 1, stdout: Data(), stderr: "boom"))]
        await model.load(for: "/bin/swe")
        #expect(model.inventory == .failed("boom"))
        #expect(model.harness == "codex")
        #expect(model.source == .harness)
    }

    @Test func skillsRoundTripThroughTheirFile() async throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let skills = SnapshotFile<Snapshot<SkillInventory>>(url: temp.path("cache/skills.json"))
        let runner = ScriptedRunner([(["skills", "--json"], ok(inventoryJSON))])
        let first = SkillsModel(swe: SweClient(runner: runner), snapshots: skills)
        await first.load(for: "/bin/swe")

        let next = SkillsModel(swe: SweClient(runner: ScriptedRunner([])), snapshots: skills)
        await next.restore()
        #expect(next.inventory == first.inventory)
        #expect(next.roots.map(\.link) == [.linked, .blocked, .notLinked, .notLinked])
        #expect(next.roots.last?.linkNeedsForce == true)
        #expect(next.allSkills.last?.source == .harness)
    }
}

/// Holds a link until released; everything else answers at once.
final class HeldLinkRunner: CommandRunning, Sendable {
    let inner: ScriptedRunner
    private let gate = HoldGate()
    init(_ inner: ScriptedRunner) { self.inner = inner }

    func run(_ arguments: [String]) async throws -> CommandResult {
        if arguments.contains("link") { try await gate.pass() }
        return try await inner.run(arguments)
    }

    /// True once a link is parked, false after `gateTimeout`.
    func held() async -> Bool { await gate.arrived() }
    func release() { gate.open() }
}
