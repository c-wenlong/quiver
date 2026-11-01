import Foundation
import Testing
@testable import QuiverCore

/// Behaviours pinned after review of the Accounts milestone.
@Suite struct ProfileStoreReviewTests {
    /// Each test gets its own instance, so its folder goes when the test ends.
    private let home: TempDir
    init() throws { home = try TempDir() }

    func scratch() throws -> URL { home.url }

    @Test func aRemovedAccountsIdAndHomeAreNeverReused() throws {
        // Claude Code names its keychain item after the config home, so a reused
        // home would hand the old account's login to the new one.
        let home = try scratch()
        let store = ProfileStore(root: home.appending(path: ".swe/accounts"), userHome: home)
        let first = try store.add(vendor: .claude, name: "Work")
        try store.remove(id: first.id)
        let second = try store.add(vendor: .claude, name: "Work")
        #expect(second.id == "claude-work-2")
        #expect(store.paths(for: second).home != store.paths(for: first).home)
        // And the retirement survives a fresh store reading the file.
        let fresh = ProfileStore(root: home.appending(path: ".swe/accounts"), userHome: home)
        try fresh.remove(id: second.id)
        #expect(try fresh.add(vendor: .claude, name: "Work").id == "claude-work-3")
    }

    @Test func aNonDefaultEntryClaimingADefaultIdIsMalformed() throws {
        let home = try scratch()
        let root = home.appending(path: ".swe/accounts")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try ##"{"schema":1,"profiles":[{"id":"claude-default","vendor":"claude","name":"Fake","isDefault":false,"color":"#123456"}]}"##
            .write(to: root.appending(path: "profiles.json"), atomically: true, encoding: .utf8)
        #expect(throws: ProfileStoreError.self) {
            _ = try ProfileStore(root: root, userHome: home).load()
        }
    }

    @Test func threeDigitColorsExpandLikeCSS() {
        #expect(HexColor.rgb("#abc") == HexColor.rgb("#aabbcc"))
        #expect(HexColor.rgb("#3b82f6")?.red == 0x3b)
        #expect(HexColor.rgb("blue") == nil)
    }
}

@Suite struct ProcessLauncherTests {
    @Test func aLaunchThatFailsReportsWhy() async {
        do {
            try await ProcessLauncher().launch(LaunchCommand(executable: "/bin/sh", arguments: ["-c", "echo 'Unable to find application named Claude' >&2; exit 1"]))
            Issue.record("a failing launch should throw")
        } catch {
            #expect(error is LaunchError)
            #expect(error.localizedDescription.contains("Unable to find application"))
        }
    }

    @Test func aLaunchThatSucceedsReturns() async throws {
        try await ProcessLauncher().launch(LaunchCommand(executable: "/usr/bin/true", arguments: []))
    }
}

@MainActor
@Suite struct OtherLimitsFallbackTests {
    /// Each test gets its own instance, so its folder goes when the test ends.
    private let scratch: TempDir
    init() throws { scratch = try TempDir() }

    @Test func anSweWithoutExcludeIsAskedAgainWithoutIt() async {
        let ok = Data(#"{"schema":1,"data":{"harnesses":[{"name":"devin","command":"devin","description":"","version":null,"aliases":[],"tags":[],"state":"starred","sessions_100d":1,"rate_limit":{"status":"ok","used_percent":5,"remaining_percent":95,"limit_reached":false,"reset_at":null,"window":"1d","window_seconds":null,"plan_type":"teams","remaining_units":null,"total_units":null,"fetched_at":1791700000}}]}}"#.utf8)
        let runner = SequencedRunner([
            CommandResult(status: 1, stdout: Data(), stderr: "\u{1B}[31mUnknown flag: --exclude=claude,codex\u{1B}[0m"),
            CommandResult(status: 0, stdout: ok, stderr: ""),
        ])
        let model = OtherLimitsModel(
            swe: SweClient(runner: runner),
            snapshotFile: scratch.url.appending(path: "app-other-limits.json"),
            names: HarnessAccountNameStore(file: scratch.url.appending(path: "harness-names.json")))
        await model.load()
        #expect(model.rows.map(\.name) == ["devin"])
        #expect(model.note == nil)
    }
}

@Suite struct UsageCacheLockTests {
    @Test func concurrentWritersForDifferentAccountsKeepBothCooldowns() async throws {
        let dir = try TempDir()
        let url = dir.url.appending(path: "app-usage.json")
        let until = Date(timeIntervalSinceReferenceDate: 800_000_000)
        let keys = (0..<2).map { AccountKey(provider: .claude, configHome: "/home/\($0)", isDefault: false) }
        // Two stores on one file stand in for two running copies of the app.
        await withTaskGroup(of: Void.self) { group in
            for (index, key) in keys.enumerated() {
                group.addTask {
                    let store = UsageCacheStore(url: url)
                    for round in 0..<200 {
                        store.save(StoredAccount(fingerprint: "f\(index)-\(round)", reading: nil, cooldownUntil: until), for: key)
                    }
                }
            }
        }
        let store = UsageCacheStore(url: url)
        for (index, key) in keys.enumerated() {
            #expect(store.entry(for: key)?.cooldownUntil == until)
            #expect(store.entry(for: key)?.fingerprint == "f\(index)-199")
        }
        withExtendedLifetime(dir) {}
    }
}

@MainActor
@Suite struct AccountsKeepFreshTests {
    @Test(.timeLimit(.minutes(1))) func anOpenPageKeepsReloadingUntilItCloses() async throws {
        let home = try TempDir()
        let usage = CountedUsage()
        let model = AccountsModel(store: ProfileStore(root: home.url.appending(path: "a"), userHome: home.url),
                                  usage: usage, launcher: FakeLauncher(), terminal: { .terminal }, loginPath: { "" })
        let task = Task { await model.keepFresh(every: .milliseconds(10)) }
        // Three reloads, two default accounts each.
        #expect(await usage.calls.reach(6))
        task.cancel()
        // Each load's fetches run in a task group it awaits, so once
        // keepFresh returns no fetch can follow: its ending is the whole
        // check. The time limit fails a loop that ignores cancellation
        // (a task group racing a deadline would wait on it forever).
        await task.value
        withExtendedLifetime(home) {}
    }
}

/// Counts usage fetches on a gate the test can wait on, and answers each
/// as signed out.
private final class CountedUsage: UsageFetching, Sendable {
    let calls = CountGate()

