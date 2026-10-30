import Foundation
import Testing
@testable import QuiverCore

/// Replies by command prefix like ScriptedRunner, but under a lock:
/// extra-account usage fetches run in a task group, so calls land
/// concurrently.
final class LockedRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [[String]] = []
    var rules: [(prefix: [String], reply: CommandResult)]

    init(_ rules: [(prefix: [String], reply: CommandResult)]) { self.rules = rules }

    var calls: [[String]] { lock.withLock { recorded } }
    /// How many calls started with the prefix.
    func count(_ prefix: [String]) -> Int {
        calls.filter { $0.starts(with: prefix) }.count
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { recorded.append(arguments) }
        return lock.withLock {
            rules.first { arguments.starts(with: $0.prefix) }?.reply
                ?? CommandResult(status: 1, stdout: Data(), stderr: "unscripted: \(arguments.joined(separator: " "))")
        }
    }
}

/// A LockedRunner that can hold `harness usage` calls open until released,
/// so a test can look at the published lists mid-fetch.
final class HeldRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [[String]] = []
    /// Where held calls park; nil while nothing is held.
    private var gate: HoldGate?
    var rules: [(prefix: [String], reply: CommandResult)] = []

    var calls: [[String]] { lock.withLock { recorded } }
    func count(_ prefix: [String]) -> Int {
        calls.filter { $0.starts(with: prefix) }.count
    }

    /// Every `harness usage` call from here parks on the returned gate
    /// until `release()`, so a test can wait for one to be in flight.
    @discardableResult
    func holdUsageCalls() -> HoldGate {
        let gate = HoldGate()
        lock.withLock { self.gate = gate }
        return gate
    }

    func release() {
        lock.withLock { () -> HoldGate? in
            defer { gate = nil }
            return gate
        }?.open()
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let gate = lock.withLock { () -> HoldGate? in
            recorded.append(arguments)
            return arguments.starts(with: ["harness", "usage"]) ? self.gate : nil
        }
        if let gate { try await gate.pass() }
        return lock.withLock {
            rules.first { arguments.starts(with: $0.prefix) }?.reply
                ?? CommandResult(status: 1, stdout: Data(), stderr: "unscripted: \(arguments.joined(separator: " "))")
        }
    }
}

