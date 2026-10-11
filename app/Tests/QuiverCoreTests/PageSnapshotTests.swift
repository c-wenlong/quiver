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
        let url = FileManager.default.temporaryDirectory
            .appending(path: "quiver-snapshot-\(UUID().uuidString)/cache/mcp-servers.json")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent().deletingLastPathComponent()) }
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
