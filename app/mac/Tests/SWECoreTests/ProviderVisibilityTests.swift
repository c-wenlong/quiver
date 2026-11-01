import Foundation
import Testing
@testable import SWECore

/// Holds the first check until released, so a second one can finish first.
actor Gate {
    struct Failed: Error, LocalizedError { var errorDescription: String? { "old check failed" } }
    var calls = 0
    private let hold = HoldGate()
    func next() async throws -> Set<String> {
        calls += 1
        if calls == 1 {
            try? await hold.pass()
            throw Failed()
        }
        return ["claude"]
    }
    /// True once the first check is parked, false after `gateTimeout`.
    nonisolated func parked() async -> Bool { await hold.arrived() }
    func release() { hold.open() }
}

/// Installed providers the first time, a failure after that.
actor Flaky {
    struct Failed: Error {}
    var calls = 0
    func next() throws -> Set<String> {
        calls += 1
        if calls > 1 { throw Failed() }
        return ["claude"]
    }
}

/// Counts calls.
actor CancelsFirst {
    private(set) var calls = 0
    func next() throws -> Set<String> {
        calls += 1
        if calls == 1 { throw CancellationError() }
        return ["claude"]
    }
}

actor Counter {
    var count = 0
    func tick() { count += 1 }
}

/// A failure the first time, installed providers after that.
actor FailsFirst {
    struct Failed: Error, LocalizedError { var errorDescription: String? { "swe didn't answer" } }
    var calls = 0
    func next() throws -> Set<String> {
        calls += 1
        if calls == 1 { throw Failed() }
        return ["claude"]
    }
}

/// A swe whose answer depends on the path in use; nil fails.
actor FakeSwe {
    struct Failed: Error, LocalizedError { var errorDescription: String? { "swe failed" } }
    var results: [String: Set<String>?]
    var path = ""
    var calls = 0
    init(results: [String: Set<String>?]) { self.results = results }
    func use(_ path: String) { self.path = path }
    func set(_ path: String, _ found: Set<String>?) { results[path] = found }
    func detect() throws -> Set<String> {
        calls += 1
        guard let found = results[path] ?? nil else { throw Failed() }
        return found
    }
}

/// Answers at once the first time, then holds the second check until
/// released, with a harness added.
actor HeldCheck {
    var calls = 0
    private let hold = HoldGate()
    func next() async -> Set<String> {
        calls += 1
        if calls == 1 { return ["claude"] }
        try? await hold.pass()
        return ["claude", "codex"]
    }
    /// True once the second check is parked, false after `gateTimeout`.
    nonisolated func parked() async -> Bool { await hold.arrived() }
    func release() { hold.open() }
}

/// An in-memory stand-in for UserDefaults.
final class MemoryStore: KeyValueStoring, @unchecked Sendable {
    var values: [String: [String]] = [:]
    func strings(forKey key: String) -> [String]? { values[key] }
    func set(_ strings: [String], forKey key: String) { values[key] = strings }
}

@Suite struct ProviderCatalogTests {
    @Test func tokenTrackersProvidersAndSWEsHarnessesAreListed() {
        let ids = ProviderCatalog.all.map(\.id)
        // TokenTracker's order first.
        #expect(Array(ids.prefix(5)) == ["claude", "codex", "devin", "cursor", "antigravity"])
        for id in ["kimi", "zcode", "commandcode", "qoder", "qoder-cn", "gemini",
                   "qwen-code", "goose", "droid"] {
            #expect(ids.contains(id), "\(id)")
        }
        #expect(Set(ids).count == ids.count)
        // Model plans are subscriptions, not harnesses.
        #expect(ProviderCatalog.provider("ark-coding-plan") == nil)
        #expect(ProviderCatalog.provider("ark-agent-plan") == nil)
        #expect(ProviderCatalog.provider("copilot")?.name == "GitHub Copilot")
    }

    @Test func everyProviderHasALogoOrAnInitial() {
        let withLogo = ProviderCatalog.all.filter { HarnessIcons.icon(for: $0.icon) != nil }.map(\.id)
        #expect(Set(ProviderCatalog.all.map(\.id)).subtracting(withLogo).isEmpty)
    }
}

@Suite struct ProviderDetectionTests {
    let home = URL(fileURLWithPath: "/Users/k")

    @Test func harnessesComeFromSweAndTheRestFromTheirOwnSigns() {
        let found = ProviderDetection.installed(
            sweHarnesses: ["claude", "codex", "devin"],
            commandExists: { $0 == "kimi" },
            pathExists: { ["/Users/k/.zcode", "/Applications/Qoder.app"].contains($0) },
            home: home)
        #expect(found == ["claude", "codex", "devin", "kimi", "zcode", "qoder", "qoder-cn"])
    }

