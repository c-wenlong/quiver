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
