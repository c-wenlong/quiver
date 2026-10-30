import Foundation
import Testing
@testable import QuiverCore

let harnessesJSON = #"""
{"harnesses": [
 {"name": "codex", "command": "codex", "description": "OpenAI's", "version": "0.144.1",
  "aliases": ["cx"], "tags": ["agentic"], "state": "active", "sessions_100d": 60,
  "rate_limit": null, "installed": true, "archived": null},
 {"name": "claude", "command": "claude", "description": "Anthropic's", "version": "2.1.220",
  "aliases": ["cc"], "tags": ["agentic"], "state": "starred", "sessions_100d": 533,
  "rate_limit": null, "installed": true, "archived": null},
 {"name": "opencode", "command": "opencode", "description": "Open source", "version": null,
  "aliases": [], "tags": ["open-source"], "state": "active", "sessions_100d": 80,
  "rate_limit": null, "installed": false, "archived": null},
 {"name": "goose", "command": "goose", "description": "Block's", "version": null,
  "aliases": [], "tags": [], "state": "archived", "sessions_100d": 2, "rate_limit": null,
  "installed": true,
  "archived": {"reason": "too slow", "archived_at": "2026-09-01T10:00:00", "usage": "trial"}}
]}
"""#

@MainActor
@Suite struct HarnessesModelTests {
    func model(_ rules: [(prefix: [String], reply: CommandResult)]) -> (HarnessesModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (HarnessesModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func listsEveryScopeStarredFirstThenBusiest() async throws {
        let (model, runner) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        #expect(runner.calls == [["list", "--scope=all", "--json"]])
        #expect(model.visible.map(\.name) == ["claude", "opencode", "codex"])
        // `sessions_100d` decodes despite convertFromSnakeCase making it `sessions100D`.
        #expect(model.all.first { $0.name == "claude" }?.sessions100d == 533)
        #expect(model.count(.inUse) == 3)
        #expect(model.count(.archived) == 1)
        #expect(model.visible[1].installed == false)
        model.scope = .archived
        let goose = try #require(model.visible.first)
        #expect(goose.archived?.reason == "too slow")
        #expect(goose.archived?.usage == "trial")
    }

    @Test func anOlderSweWithoutTheNewFieldsStillLoads() async {
        let old = #"{"harnesses": [{"name": "codex", "command": "codex", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": null, "rate_limit": null}]}"#
        let (model, _) = model([(["list"], ok(old))])
        await model.load()
        #expect(model.all.first?.installed == nil)
        #expect(model.all.first?.archived == nil)
    }

    @Test func searchMatchesNameDescriptionAndTags() async {
        let (model, _) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        model.query = "open"
        #expect(model.visible.map(\.name) == ["opencode", "codex"])
    }

    @Test func unstarredHarnessesAreCollapsedUntilExpanded() async {
        let (model, _) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        #expect(model.shown.map(\.name) == ["claude"])
        #expect(model.collapsedCount == 2)
        #expect(model.canCollapse)
        model.showsUnstarred = true
        #expect(model.shown.map(\.name) == ["claude", "opencode", "codex"])
        #expect(model.collapsedCount == 0)
        #expect(model.canCollapse)
    }

    @Test func searchingShowsEveryMatchWithoutTheExpandRow() async {
        let (model, _) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        model.query = "open"
        #expect(model.shown.map(\.name) == ["opencode", "codex"])
        #expect(!model.canCollapse)
    }

    @Test func theArchivedListIsNeverCollapsed() async {
        let (model, _) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        model.scope = .archived
        #expect(model.shown.map(\.name) == ["goose"])
        #expect(!model.canCollapse)
    }

    @Test func nothingToFoldWhenEveryHarnessIsStarred() async {
        let json = harnessesJSON.replacingOccurrences(of: #""state": "active""#, with: #""state": "starred""#)
        let (model, _) = model([(["list"], ok(json))])
        await model.load()
        #expect(model.shown.count == 3)
        #expect(!model.canCollapse)
    }

    @Test func starTogglesThroughSweThenReloads() async throws {
        let (model, runner) = model([
            (["harness", "star"], ok(#"{"name": "codex", "state": "starred"}"#)),
            (["list"], ok(harnessesJSON)),
        ])
        await model.load()
        await model.toggleStar(try #require(model.all.first { $0.name == "codex" }))
        #expect(runner.calls == [
            ["list", "--scope=all", "--json"],
            ["harness", "star", "codex", "--json"],
            ["list", "--scope=all", "--json"],
        ])
    }

    @Test func archiveNeedsAReason() async throws {
        let (model, runner) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        await model.archive(try #require(model.all.first { $0.name == "codex" }), reason: "   ")
        // A blank reason would restore instead of archive, so swe is never asked.
        #expect(runner.calls == [["list", "--scope=all", "--json"]])
        #expect(model.actionError == "Say why you're archiving it.")
    }

    @Test func archiveAndRestorePassTheReason() async throws {
        let (model, runner) = model([
            (["harness", "archive", "codex", "too noisy"], ok(#"{"name": "codex", "state": "archived", "archived": {"reason": "too noisy", "archived_at": "x", "usage": "used"}}"#)),
            (["harness", "archive", "goose"], ok(#"{"name": "goose", "state": "active", "archived": null}"#)),
            (["list"], ok(harnessesJSON)),
        ])
        await model.load()
        await model.archive(try #require(model.all.first { $0.name == "codex" }), reason: " too noisy ")
        await model.restore(try #require(model.all.first { $0.name == "goose" }))
        #expect(runner.calls.filter { $0.first == "harness" } == [
            ["harness", "archive", "codex", "too noisy", "--json"],
            ["harness", "archive", "goose", "--json"],
        ])
        #expect(model.actionError == nil)
    }

    @Test func restoreIgnoresAHarnessThatIsNotArchived() async throws {
        let (model, runner) = model([(["list"], ok(harnessesJSON))])
        await model.load()
        await model.restore(try #require(model.all.first { $0.name == "codex" }))
        #expect(runner.calls.count == 1)
    }

    @Test func changesToDifferentHarnessesRunOneAtATime() async throws {
        // Each swe run rewrites harness.json; overlapping runs lose a change.
        let runner = CountingRunner(counts: { $0.first == "harness" }, reply: { args in
            args.first == "harness" ? ok(#"{"name": "x", "state": "starred"}"#) : ok(harnessesJSON)
        })
        let model = HarnessesModel(swe: SweClient(runner: runner))
        await model.load()
        let codex = try #require(model.all.first { $0.name == "codex" })
        let opencode = try #require(model.all.first { $0.name == "opencode" })
        let star = Task { await model.toggleStar(codex) }
        let archive = Task { await model.archive(opencode, reason: "slow") }
        // Both changes have asked while the first swe run is held, so a
        // second run that did not wait its turn would overlap it.
        #expect(await runner.firstCounted.arrived())
        #expect(await observe { model.busy.count == 2 })
        runner.firstCounted.open()
        await star.value
        await archive.value
        #expect(runner.calls.filter { $0.first == "harness" }.count == 2)
        #expect(runner.peak == 1)
        #expect(model.busy.isEmpty)
    }

    @Test func searchFindsAHarnessByItsDisplayName() async {
        let json = harnessesJSON.replacingOccurrences(of: #""name": "codex","#, with: #""name": "codex", "display_name": "Work assistant","#)
        let (model, _) = model([(["list"], ok(json))])
        await model.load()
        model.query = "work assist"
        #expect(model.visible.map(\.name) == ["codex"])
        // The built-in brand name counts too.
        model.query = "claude code"
        #expect(model.visible.map(\.name) == ["claude"])
    }

    @Test func aFailedChangeKeepsTheListAndSaysWhy() async throws {
        let (model, _) = model([
            (["harness", "star"], CommandResult(status: 1, stdout: Data(), stderr: "  Tool 'codex' not found. Try 'swe list'.")),
            (["list"], ok(harnessesJSON)),
        ])
        await model.load()
        await model.toggleStar(try #require(model.all.first { $0.name == "codex" }))
        #expect(model.actionError == "Tool 'codex' not found. Try 'swe list'.")
        #expect(model.all.count == 4)
        #expect(model.busy.isEmpty)
    }
}
