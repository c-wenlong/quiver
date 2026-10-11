import Foundation
import Testing
@testable import QuiverCore

/// Behaviours pinned after review of the Accounts milestone.
@Suite struct ProfileStoreReviewTests {
    func scratch() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appending(path: "review-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    @Test func aRemovedAccountsIdAndHomeAreNeverReused() throws {
        // Claude Code names its keychain item after the config home, so a reused
        // home would hand the old account's login to the new one.
        let home = try scratch()
        let store = ProfileStore(root: home.appending(path: ".quiver/accounts"), userHome: home)
        let first = try store.add(vendor: .claude, name: "Work")
        try store.remove(id: first.id)
        let second = try store.add(vendor: .claude, name: "Work")
        #expect(second.id == "claude-work-2")
        #expect(store.paths(for: second).home != store.paths(for: first).home)
        // And the retirement survives a fresh store reading the file.
        let fresh = ProfileStore(root: home.appending(path: ".quiver/accounts"), userHome: home)
        try fresh.remove(id: second.id)
        #expect(try fresh.add(vendor: .claude, name: "Work").id == "claude-work-3")
    }

    @Test func aNonDefaultEntryClaimingADefaultIdIsMalformed() throws {
        let home = try scratch()
        let root = home.appending(path: ".quiver/accounts")
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

@Suite struct LaunchPlannerReviewTests {
    let claudeDefault = Profile(id: "claude-default", vendor: .claude, name: "Default", isDefault: true, color: "#d97757")
    let paths = ProfilePaths(home: URL(fileURLWithPath: "/Users/me/.claude"), desktopData: nil)

    @Test func theDefaultAccountClearsAnInheritedHomeOverride() {
        // A CLAUDE_CONFIG_DIR exported by the user's shell would otherwise open another account.
        #expect(LaunchPlanner.shellLine(for: claudeDefault, paths: paths, command: "claude")
            == "unset CLAUDE_CONFIG_DIR; claude")
    }

    @Test func ghosttyAndWezTermRunAnInteractiveLoginShell() {
        // -i reads .zshrc, where many people put the CLI's folder on PATH.
        #expect(LaunchPlanner.terminal(.ghostty, running: "claude").arguments.suffix(3) == ["/bin/zsh", "-ilc", "claude"])
        #expect(LaunchPlanner.terminal(.wezterm, running: "claude").arguments.suffix(3) == ["/bin/zsh", "-ilc", "claude"])
    }
}

@Suite struct ProcessLauncherTests {
    @Test func aLaunchThatFailsReportsWhy() async {
        let launcher = ProcessLauncher()
        await #expect(throws: LaunchError.self) {
            try await launcher.launch(LaunchCommand(executable: "/bin/sh", arguments: ["-c", "echo 'Unable to find application named Claude' >&2; exit 1"]))
        }
        do {
            try await launcher.launch(LaunchCommand(executable: "/bin/sh", arguments: ["-c", "echo 'Unable to find application named Claude' >&2; exit 1"]))
        } catch {
            #expect(error.localizedDescription.contains("Unable to find application"))
        }
    }

    @Test func aLaunchThatSucceedsReturns() async throws {
        try await ProcessLauncher().launch(LaunchCommand(executable: "/usr/bin/true", arguments: []))
    }
}

@MainActor
@Suite struct OtherLimitsFallbackTests {
    @Test func anSweWithoutExcludeIsAskedAgainWithoutIt() async {
        let ok = Data(#"{"schema":1,"data":{"harnesses":[{"name":"devin","command":"devin","description":"","version":null,"aliases":[],"tags":[],"state":"starred","sessions_100d":1,"rate_limit":{"status":"ok","used_percent":5,"remaining_percent":95,"limit_reached":false,"reset_at":null,"window":"1d","window_seconds":null,"plan_type":"teams","remaining_units":null,"total_units":null,"fetched_at":1791700000}}]}}"#.utf8)
        let runner = SequencedRunner([
            (.zero, CommandResult(status: 1, stdout: Data(), stderr: "\u{1B}[31mUnknown flag: --exclude=claude,codex\u{1B}[0m")),
            (.zero, CommandResult(status: 0, stdout: ok, stderr: "")),
        ])
        let model = OtherLimitsModel(swe: SweClient(runner: runner))
        await model.load()
        #expect(model.rows.map(\.name) == ["devin"])
        #expect(model.note == nil)
    }
}

@Suite struct UsageCacheLockTests {
    @Test func concurrentWritersForDifferentAccountsKeepBothCooldowns() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "cache-\(UUID().uuidString)")
        let url = dir.appending(path: "app-usage.json")
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
    }
}

@MainActor
@Suite struct AccountsKeepFreshTests {
    @Test func anOpenPageKeepsReloadingUntilItCloses() async throws {
        let home = FileManager.default.temporaryDirectory.appending(path: "fresh-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        let usage = FakeUsage()
        let model = AccountsModel(store: ProfileStore(root: home.appending(path: "a"), userHome: home),
                                  usage: usage, launcher: FakeLauncher(), terminal: { .terminal }, loginPath: { "" })
        let task = Task { await model.keepFresh(every: .milliseconds(10)) }
        // Wait for three reloads (two default accounts each) rather than
        // counting what fits in a fixed window: CI runners are slow.
        let deadline = Date().addingTimeInterval(10)
        while usage.requests.count < 6 && Date() < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        task.cancel()
        await task.value
        #expect(usage.requests.count >= 6)
        let afterCancel = usage.requests.count
        try await Task.sleep(for: .milliseconds(100))
        #expect(usage.requests.count == afterCancel)
    }
}

@Suite struct CrossProcessTests {
    @Test func twoStoresAddingAtOnceKeepBothAccounts() async throws {
        let home = FileManager.default.temporaryDirectory.appending(path: "multi-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        let root = home.appending(path: ".quiver/accounts")
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
        let url = FileManager.default.temporaryDirectory.appending(path: "cool-\(UUID().uuidString)/app-usage.json")
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

    @Test func theCooldownCheckUsesTheServiceClock() {
        let url = FileManager.default.temporaryDirectory.appending(path: "clock-\(UUID().uuidString)/app-usage.json")
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
