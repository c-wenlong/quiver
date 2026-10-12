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

    @Test func onlyStarredHarnessesGetCards() async {
        // swe's list holds active and archived harnesses too: cards keep
        // the starred ones only, Claude and Codex excluded (accounts of
        // their own).
        let json = #"""
        {"schema": 1, "data": {"harnesses": [
          {"name": "pi", "command": "pi", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": 9, "rate_limit": null},
          {"name": "claude", "command": "claude", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 9, "rate_limit": null},
          {"name": "droid", "command": "droid", "description": "", "version": null, "aliases": [], "tags": [], "state": "archived", "sessions_100d": 1, "rate_limit": null},
          {"name": "devin", "display_name": "Devin Teams", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 3,
           "rate_limit": {"status": "ok", "used_percent": 10, "remaining_percent": 90, "limit_reached": false, "reset_at": null, "window": "7d", "window_seconds": null, "plan_type": "teams", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}},
          {"name": "kilo", "command": "kilo", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": 2, "rate_limit": null}
        ]}}
        """#
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))))
        await model.load()
        #expect(model.cards.map(\.name) == ["devin"])
        #expect(model.cards[0].title == "Devin Teams")
        #expect(model.cards[0].limit != nil)
        // The starred set remembers Claude and Codex too: their account
        // cards hide the moment either is unstarred.
        #expect(model.starred == ["claude", "devin"])
        #expect(model.isStarred("claude"))
        #expect(!model.isStarred("pi"))
        #expect(!model.isStarred("droid"))
    }

    @Test func onlyStarredHarnessesShowALimitAnywhere() async {
        // swe can still hand back a cached reading for a harness since unstarred.
        let json = #"""
        {"schema": 1, "data": {"harnesses": [
          {"name": "cursor", "command": "cursor-agent", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": 1,
           "rate_limit": {"status": "ok", "used_percent": 40, "remaining_percent": 60, "limit_reached": false, "reset_at": null, "window": "auto", "window_seconds": null, "plan_type": "pro", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}},
          {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 3,
           "rate_limit": {"status": "ok", "used_percent": 10, "remaining_percent": 90, "limit_reached": false, "reset_at": null, "window": "7d", "window_seconds": null, "plan_type": "teams", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}}
        ]}}
        """#
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))))
        await model.load()
        #expect(model.cards.map(\.name) == ["devin"])
        // The menu bar, notifications and widgets read rows: starred only too.
        #expect(model.rows.map(\.name) == ["devin"])
    }

    @Test func aFailedLoadKeepsTheLastStarredSet() async {
        // Before swe has answered once, nothing is hidden: unknown means show.
        let runner = FakeRunner(.init(status: 0, stdout: body, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner))
        #expect(model.starred == nil)
        #expect(model.isStarred("codex"))
        await model.load()
        #expect(model.starred == ["claude", "devin", "cursor"])
        // A hiccup must not unstar anything.
        runner.reply = .init(status: 1, stdout: Data(), stderr: "boom\n")
        await model.load()
        #expect(model.starred == ["claude", "devin", "cursor"])
    }

    @Test func aNamedHarnessReadsLikeAnAccount() {
        let limit = RateLimit(status: .ok, usedPercent: 1)
        #expect(OtherLimitsModel.Row(name: "cursor", limit: limit, displayName: "Cortex").accountTitle == "Cursor: Cortex")
        #expect(OtherLimitsModel.Row(name: "cursor", limit: limit).accountTitle == "Cursor")
        // A name that just repeats the brand isn't said twice.
        #expect(OtherLimitsModel.Row(name: "cursor", limit: limit, displayName: "Cursor").accountTitle == "Cursor")
        #expect(OtherLimitsModel.Row(name: "qwen-code", limit: limit, displayName: "Qwen Code").accountTitle == "Qwen Code")
        #expect(OtherLimitsModel.Row(name: "devin", limit: limit, displayName: "Devin Work").accountTitle == "Devin Work")
        // Sharing only the brand's first letters isn't saying the brand.
        #expect(OtherLimitsModel.Row(name: "devin", limit: limit, displayName: "Devinshire").accountTitle == "Devin: Devinshire")
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
