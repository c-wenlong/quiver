import Foundation
import Synchronization
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
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        // Claude and Codex have accounts of their own; unpolled harnesses have nothing to show.
        #expect(model.rows.map(\.name) == ["devin", "cursor"])
        #expect(model.rows[0].limit.usedPercent == 100)
        #expect(model.rows[1].limit.status == .authRequired)
        // swe's "—" placeholder means no plan, not a plan called "—".
        #expect(model.rows[0].limit.planType == "teams")
        #expect(model.rows[1].limit.planType == nil)
        // Claude and Codex are fetched per account by the app; swe must not poll them too.
        #expect(runner.calls == [["list", "--usage", "--json", "--cache-ttl=300", "--exclude=claude,codex"]])
    }

    @Test func rowsCarryTheirCommandAndWhetherItIsOnPath() async {
        let json = String(decoding: body, as: UTF8.self)
            .replacingOccurrences(of: #""name": "devin", "command": "devin","#, with: #""name": "devin", "command": "devin", "installed": true,"#)
            .replacingOccurrences(of: #""name": "cursor", "command": "cursor-agent","#, with: #""name": "cursor", "display_name": "Cursor", "command": "cursor-agent", "installed": false,"#)
            .replacingOccurrences(of: #""name": "claude", "command": "claude","#, with: #""name": "claude", "command": "claude", "installed": true,"#)
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))),
                  snapshotFile: tempSnapshotFile(), names: tempNamesStore())
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
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))),
                  snapshotFile: tempSnapshotFile(), names: tempNamesStore())
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

    @Test func cardsSayWhySweHasNoReading() async {
        // swe's no_reading tells a signed-out harness from a failed fetch;
        // a row without it (or from an older swe) keeps the plain note.
        let json = #"""
        {"schema": 1, "data": {"harnesses": [
          {"name": "copilot", "command": "copilot", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1, "rate_limit": null, "no_reading": "signed_out"},
          {"name": "cursor", "command": "cursor-agent", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1, "rate_limit": null, "no_reading": "error"},
          {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1, "rate_limit": null},
          {"name": "droid", "command": "droid", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1, "rate_limit": null, "no_reading": "something-new"}
        ]}}
        """#
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))),
                  snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        #expect(model.cards.map(\.name) == ["copilot", "cursor", "devin", "droid"])
        #expect(model.cards.map(\.usageNote) == [.signIn, .fetchFailed, nil, nil])
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
        let model = OtherLimitsModel(swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: Data(json.utf8), stderr: ""))),
                  snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        #expect(model.cards.map(\.name) == ["devin"])
        // The menu bar, notifications and widgets read rows: starred only too.
        #expect(model.rows.map(\.name) == ["devin"])
    }

    @Test func aFailedLoadKeepsTheLastStarredSet() async {
        // Before swe has answered once, nothing is hidden: unknown means show.
        let runner = FakeRunner(.init(status: 0, stdout: body, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
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

    @Test func anOlderLoadCannotBringBackAnUnstarredHarness() async throws {
        // A star change on Harnesses reloads limits, overlapping the
        // five-minute background load: the slower answer must not win.
        let json = #"""
        {"schema": 1, "data": {"harnesses": [
          {"name": "pi", "command": "pi", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1, "rate_limit": null}
        ]}}
        """#
        let runner = GatedRunner(
            first: .init(status: 0, stdout: Data(json.utf8), stderr: ""),
            later: .init(status: 0, stdout: Data(json.replacingOccurrences(of: "starred", with: "active").utf8), stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        let outdated = Task { await model.load() }
        defer { outdated.cancel() }
        try #require(await runner.gate.arrived())
        await model.load()
        runner.gate.open()
        await outdated.value
        // The newer load is the truth: unstarred pi keeps no star, no card.
        #expect(model.starred == [])
        #expect(model.cards.isEmpty)
    }

    /// A failed refresh keeps the last cards and rows and says so,
    /// rather than blanking every harness over one swe hiccup.
    @Test func aFailedRefreshKeepsTheLastCards() async {
        let runner = QueueRunner([.init(status: 0, stdout: body, stderr: ""),
                                  .init(status: 1, stdout: Data(), stderr: "swe timed out")])
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        let cards = model.cards
        let rows = model.rows
        #expect(!cards.isEmpty)
        #expect(model.note == nil)
        await model.load()
        #expect(model.cards == cards)
        #expect(model.rows == rows)
        #expect(model.note?.hasPrefix("Couldn't refresh") == true)
    }

    @Test func anOldSweLeavesTheSectionEmptyWithAReason() async {
        let runner = FakeRunner(.init(status: 0, stdout: Data("Unexpected args: --json".utf8), stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        #expect(model.rows.isEmpty)
        #expect(model.note?.contains("older") == true)
    }
}

/// The first `run` parks at `gate` until the test opens it; every call
/// after answers `later` at once, so an extra read cannot hang the test.
private final class GatedRunner: CommandRunning, Sendable {
    let gate = HoldGate()
    private let calls = Mutex(0)
    let first: CommandResult
    let later: CommandResult

    init(first: CommandResult, later: CommandResult) {
        self.first = first
        self.later = later
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let isFirst = calls.withLock { n -> Bool in
            n += 1
            return n == 1
        }
        guard isFirst else { return later }
        try await gate.pass()
        return first
    }
}

/// Answers each run from a queue, then repeats the last reply.
final class QueueRunner: CommandRunning, @unchecked Sendable {
    private var replies: [CommandResult]
    private(set) var calls: [[String]] = []
    init(_ replies: [CommandResult]) { self.replies = replies }
    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        return replies.count > 1 ? replies.removeFirst() : replies[0]
    }
}

@MainActor
@Suite struct OtherLimitsCacheTests {
    let okBody = Data(#"""
    {"schema": 1, "data": {"harnesses": [
      {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 3,
       "rate_limit": {"status": "ok", "used_percent": 40, "remaining_percent": 60, "limit_reached": false, "reset_at": null, "window": "7d", "window_seconds": 604800, "plan_type": "teams", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}}
    ]}}
    """#.utf8)

    @Test func aRefreshLoadAsksSweToRefetchOnlyTheFailed() async {
        let runner = FakeRunner(.init(status: 0, stdout: okBody, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load(refreshFailed: true)
        #expect(runner.calls == [["list", "--usage", "--json", "--cache-ttl=300",
                                 "--refresh-failed", "--exclude=claude,codex"]])
    }

    @Test func anSweWithoutTheNewFlagsIsRetriedPlainAndRemembered() async {
        let runner = QueueRunner([
            .init(status: 2, stdout: Data(), stderr: "Unknown flag: --cache-ttl"),
            .init(status: 0, stdout: okBody, stderr: ""),
        ])
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        #expect(model.rows.map(\.name) == ["devin"])
        #expect(runner.calls == [
            ["list", "--usage", "--json", "--cache-ttl=300", "--exclude=claude,codex"],
            ["list", "--usage", "--json", "--exclude=claude,codex"],
        ])

        // The session remembers: even a refresh press never sends them again.
        await model.load(refreshFailed: true)
        #expect(runner.calls.last == ["list", "--usage", "--json", "--exclude=claude,codex"])
        // swe keeps its own five minutes, so staleness must not use the
        // longer window the flag would have asked for.
        model.cacheTTL = 7200
        #expect(model.acceptedCacheTTL == 0)
    }

    @Test func anSweThatTookTheFlagsHonoursTheWindow() async {
        let runner = FakeRunner(.init(status: 0, stdout: okBody, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        model.cacheTTL = 7200
        await model.load()
        #expect(model.acceptedCacheTTL == 7200)
    }

    @Test func anSweRejectingBothFlagSetsEndsAtThePlainCall() async {
        let runner = QueueRunner([
            .init(status: 2, stdout: Data(), stderr: "Unknown flag: --cache-ttl"),
            .init(status: 2, stdout: Data(), stderr: "Unknown flag: --exclude"),
            .init(status: 0, stdout: okBody, stderr: ""),
        ])
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(), names: tempNamesStore())
        await model.load()
        #expect(model.rows.map(\.name) == ["devin"])
        #expect(runner.calls == [
            ["list", "--usage", "--json", "--cache-ttl=300", "--exclude=claude,codex"],
            ["list", "--usage", "--json", "--exclude=claude,codex"],
            ["list", "--usage", "--json"],
        ])

        // Both "doesn't know" facts persist for the session.
        await model.load(refreshFailed: true)
        #expect(runner.calls.last == ["list", "--usage", "--json"])
    }

    @Test func theLastSuccessfulListRoundTripsThroughTheSnapshotFile() async {
        let file = tempSnapshotFile()
        let runner = FakeRunner(.init(status: 0, stdout: okBody, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: file, names: tempNamesStore())
        await model.load()
        #expect(FileManager.default.fileExists(atPath: file.path))

        // A new model on the same file shows the saved rows before swe answers.
        let revived = OtherLimitsModel(swe: SweClient(runner: FakeRunner(
            .init(status: 2, stdout: Data(), stderr: "down"))), snapshotFile: file, names: tempNamesStore())
        #expect(revived.rows.map(\.name) == ["devin"])
        #expect(revived.cards.map(\.name) == ["devin"])
        #expect(revived.starred == ["devin"])
        #expect(revived.rows[0].limit.usedPercent == 40)
    }

    @Test func aMissingOrCorruptSnapshotStartsCold() async throws {
        let file = tempSnapshotFile()
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "not json".write(to: file, atomically: true, encoding: .utf8)
        let revived = OtherLimitsModel(swe: SweClient(runner: FakeRunner(
            .init(status: 0, stdout: okBody, stderr: ""))), snapshotFile: file, names: tempNamesStore())
        #expect(revived.rows.isEmpty && revived.starred == nil)
    }
}

@MainActor
@Suite struct HarnessAccountNameModelTests {
    func tempNamesFile() -> URL {
        usageScratch
            .appendingPathComponent("names-\(UUID().uuidString)")
            .appendingPathComponent("harness-names.json")
    }

    let body = Data(#"""
    {"schema": 1, "data": {"harnesses": [
      {"name": "cursor", "display_name": "Cursor IDE", "command": "cursor-agent", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1,
       "rate_limit": {"status": "ok", "used_percent": 40, "remaining_percent": 60, "limit_reached": false, "reset_at": null, "window": "7d", "window_seconds": 604800, "plan_type": null, "remaining_units": null, "total_units": null, "fetched_at": 1791700000}}
    ]}}
    """#.utf8)

    @Test func accountNameWinsOverDisplayNameAndBrand() async {
        let names = HarnessAccountNameStore(file: tempNamesFile())
        try? names.setName("Cortex", for: "cursor")
        let model = OtherLimitsModel(
            swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))),
            snapshotFile: tempSnapshotFile(), names: names)
        await model.load()
        // accountName > displayName > brand, on rows and cards alike.
        #expect(model.rows[0].title == "Cortex")
        #expect(model.rows[0].accountTitle == "Cursor: Cortex")
        #expect(model.cards[0].title == "Cortex")
        // The harness's own name ignores the account name.
        #expect(model.cards[0].harnessTitle == "Cursor IDE")
    }

    @Test func aCardWithoutAnAccountNameFallsBackToDisplayNameThenBrand() async {
        let model = OtherLimitsModel(
            swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))),
            snapshotFile: tempSnapshotFile(),
            names: HarnessAccountNameStore(file: tempNamesFile()))
        await model.load()
        #expect(model.cards[0].title == "Cursor IDE")
        #expect(model.cards[0].harnessTitle == "Cursor IDE")
        #expect(OtherLimitsModel.Card(name: "cursor", displayName: nil, command: "cursor-agent",
                                    cliInstalled: nil, limit: nil).harnessTitle == "Cursor")
    }

    @Test func renameWritesTheNamesFileAndNeverCallsSwe() async throws {
        let file = tempSnapshotFile()
        let names = HarnessAccountNameStore(file: tempNamesFile())
        let runner = FakeRunner(.init(status: 0, stdout: body, stderr: ""))
        let model = OtherLimitsModel(swe: SweClient(runner: runner),
                                     snapshotFile: file, names: names)
        await model.load()
        let calls = runner.calls

        try model.rename("cursor", to: "Cortex")

        #expect(runner.calls == calls)  // no swe run: harness.json untouched
        #expect(model.cards[0].accountName == "Cortex")
        #expect(model.rows[0].accountName == "Cortex")
        #expect(model.rows[0].accountTitle == "Cursor: Cortex")
        #expect(names.load()["cursor"] == "Cortex")
        // The snapshot carries it, so the next launch shows it too.
        let revived = OtherLimitsModel(
            swe: SweClient(runner: FakeRunner(.init(status: 2, stdout: Data(), stderr: "down"))),
            snapshotFile: file, names: names)
        #expect(revived.cards[0].accountName == "Cortex")

        try model.rename("cursor", to: "")
        #expect(model.cards[0].accountName == nil)
        #expect(names.load()["cursor"] == nil)
    }

    @Test func aSnapshotWithoutAccountNameStillDecodes() async throws {
        // Snapshots written before account names exist carry no such key.
        let file = tempSnapshotFile()
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let json = #"""
        {"savedAt": 800000000, "rows": [
          {"name": "devin", "limit": {"status": "ok", "usedPercent": 40, "remainingPercent": 60,
            "limitReached": false}, "displayName": "Devin Teams", "command": "devin"}],
         "cards": [{"name": "devin", "displayName": "Devin Teams", "command": "devin",
                    "cliInstalled": true, "limit": null}],
         "starred": ["devin"], "cliInstalled": {"devin": true}}
        """#
        try json.write(to: file, atomically: true, encoding: .utf8)
        let revived = OtherLimitsModel(
            swe: SweClient(runner: FakeRunner(.init(status: 2, stdout: Data(), stderr: "down"))),
            snapshotFile: file, names: HarnessAccountNameStore(file: tempNamesFile()))
        #expect(revived.rows.map(\.name) == ["devin"])
        #expect(revived.rows[0].accountName == nil)
        #expect(revived.rows[0].title == "Devin Teams")
    }

    @Test func hydrateStampsARenameMadeAfterTheSnapshot() async throws {
        let file = tempSnapshotFile()
        let names = HarnessAccountNameStore(file: tempNamesFile())
        let model = OtherLimitsModel(
            swe: SweClient(runner: FakeRunner(.init(status: 0, stdout: body, stderr: ""))),
            snapshotFile: file, names: names)
        await model.load()
        // The name changed after the snapshot was written.
        try names.setName("Cortex", for: "cursor")
        let revived = OtherLimitsModel(
            swe: SweClient(runner: FakeRunner(.init(status: 2, stdout: Data(), stderr: "down"))),
            snapshotFile: file, names: names)
        #expect(revived.cards[0].accountName == "Cortex")
        #expect(revived.rows[0].accountTitle == "Cursor: Cortex")
    }
}