@MainActor
@Suite struct ExtraAccountLimitTests {
    let listBody = Data(#"""
    {"schema": 1, "data": {"harnesses": [
      {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 3,
       "rate_limit": {"status": "ok", "used_percent": 100, "remaining_percent": 0, "limit_reached": true, "reset_at": 1791760000, "window": "7d", "window_seconds": 604800, "plan_type": "teams", "remaining_units": null, "total_units": null, "fetched_at": 1791700000}},
      {"name": "cursor", "command": "cursor-agent", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 1,
       "rate_limit": null}
    ]}}
    """#.utf8)

    let devinUsage = CommandResult(status: 0, stdout: Data(#"""
    {"schema": 1, "data": {"name": "devin", "account_dir": "/t/devin-work/home",
     "limit": {"status": "ok", "used_percent": 20, "remaining_percent": 80, "limit_reached": false, "reset_at": null, "window": "7d", "window_seconds": 604800, "plan_type": "teams", "remaining_units": null, "total_units": null, "fetched_at": 1791700100}}}
    """#.utf8), stderr: "")

    let cursorSignedOut = CommandResult(status: 0, stdout: Data(#"""
    {"schema": 1, "data": {"name": "cursor", "account_dir": "/t/cursor-side/desktop", "limit": null}}
    """#.utf8), stderr: "")

    func profile(_ vendor: Vendor, _ id: String) -> Profile {
        Profile(id: id, vendor: vendor, name: "Work", isDefault: false, color: "#3b82f6")
    }

    /// Devin reads credentials under the home; Cursor reads the app's own
    /// sign-in db under the isolated user-data dir.
    func paths(_ id: String) -> ProfilePaths {
        ProfilePaths(home: URL(fileURLWithPath: "/t/\(id)/home", isDirectory: true),
                     desktopData: URL(fileURLWithPath: "/t/\(id)/desktop", isDirectory: true))
    }

    func model(_ runner: any CommandRunning,
               extras: @escaping @MainActor () -> [(Profile, ProfilePaths)])
        -> OtherLimitsModel {
        OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: snapshotFile(),
                         names: namesStore(), extraAccounts: extras)
    }

    /// Each test gets its own instance, so its folder goes when the test ends.
    private let scratch: TempDir
    init() throws { scratch = try TempDir() }

    /// A snapshot file inside this test's folder, never the real `~/.quiver`.
    func snapshotFile() -> URL {
        scratch.url.appending(path: "\(UUID().uuidString)/app-other-limits.json")
    }

    /// Same for the account names file.
    func namesStore() -> HarnessAccountNameStore {
        HarnessAccountNameStore(file: scratch.url.appending(path: "\(UUID().uuidString)/harness-names.json"))
    }

    @Test func anExtraAccountIsACardAfterItsOwnHarness() async {
        let devin = profile(.devin, "devin-work")
        let cursor = profile(.cursor, "cursor-side")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage", "devin"], devinUsage),
            (["harness", "usage", "cursor"], cursorSignedOut),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work")), (cursor, self.paths("cursor-side"))] }
        await model.load()

        #expect(model.cards.map(\.id) == ["devin", devin.id, "cursor", cursor.id])
        #expect(model.cards[1].title == "Devin: Work")
        #expect(model.cards[1].limit?.usedPercent == 20)
        // A null limit means the account is signed out, not unreadable.
        #expect(model.cards[3].limit == nil)
        #expect(model.cards[3].usageNote == .signIn)
        // Each account dir goes to its own swe call.
        #expect(runner.calls.contains(["harness", "usage", "devin",
                                       "--account-dir=/t/devin-work/home", "--json"]))
        #expect(runner.calls.contains(["harness", "usage", "cursor",
                                       "--account-dir=/t/cursor-side/desktop", "--json"]))
        // Rows exist only where a limit exists, after the harness's own.
        #expect(model.rows.map(\.id) == ["devin", devin.id])
        #expect(model.rows[1].title == "Devin: Work")
        #expect(model.rows[1].accountTitle == "Devin: Work")
    }

    /// A failed poll keeps the last cards, but not one for an account
    /// removed since: deleting it must take its card away at once.
    @Test func aRemovedAccountLeavesEvenWhenThePollFails() async {
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage", "devin"], devinUsage),
        ])
        var extras = [(devin, paths("devin-work"))]
        let model = model(runner) { extras }
        await model.load()
        #expect(model.cards.map(\.id) == ["devin", devin.id, "cursor"])
        #expect(model.rows.map(\.id) == ["devin", devin.id])

        extras = []
        runner.rules = []
        await model.load()
        #expect(model.cards.map(\.id) == ["devin", "cursor"])
        #expect(model.rows.map(\.id) == ["devin"])
        #expect(model.note?.hasPrefix("Couldn't refresh") == true)
    }

    @Test func aGoodReadingIsNotRefetchedInsideTheWindow() async {
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        await model.load()
        #expect(runner.count(["harness", "usage"]) == 1)

        // A forced load refetches; a failed-reading refresh does not.
        await model.load(force: true)
        #expect(runner.count(["harness", "usage"]) == 2)
        await model.load(refreshFailed: true)
        #expect(runner.count(["harness", "usage"]) == 2)
    }

    @Test func aFailedReadRefetchesOnTheNextLoad() async {
        let devin = profile(.devin, "devin-work")
        let down = CommandResult(status: 1, stdout: Data(), stderr: "connection refused")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], down),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        #expect(model.cards.first { $0.profile?.id == devin.id }?.limit == nil)

        // A failed reading is refetched by a plain load, and by refreshFailed.
        await model.load()
        #expect(runner.count(["harness", "usage"]) == 2)
        await model.load(refreshFailed: true)
        #expect(runner.count(["harness", "usage"]) == 3)

        // Recovered: the card carries the limit and stops refetching.
        runner.rules = [
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ]
        await model.load()
        #expect(model.cards.first { $0.profile?.id == devin.id }?.limit?.usedPercent == 20)
        await model.load()
        #expect(runner.count(["harness", "usage"]) == 4)
    }

    @Test func anSweWithoutTheSubcommandIsAskedOnceAndSaysToUpdate() async {
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness"], .init(status: 2, stdout: Data(), stderr: "Unknown harness subcommand: usage")),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()

        #expect(model.sweKnowsAccountUsage == false)
        #expect(model.cards.first { $0.profile?.id == devin.id }?.usageNote == .updateSwe)
        await model.load()
        #expect(runner.count(["harness", "usage"]) == 1)
    }

    @Test func anUnstarredVendorHidesItsExtras() async {
        // Cursor is active here, devin starred: only devin's extra shows,
        // and cursor's account is never polled.
        let json = Data(#"""
        {"schema": 1, "data": {"harnesses": [
          {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "starred", "sessions_100d": 3, "rate_limit": null},
          {"name": "cursor", "command": "cursor-agent", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": 1, "rate_limit": null}
        ]}}
        """#.utf8)
        let devin = profile(.devin, "devin-work")
        let cursor = profile(.cursor, "cursor-side")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: json, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work")), (cursor, self.paths("cursor-side"))] }
        await model.load()

        #expect(model.cards.map(\.id) == ["devin", devin.id])
        // The extra fetch runs after the list call, on the starred set it
        // just learned: cursor's account is never polled, not even once.
        #expect(runner.count(["harness", "usage", "cursor"]) == 0)
        await model.load(force: true)
        #expect(runner.count(["harness", "usage", "cursor"]) == 0)
        #expect(runner.count(["harness", "usage", "devin"]) == 2)
    }

    @Test func aNewlyStarredVendorFetchesItsExtrasInTheSameLoad() async {
        // Devin starts active; once the list reports it starred, that same
        // load already fetches the account's usage.
        let active = Data(#"""
        {"schema": 1, "data": {"harnesses": [
          {"name": "devin", "command": "devin", "description": "", "version": null, "aliases": [], "tags": [], "state": "active", "sessions_100d": 3, "rate_limit": null}
        ]}}
        """#.utf8)
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: active, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        #expect(runner.count(["harness", "usage", "devin"]) == 0)
        #expect(model.cards.isEmpty)

        runner.rules = [
            (["list"], .init(status: 0, stdout: self.listBody, stderr: "")),
            (["harness", "usage"], self.devinUsage),
        ]
        await model.load()
        #expect(runner.count(["harness", "usage", "devin"]) == 1)
        #expect(model.cards.contains { $0.profile?.id == devin.id })
    }

    @Test func aReplyReportingAFailedReadingIsRefetched() async {
        // status "timeout" arrives inside a successful reply; it counts as
        // failed so a Refresh-press load refetches it.
        let timeout = CommandResult(status: 0, stdout: Data(#"""
        {"schema": 1, "data": {"name": "devin", "account_dir": "/t/devin-work/home",
         "limit": {"status": "timeout", "used_percent": 0, "remaining_percent": 0, "limit_reached": false, "window": "timeout", "window_seconds": null}}}
        """#.utf8), stderr: "")
        let auth = CommandResult(status: 0, stdout: Data(#"""
        {"schema": 1, "data": {"name": "devin", "account_dir": "/t/devin-work/home",
         "limit": {"status": "auth-required", "used_percent": 0, "remaining_percent": 0, "limit_reached": false, "window": "re-login", "window_seconds": null}}}
        """#.utf8), stderr: "")
        let unavailable = CommandResult(status: 0, stdout: Data(#"""
        {"schema": 1, "data": {"name": "devin", "account_dir": "/t/devin-work/home",
         "limit": {"status": "ok", "used_percent": 45, "remaining_percent": 55, "limit_reached": false, "window": "1d", "window_seconds": 86400,
                   "windows": [{"window": "1d", "used_percent": 45}, {"window": "7d", "used_percent": 31, "unavailable": true, "as_of": 1791700000}]}}}
        """#.utf8), stderr: "")
        for reply in [timeout, auth, unavailable] {
            let devin = profile(.devin, "devin-work")
            let runner = LockedRunner([
                (["list"], .init(status: 0, stdout: listBody, stderr: "")),
                (["harness", "usage"], reply),
            ])
            let model = model(runner) { [(devin, self.paths("devin-work"))] }
            await model.load()
            // The reading is kept (last-known or the marker itself) but
            // flagged failed, so a plain load within the TTL leaves it
            // alone while refreshFailed asks again.
            await model.load()
            #expect(runner.count(["harness", "usage"]) == 2)
            await model.load(refreshFailed: true)
            #expect(runner.count(["harness", "usage"]) == 3)
            runner.rules = [
                (["list"], .init(status: 0, stdout: self.listBody, stderr: "")),
                (["harness", "usage"], self.devinUsage),
            ]
            await model.load(refreshFailed: true)
            #expect(runner.count(["harness", "usage"]) == 4)
        }
    }

    @Test func anAccountUsageErrorIsNotReadAsOldSwe() async {
        // "couldn't read devin usage for this account" is an outage in the
        // account, not a swe that lacks the subcommand: the reading is
        // failed (the card says the fetch failed, not "update swe") and the
        // feature keeps being asked.
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], .init(status: 1, stdout: Data(),
                                          stderr: "couldn't read devin usage for this account")),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        #expect(model.sweKnowsAccountUsage)
        #expect(model.cards.first { $0.profile?.id == devin.id }?.usageNote == .fetchFailed)
        await model.load()
        #expect(runner.count(["harness", "usage"]) == 2)
    }

    @Test func refreshFailedReReadsAnExpiredReadingAndSkipsAFreshOne() async {
        // Inside the refresh window only failures are re-checked; a
        // reading older than the window is re-read even when it succeeded.
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        model.cacheTTL = 0
        await model.load()
        // cacheTTL zero means the just-taken reading is already expired.
        await model.load(refreshFailed: true)
        #expect(runner.count(["harness", "usage"]) == 2)
        // The re-read is fresh again, so another Refresh-press skips it.
        model.cacheTTL = 3600
        await model.load(refreshFailed: true)
        #expect(runner.count(["harness", "usage"]) == 2)
    }

    @Test func aFailedFetchPublishesTheKeptReadingAsUnavailable() async {
        // A fetch error after a good read keeps the old figures but marks
        // every window unavailable, the way swe's outage fallback does:
        // LimitReadings come out stale and the cards grey them.
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        #expect(model.rows.first { $0.profile?.id == devin.id }?.limit.allWindows
            .allSatisfy { !$0.unavailable } == true)

        runner.rules = [
            (["list"], .init(status: 0, stdout: self.listBody, stderr: "")),
            (["harness", "usage"], .init(status: 1, stdout: Data(),
                                          stderr: "couldn't read devin usage for this account")),
        ]
        await model.load(force: true)
        let kept = model.cards.first { $0.profile?.id == devin.id }?.limit
        #expect(kept?.status == .ok)
        #expect(kept?.allWindows.allSatisfy { $0.unavailable } == true)
        // The figures are the last-known ones, dated to the original read.
        #expect(kept?.allWindows.first?.usedPercent == 20)
        let readings = LimitReading.all(cards: [], others: model.rows, now: Date(), sweFreshFor: 300)
        #expect(readings.filter { $0.source == devin.id }.allSatisfy { $0.isStale })

        // A successful refetch clears the unavailable marks.
        runner.rules = [
            (["list"], .init(status: 0, stdout: self.listBody, stderr: "")),
            (["harness", "usage"], self.devinUsage),
        ]
        await model.load(refreshFailed: true)
        #expect(model.rows.first { $0.profile?.id == devin.id }?.limit.allWindows
            .allSatisfy { !$0.unavailable } == true)
    }

    @Test func extrasStayVisibleWhileARefreshRuns() async {
        // The lists publish once, after the extra fetches land: while an
        // account fetch is mid-flight the previous extra row and card stay.
        let devin = profile(.devin, "devin-work")
        let runner = HeldRunner()
        runner.rules = [
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ]
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        #expect(model.rows.contains { $0.id == devin.id })
        #expect(model.cards.contains { $0.id == devin.id })

        let held = runner.holdUsageCalls()
        let task = Task { await model.load(force: true) }
        // Wait until the account fetch is actually in flight.
        #expect(await held.arrived())
        #expect(model.rows.contains { $0.id == devin.id })
        #expect(model.cards.contains { $0.id == devin.id })
        runner.release()
        await task.value
        #expect(model.rows.contains { $0.id == devin.id })
    }

    @Test func anUnstampedReadingIsStampedWithTheFetchTime() async {
        // swe older than the fetched_at field returns no timestamp; the
        // row keeps the real fetch time so it does not read as stale.
        let old = CommandResult(status: 0, stdout: Data(#"""
        {"schema": 1, "data": {"name": "devin", "account_dir": "/t/devin-work/home",
         "limit": {"status": "ok", "used_percent": 20, "remaining_percent": 80, "limit_reached": false, "reset_at": 1791760000, "window": "7d", "window_seconds": 604800}}}
        """#.utf8), stderr: "")
        let devin = profile(.devin, "devin-work")
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], old),
        ])
        let model = model(runner) { [(devin, self.paths("devin-work"))] }
        await model.load()
        #expect(model.cards.first { $0.profile?.id == devin.id }?.limit?.fetchedAt != nil)
    }

    @Test func aRemovedProfileDropsItsCardAndSavedReading() async {
        let devin = profile(.devin, "devin-work")
        var current: [(Profile, ProfilePaths)] = [(devin, paths("devin-work"))]
        let file = snapshotFile()
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: file,
                                     names: namesStore(), extraAccounts: { current })
        await model.load()
        #expect(model.cards.contains { $0.profile?.id == devin.id })

        current = []
        await model.load()
        #expect(!model.cards.contains { $0.profile != nil })

        // The reading left the snapshot too: a rehydrated model shows nothing.
        let revived = OtherLimitsModel(swe: SweClient(runner: FakeRunner(
            .init(status: 0, stdout: listBody, stderr: ""))), snapshotFile: file,
            names: namesStore(), extraAccounts: { current })
        #expect(!revived.cards.contains { $0.profile != nil })
    }
}

@Suite struct ExtraAccountVendorTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    @Test func onlyClaudeAndCodexOwnADefaultProfile() {
        #expect(Vendor.claude.hasDefaultProfile && Vendor.codex.hasDefaultProfile)
        #expect(!Vendor.devin.hasDefaultProfile && !Vendor.cursor.hasDefaultProfile)
        #expect(ProfileStore.defaultProfiles.map(\.vendor) == [.claude, .codex])
    }

    @Test func devinAndCursorProfilesGetTheirOwnTrees() throws {
        let root = scratch.url.appending(path: "accounts-\(UUID().uuidString)")
        let store = ProfileStore(root: root.appending(path: ".quiver/accounts"), userHome: root)

        // No file yet: only Claude and Codex synthesise defaults.
        #expect(try store.load().map(\.id) == ["claude-default", "codex-default"])

        let devin = try store.add(vendor: .devin, name: "Work")
        let cursor = try store.add(vendor: .cursor, name: "Side")
        #expect(devin.id == "devin-work")
        #expect(cursor.id == "cursor-side")
        #expect(try store.load().map(\.id) == ["claude-default", "codex-default", "devin-work", "cursor-side"])

        // Same shape as the other extras: <root>/<vendor>/<id>/{home,desktop}.
        let paths = store.paths(for: devin)
        #expect(paths.home.path == root.appending(path: ".quiver/accounts/devin/devin-work/home").path)
        #expect(paths.desktopData?.path == root.appending(path: ".quiver/accounts/devin/devin-work/desktop").path)
        #expect(FileManager.default.fileExists(atPath: paths.desktopData!.path))
    }
}
