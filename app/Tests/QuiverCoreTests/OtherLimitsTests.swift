import Foundation
import Testing
@testable import QuiverCore

@MainActor
@Suite struct OtherLimitsTests {
    let body = Data(#"""
    {"schema": 1, "data": {"harnesses": [
      {"name": "claude", "command": "claude", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 9,
       "rate_limit": {"status": "ok", "used_percent": 50, "remaining_percent": 50, "limit_reached": false, "reset_at": null, "window": "7d", "window_seconds": null, "plan_type": "max", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}},
      {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 3,
       "rate_limit": {"status": "ok", "used_percent": 100, "remaining_percent": 0, "limit_reached": true, "reset_at": 1791760000, "window": "7d", "window_seconds": 604800, "plan_type": "teams", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}},
      {"name": "cursor", "command": "cursor-agent", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1,
       "rate_limit": {"status": "auth-required", "used_percent": 0, "remaining_percent": 100, "limit_reached": false, "reset_at": null, "window": null, "window_seconds": null, "plan_type": "\u2014", "remaining_units": null, "total_units": null, "fetched_at": null}},
      {"name": "gemini", "command": "gemini", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": 0, "rate_limit": null}
    ]}}
    """#.utf8)

    @Test func harnessesWithoutAQuiverAccountComeFromSwe() async {
        let runner = FakeRunner(.init(status: 0, stdout: body, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner))
        await model.load()
        // Claude and Codex have accounts of their own; unpolled harnesses have nothing to show.
        #expect(model.rows.map(\.name) == ["devin", "cursor"])
        #expect(model.rows[0].limit.usedPercent == 100)
        #expect(model.rows[1].limit.status == .authRequired)
        // swe's "—" placeholder means no plan, not a plan called "—".
        #expect(model.rows[0].limit.planType == "teams")
        #expect(model.rows[1].limit.planType == nil)
        // Claude and Codex are fetched per account by the app; swe must not poll them too.
        #expect(runner.calls == [["list", "--usage", "--json", "--exclude=claude,codex"]])
    }

    @Test func rowsCarryTheirCommandAndWhetherItIsOnPath() async {
        let json = String(decoding: body, as: UTF8.self)
            .replacingOccurrences(of: #""name": "devin", "command": "devin","#, with: #""name": "devin", "command": "devin", "installed": true,"#)
            .replacingOccurrences(of: #""name": "cursor", "command": "cursor-agent","#, with: #""name": "cursor", "display_name": "Cursor", "command": "cursor-agent", "installed": false,"#)
            .replacingOccurrences(of: #""name": "claude", "command": "claude","#, with: #""name": "claude", "command": "claude", "installed": true,"#)
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))))
        await model.load()
        #expect(model.rows.map(\.command) == ["devin", "cursor-agent"])
        #expect(model.rows.map(\.cliInstalled) == [true, false])
        #expect(model.rows.map(\.title) == ["Devin", "Cursor"])
        // Every harness's PATH check, Claude's too, for the account cards.
        #expect(model.cliInstalled["claude"] == true)
        #expect(model.cliInstalled["gemini"] == nil)
    }

    @Test func everyHarnessInUseGetsACardStarredFirstWithOrWithoutALimit() async {
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))))
        await model.load()
        // Claude and Codex have account cards of their own.
        #expect(model.cards.map(\.name) == ["devin", "cursor", "gemini"])
        #expect(model.cards.map { $0.limit != nil } == [true, true, false])
        #expect(model.cards.map(\.title) == ["Devin", "Cursor", "Gemini"])
        #expect(model.cards.map(\.isStarred) == [true, true, false])
    }

    @Test func aCardSaysWhyItHasNoLimit() {
        #expect(HarnessAccess.hasLimits("devin"))
        #expect(HarnessAccess.hasLimits("copilot"))
        #expect(!HarnessAccess.hasLimits("opencode"))
    }

    @Test func anOldSweLeavesTheSectionEmptyWithAReason() async {
        let runner = FakeRunner(.init(status: 0, stdout: Data("Unexpected args: --json".utf8), stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner))
        await model.load()
        #expect(model.rows.isEmpty)
        #expect(model.note?.contains("older") == true)
    }
}
