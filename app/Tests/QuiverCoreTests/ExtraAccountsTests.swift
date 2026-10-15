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

    func model(_ runner: LockedRunner,
               extras: @escaping @MainActor () -> [(Profile, ProfilePaths)])
        -> OtherLimitsModel {
        OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: tempSnapshotFile(),
                         names: tempNamesStore(), extraAccounts: extras)
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
        // First load starred cursor is still unknown, so it is asked once;
        // afterwards its unstarred state is known and it is never polled,
        // even when the devin extra is forced to refetch.
        await model.load(force: true)
        #expect(runner.count(["harness", "usage", "cursor"]) == 1)
        #expect(runner.count(["harness", "usage", "devin"]) == 2)
    }

    @Test func aRemovedProfileDropsItsCardAndSavedReading() async {
        let devin = profile(.devin, "devin-work")
        var current: [(Profile, ProfilePaths)] = [(devin, paths("devin-work"))]
        let file = tempSnapshotFile()
        let runner = LockedRunner([
            (["list"], .init(status: 0, stdout: listBody, stderr: "")),
            (["harness", "usage"], devinUsage),
        ])
        let model = OtherLimitsModel(swe: SweClient(runner: runner), snapshotFile: file,
                                     names: tempNamesStore(), extraAccounts: { current })
        await model.load()
        #expect(model.cards.contains { $0.profile?.id == devin.id })

        current = []
        await model.load()
        #expect(!model.cards.contains { $0.profile != nil })

        // The reading left the snapshot too: a rehydrated model shows nothing.
        let revived = OtherLimitsModel(swe: SweClient(runner: FakeRunner(
            .init(status: 0, stdout: listBody, stderr: ""))), snapshotFile: file,
            names: tempNamesStore(), extraAccounts: { current })
        #expect(!revived.cards.contains { $0.profile != nil })
    }
}

@Suite struct ExtraAccountVendorTests {
    @Test func onlyClaudeAndCodexOwnADefaultProfile() {
        #expect(Vendor.claude.hasDefaultProfile && Vendor.codex.hasDefaultProfile)
        #expect(!Vendor.devin.hasDefaultProfile && !Vendor.cursor.hasDefaultProfile)
        #expect(ProfileStore.defaultProfiles.map(\.vendor) == [.claude, .codex])
    }

    @Test func devinAndCursorProfilesGetTheirOwnTrees() throws {
        let root = FileManager.default.temporaryDirectory.appending(path: "accounts-\(UUID().uuidString)")
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