    func usage(for key: AccountKey, force: Bool, freshFor _: TimeInterval) async -> AccountUsage {
        await calls.bump()
        return AccountUsage(status: .signedOut, plan: nil, email: nil, windows: [], fetchedAt: nil, isStale: false)
    }

    func cachedUsage(for key: AccountKey) async -> AccountUsage? { nil }
}

@Suite struct CrossProcessTests {
    /// Each test gets its own instance, so its folder goes when the test ends.
    private let scratch: TempDir
    init() throws { scratch = try TempDir() }

    @Test func twoStoresAddingAtOnceKeepBothAccounts() async throws {
        let home = scratch.url
        let root = home.appending(path: ".swe/accounts")
        // Two stores on one folder stand in for two running copies of the app.
        await withTaskGroup(of: Void.self) { group in
            for copy in 0..<2 {
                group.addTask {
                    let store = ProfileStore(root: root, userHome: home)
                    for n in 0..<15 { do { _ = try store.add(vendor: .codex, name: "c\(copy)-\(n)") } catch { Issue.record("add failed: \(error)") } }
                }
            }
        }
        let names = try ProfileStore(root: root, userHome: home).load().map(\.name)
        #expect(names.count == 2 + 30)
    }

    @Test func aSuccessSavedLaterKeepsAnotherCopysActiveCooldown() {
        let url = scratch.url.appending(path: "app-usage.json")
        let key = AccountKey(provider: .claude, configHome: "/h", isDefault: true)
        let until = Date().addingTimeInterval(600)
        let other = UsageCacheStore(url: url)
        other.save(StoredAccount(fingerprint: "same", reading: nil, cooldownUntil: until), for: key)
        let reading = StoredReading(plan: nil, email: nil, windows: [], fetchedAt: Date())
        UsageCacheStore(url: url).save(StoredAccount(fingerprint: "same", reading: reading, cooldownUntil: nil), for: key)
        #expect(other.entry(for: key)?.cooldownUntil == until)
        #expect(other.entry(for: key)?.reading == reading)
        // A different login does not inherit it.
        UsageCacheStore(url: url).save(StoredAccount(fingerprint: "new", reading: reading, cooldownUntil: nil), for: key)
        #expect(other.entry(for: key)?.cooldownUntil == nil)
    }

    @Test func anOlderIncomingCooldownDoesNotShortenTheStoredOne() {
        let url = scratch.url.appending(path: "app-usage.json")
        let key = AccountKey(provider: .claude, configHome: "/h", isDefault: true)
        let later = Date().addingTimeInterval(600)
        let earlier = Date().addingTimeInterval(60)
        let other = UsageCacheStore(url: url)
        other.save(StoredAccount(fingerprint: "same", reading: nil, cooldownUntil: later), for: key)
        UsageCacheStore(url: url).save(
            StoredAccount(fingerprint: "same", reading: nil, cooldownUntil: earlier), for: key)
        #expect(other.entry(for: key)?.cooldownUntil == later)
    }

    @Test func theCooldownCheckUsesTheServiceClock() {
        let url = scratch.url.appending(path: "app-usage.json")
        let key = AccountKey(provider: .claude, configHome: "/h", isDefault: true)
        // Long past by the wall clock, still ahead by the injected one.
        let then = Date(timeIntervalSince1970: 1_000_000)
        let until = then.addingTimeInterval(600)
        let store = UsageCacheStore(url: url, now: { then })
        store.save(StoredAccount(fingerprint: "same", reading: nil, cooldownUntil: until), for: key)
        let reading = StoredReading(plan: nil, email: nil, windows: [], fetchedAt: then)
        store.save(StoredAccount(fingerprint: "same", reading: reading, cooldownUntil: nil), for: key)
        #expect(store.entry(for: key)?.cooldownUntil == until)
    }
}