    @Test func aListedButUninstalledHarnessDoesNotCount() async throws {
        let runner = FakeRunner(.init(status: 0, stdout: Data(#"{"schema": 1, "data": \#(harnessesJSON)}"#.utf8), stderr: ""))
        let harnesses = try await SweClient(runner: runner).harnesses(withUsage: false)
        // opencode is listed with installed: false; a missing flag (older swe) counts as installed.
        #expect(ProviderDetection.installedHarnesses(harnesses) == ["codex", "claude", "goose"])
    }

    @Test func nothingFoundWhenNothingIsThere() {
        let found = ProviderDetection.installed(sweHarnesses: [], commandExists: { _ in false },
                                                pathExists: { _ in false }, home: home)
        #expect(found.isEmpty)
    }
}

@MainActor
@Suite struct ProviderVisibilityTests {
    func model(installed: Set<String>, store: MemoryStore = MemoryStore()) async -> ProviderVisibilityModel {
        let model = ProviderVisibilityModel(store: store, detect: { installed })
        await model.load()
        return model
    }

    @Test func installedProvidersStartEnabledAndTheRestCannotBe() async {
        let model = await model(installed: ["claude", "codex", "devin"])
        #expect(model.enabledCount == 3)
        #expect(model.rows.count == ProviderCatalog.all.count)
        let cursor = model.rows.first { $0.id == "cursor" }
        #expect(cursor?.isInstalled == false)
        #expect(cursor?.isEnabled == false)
        model.setEnabled("cursor", true)
        #expect(model.isVisible("cursor") == false)
    }

    @Test func togglesAndBulkActionsPersist() async {
        let store = MemoryStore()
        let model = await model(installed: ["claude", "codex", "devin"], store: store)
        model.setEnabled("codex", false)
        #expect(model.enabledCount == 2)
        #expect(model.isVisible("codex") == false)

        let reopened = await self.model(installed: ["claude", "codex", "devin"], store: store)
        #expect(reopened.isVisible("codex") == false)

        reopened.disableAll()
        #expect(reopened.enabledCount == 0)
        reopened.enableAll()
        #expect(reopened.enabledCount == 3)
        #expect(reopened.isVisible("cursor") == false)
    }

    @Test func nothingIsCalledMissingBeforeTheFirstCheck() async {
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { ["claude"] })
        #expect(model.hasChecked == false)
        await model.load()
        #expect(model.hasChecked)
    }

    @Test func usageViewsHideOnlyWhatWasSwitchedOff() async {
        let model = await model(installed: ["claude"])
        #expect(model.isHidden("opencode") == false)
        model.setEnabled("claude", false)
        #expect(model.isHidden("claude"))
    }

    @Test func aProviderInstalledLaterTurnsOnByItself() async {
        let store = MemoryStore()
        _ = await model(installed: ["claude"], store: store)
        let later = await model(installed: ["claude", "cursor"], store: store)
        #expect(later.isVisible("cursor"))
    }

    @Test func dragMovesARowAndTheOrderIsKept() async {
        let store = MemoryStore()
        let model = await model(installed: ["claude"], store: store)
        model.move("gemini", before: "claude")
        #expect(model.rows.first?.id == "gemini")
        #expect(model.rows[1].id == "claude")
        let reopened = await self.model(installed: ["claude"], store: store)
        #expect(reopened.rows.first?.id == "gemini")
        // A provider added to the catalog later still shows up, at the end.
        store.values["providerOrder"] = ["gemini", "claude"]
        let partial = await self.model(installed: ["claude"], store: store)
        #expect(partial.rows.count == ProviderCatalog.all.count)
        #expect(Array(partial.rows.prefix(2).map(\.id)) == ["gemini", "claude"])
    }

    @Test func aFailedCheckIsReportedNotTakenAsNothingInstalled() async {
        struct Broken: Error, LocalizedError { var errorDescription: String? { "swe is too old" } }
        let failing = ProviderVisibilityModel(store: MemoryStore(), detect: { throw Broken() })
        await failing.load()
        #expect(failing.checkError == "swe is too old")
        #expect(failing.hasChecked == false)

        // A later check that works clears it; one that fails keeps what was known.
        let store = MemoryStore()
        let flaky = Flaky()
        let model = ProviderVisibilityModel(store: store, detect: { try await flaky.next() })
        await model.load()
        #expect(model.isVisible("claude"))
        await model.load()
        #expect(model.checkError != nil)
        #expect(model.isVisible("claude"))
        #expect(model.hasChecked)
    }

    @Test func anOlderCheckFinishingLateChangesNothing() async {
        let gate = Gate()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await gate.next() })
        let old = Task { await model.load() }
        #expect(await gate.parked())
        await model.load()
        #expect(model.isVisible("claude"))
        #expect(model.isLoading == false)   // the newest check is done
        await gate.release()
        await old.value
        #expect(model.checkError == nil)
        #expect(model.isLoading == false)
        #expect(model.isVisible("claude"))
    }

