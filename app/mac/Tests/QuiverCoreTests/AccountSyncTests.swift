import Foundation
import Testing
@testable import QuiverCore

/// The Accounts page's "Shared content" switches against
/// `swe init --accounts --json`, captured from a real swe run in a
/// throwaway HOME (skills and instructions on, one account's CLAUDE.md
/// already its own).
@MainActor
@Suite struct AccountSyncTests {
    private func model() throws -> (AccountSyncModel, FakeRunner) {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture("swe-init-accounts"), stderr: ""))
        return (AccountSyncModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func reportDecodesSwitchesRowsAndConflicts() async throws {
        let (sync, runner) = try model()
        await sync.load()
        #expect(runner.calls == [["init", "--accounts", "--check", "--json"]])
        #expect(sync.isOn(.skills))
        #expect(sync.isOn(.instructions))
        #expect(!sync.isOn(.hooks))
        let report = try #require(sync.report)
        #expect(report.accounts.count == 2)
        #expect(report.conflicts.map(\.primitive) == ["instructions"])
        #expect(sync.error == nil)
    }

    @Test func flippingASwitchSavesItThenRunsThePass() async throws {
        let (sync, runner) = try model()
        await sync.set(.hooks, on: true)
        #expect(runner.calls == [
            ["config", "set", "accounts.sync.hooks", "true"],
            ["init", "--accounts", "--json"],
        ])
        #expect(!sync.busy)
    }

    @Test func aNewAccountIsSyncedOnlyWhenASwitchIsOn() async throws {
        let (sync, runner) = try model()
        await sync.syncIfOn()
        #expect(runner.calls == [
            ["init", "--accounts", "--check", "--json"],
            ["init", "--accounts", "--json"],
        ])

        let off = FakeRunner(.init(status: 0, stdout: Data(
            #"{"schema": 1, "data": {"account_sync": {"instructions": false, "skills": false, "hooks": false}, "accounts": [], "notes": []}}"#
                .utf8), stderr: ""))
        let idle = AccountSyncModel(swe: SweClient(runner: off))
        await idle.syncIfOn()
        #expect(off.calls == [["init", "--accounts", "--check", "--json"]])
    }

    @Test func aFailedSwitchKeepsItsReasonAfterTheReload() async throws {
        let runner = AccountSyncRunner(fixture: try fixture("swe-init-accounts"), holdConfig: false,
                                    configFails: true)
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.set(.hooks, on: true)
        #expect(sync.error?.contains("config.json is read-only") == true)
        #expect(sync.report != nil)
    }

    @Test func anAccountAddedDuringAPassGetsAPassOfItsOwn() async throws {
        let runner = AccountSyncRunner(fixture: try fixture("swe-init-accounts"), holdConfig: true,
                                    configFails: false)
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.load()
        let flip = Task { await sync.set(.hooks, on: true) }
        await runner.configEntered.wait()
        #expect(sync.busy)
        await sync.syncIfOn()  // busy: queued, not dropped
        runner.releaseConfig.signal()
        await flip.value
        #expect(runner.calls.filter { $0 == ["init", "--accounts", "--json"] }.count == 2)
        #expect(!sync.busy)
    }

    @Test func mcpRowsNameTheirServerAndBlockedFilesShowApart() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: Data(#"""
            {"schema": 1, "data": {"account_sync": {"instructions": false, "skills": false, "hooks": false, "mcp": true},
             "accounts": [
              {"account": "claude/claude-work", "primitive": "mcp", "path": "/h/.claude.json", "state": "conflict",
               "detail": "server github: the account's own server, left alone", "name": "github"},
              {"account": "codex/codex-alt", "primitive": "mcp", "path": "/h/config.toml", "state": "blocked",
               "detail": "invalid TOML, not changed", "name": null},
              {"account": "claude/claude-work", "primitive": "skills", "path": "/h/skills", "state": "linked",
               "detail": ""}],
             "notes": []}}
            """#.utf8), stderr: ""))
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.load()
        #expect(sync.isOn(.mcp))
        let report = try #require(sync.report)
        #expect(report.conflicts.map(\.target) == ["github"])
        #expect(report.blocked.map(\.target) == ["config.toml"])
        #expect(report.accounts.last?.target == "skills")
    }

    @Test func aPassThatCouldNotRunShowsWhy() async throws {
        let runner = FakeRunner(.init(status: 1, stdout: Data(
            #"{"schema": 1, "data": {"account_sync": null, "accounts": [], "notes": ["account sync skipped: cannot read config.json"]}}"#
                .utf8), stderr: ""))
        let sync = AccountSyncModel(swe: SweClient(runner: runner))
        await sync.load()
        #expect(sync.error == "account sync skipped: cannot read config.json")
        #expect(sync.report?.accountSync == nil)
    }
}