    @Test func aCancelledCheckDoesNotBlockTheNextRecheck() async {
        let cancelsFirst = CancelsFirst()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await cancelsFirst.next() })
        await model.loadIfNeeded(for: "/usr/local/bin/swe")
        #expect(!model.isLoading)
        await model.recheck(for: "/usr/local/bin/swe")
        #expect(await cancelsFirst.calls == 2)
        #expect(model.isVisible("claude"))
    }

    @Test func checksOncePerSwePath() async {
        let counter = Counter()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { await counter.tick(); return ["claude"] })
        await model.loadIfNeeded(for: "/usr/local/bin/swe")
        await model.loadIfNeeded(for: "/usr/local/bin/swe")   // the card built again
        #expect(await counter.count == 1)
        #expect(model.isVisible("claude"))
        await model.loadIfNeeded(for: "/opt/homebrew/bin/swe")   // another swe chosen
        #expect(await counter.count == 2)
        await model.loadIfNeeded(for: "/opt/homebrew/bin/swe")
        #expect(await counter.count == 2)
    }

    @Test func aFailedOrCancelledCheckRunsAgainNextTime() async {
        let flaky = FailsFirst()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await flaky.next() })
        await model.loadIfNeeded(for: "swe")
        #expect(model.checkError != nil)
        await model.loadIfNeeded(for: "swe")
        #expect(model.checkError == nil)
        #expect(model.isVisible("claude"))
        #expect(await flaky.calls == 2)

        let calls = Counter()
        let retried = ProviderVisibilityModel(store: MemoryStore(), detect: {
            await calls.tick()
            if await calls.count == 1 { throw CancellationError() }
            return ["claude"]
        })
        await retried.loadIfNeeded(for: "swe")   // the view went away mid-check
        #expect(retried.hasChecked == false)
        await retried.loadIfNeeded(for: "swe")   // the rebuilt one checks again
        #expect(await calls.count == 2)
        #expect(retried.hasChecked)
    }

    @Test func switchingBackAfterAFailureChecksAgainUnderTheRightPath() async {
        // A works, B fails, back to A, Try again, then B again (now working).
        let swe = FakeSwe(results: ["A": ["claude"], "B": nil])
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await swe.detect() })
        await swe.use("A"); await model.loadIfNeeded(for: "A")
        #expect(model.isVisible("claude"))
        await swe.use("B"); await model.loadIfNeeded(for: "B")
        #expect(model.checkError != nil)
        await swe.use("A"); await model.loadIfNeeded(for: "A")
        #expect(model.checkError == nil)   // B's error isn't left on A
        await model.load()                 // Try again, on A
        await swe.set("B", ["codex"])
        let before = await swe.calls
        await swe.use("B"); await model.loadIfNeeded(for: "B")
        #expect(await swe.calls == before + 1)   // B wasn't taken as checked
        #expect(model.isVisible("codex"))
        #expect(!model.isVisible("claude"))
    }

    @Test func goingBackWhileAnotherPathIsCheckingChecksAgain() async {
        let gate = Gate()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await gate.next() })
        // A check of B that parks, then a return to A before it finishes.
        let pending = Task { await model.loadIfNeeded(for: "B") }
        #expect(await gate.parked())
        await model.loadIfNeeded(for: "A")
        #expect(await gate.calls == 2)
        #expect(model.isVisible("claude"))
        await gate.release()
        await pending.value
        #expect(model.checkError == nil)   // B's late result is dropped
        await model.loadIfNeeded(for: "A")
        #expect(await gate.calls == 2)
    }

    @Test func aHarnessInstalledLaterShowsUpOnTheQuietRecheck() async {
        let swe = FakeSwe(results: ["A": ["claude"]])
        let clock = TestClock()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await swe.detect() },
                                            now: { clock.now })
        await model.recheck(for: "A")   // nothing checked yet: no swe call of its own
        #expect(await swe.calls == 0)
        await swe.use("A"); await model.loadIfNeeded(for: "A")
        #expect(model.rows.first { $0.id == "codex" }?.isInstalled == false)
        await swe.set("A", ["claude", "codex"])   // installed in a terminal
        await model.loadIfNeeded(for: "A")           // the cache still holds
        #expect(await swe.calls == 1)
        clock.advance(ProviderVisibilityModel.recheckInterval)
        await model.recheck(for: "A")                        // SWE is active again
        #expect(await swe.calls == 2)
        #expect(model.isVisible("codex"))
        await model.loadIfNeeded(for: "A")
        #expect(await swe.calls == 2)
    }

    @Test func aRecheckAfterTheSwePathChangedIsSavedUnderTheNewPath() async {
        // A checked, B chosen in Settings > Paths, then SWE active again.
        let swe = FakeSwe(results: ["A": ["claude"], "B": ["codex"]])
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await swe.detect() })
        await swe.use("A"); await model.loadIfNeeded(for: "A")
        await swe.use("B"); await model.recheck(for: "B")
        #expect(model.isVisible("codex"))
        #expect(await swe.calls == 2)
        await model.loadIfNeeded(for: "B")   // B's result is B's
        #expect(await swe.calls == 2)
        await swe.use("A"); await model.loadIfNeeded(for: "A")   // A isn't taken as checked
        #expect(await swe.calls == 3)
        #expect(model.isVisible("claude"))
        #expect(!model.isVisible("codex"))

        // A failing new path is reported, not hidden as a quiet recheck.
        await swe.set("C", nil)
        await swe.use("C"); await model.recheck(for: "C")
        #expect(model.checkError != nil)
    }

    @Test func aQuietRecheckKeepsTheRowsAndHidesItsOwnFailure() async {
        let swe = FakeSwe(results: ["A": ["claude"]])
        let clock = TestClock()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await swe.detect() },
                                            now: { clock.now })
        await swe.use("A"); await model.loadIfNeeded(for: "A")
        await swe.set("A", nil)
        clock.advance(ProviderVisibilityModel.recheckInterval)
        await model.recheck(for: "A")
        #expect(model.checkError == nil)
        #expect(model.hasChecked)
        #expect(model.isVisible("claude"))
        await model.loadIfNeeded(for: "A")   // still counts as checked
        #expect(await swe.calls == 2)
    }

    @Test func aRecheckWithinTheIntervalSkipsSwe() async {
        let swe = FakeSwe(results: ["A": ["claude"]])
        let clock = TestClock()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { try await swe.detect() },
                                            now: { clock.now })
        await swe.use("A"); await model.loadIfNeeded(for: "A")
        await model.recheck(for: "A")   // just checked: nothing new to say
        #expect(await swe.calls == 1)
        #expect(model.isVisible("claude"))
        clock.advance(ProviderVisibilityModel.recheckInterval - 1)
        await model.recheck(for: "A")
        #expect(await swe.calls == 1)
        clock.advance(1)
        await model.recheck(for: "A")
        #expect(await swe.calls == 2)

        // A different swe checks right away; the interval only throttles
        // the quiet same-swe case.
        await swe.use("B"); await swe.set("B", ["codex"])
        await model.recheck(for: "B")
        #expect(await swe.calls == 3)
        #expect(model.isVisible("codex"))

        // A failed check is throttled too, then tried again once the
        // interval passes.
        clock.advance(ProviderVisibilityModel.recheckInterval)
        await swe.set("B", nil)
        await model.recheck(for: "B")
        #expect(await swe.calls == 4)
        await model.recheck(for: "B")
        #expect(await swe.calls == 4)
        clock.advance(ProviderVisibilityModel.recheckInterval)
        await model.recheck(for: "B")
        #expect(await swe.calls == 5)
    }

    @Test func noCheckingStateWhileARecheckRuns() async {
        let gate = HeldCheck()
        let clock = TestClock()
        let model = ProviderVisibilityModel(store: MemoryStore(), detect: { await gate.next() },
                                            now: { clock.now })
        await model.loadIfNeeded(for: "A")
        clock.advance(ProviderVisibilityModel.recheckInterval)
        let recheck = Task { await model.recheck(for: "A") }
        #expect(await gate.parked())
        #expect(model.hasChecked)   // rows keep their result, no "Checking…"
        #expect(model.isVisible("claude"))
        await gate.release()
        await recheck.value
        #expect(model.isVisible("codex"))
    }

    @Test func aRowCanBeDraggedToTheEnd() async {
        let model = await model(installed: ["claude"])
        model.moveToEnd("claude")
        #expect(model.rows.last?.id == "claude")
    }

    @Test func orderingFollowsTheUsersOrder() async {
        let model = await model(installed: ["claude", "codex"])
        model.move("codex", before: "claude")
        #expect(model.ordered(["claude", "codex", "devin"]) == ["codex", "claude", "devin"])
    }
}
