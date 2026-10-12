import Foundation
import Testing
@testable import QuiverCore

/// Fake process tables; nothing real is sampled or signalled except in the
/// sampler smoke test.
@Suite struct ComputeTests {
    static let home = "/Users/test"
    static let now = Date(timeIntervalSince1970: 1_800_000_000)

    /// A fake process. `path` doubles as the name source.
    static func proc(_ pid: Int32, _ ppid: Int32, path: String, args: [String] = [],
                     mem: UInt64 = 1 << 20, cpu: UInt64 = 0, threads: Int = 1,
                     started: Date = now) -> ProcessSample {
        ProcessSample(pid: pid, ppid: ppid, path: path, arguments: args,
                      startedAt: started, threads: threads,
                      memoryBytes: mem, cpuTimeNanos: cpu)
    }

    static let claude = HarnessMatcher(harness: "claude", commands: ["claude"],
                                     homeFolder: home + "/.claude")
    static let codex = HarnessMatcher(harness: "codex", appPath: "/Applications/ChatGPT.app",
                                      commands: ["codex"], homeFolder: home + "/.codex")

    // MARK: Attribution

    @Test func anAppBundleHelperGoesToItsHarness() {
        let helper = Self.proc(10, 2, path: "/Applications/ChatGPT.app/Contents/Helpers/ChatGPT Helper")
        let a = ProcessAttribution.attribute([helper], matchers: [Self.codex])
        #expect(a.byHarness["codex"] == [helper])
        #expect(a.orphans.isEmpty)
    }

    @Test func aCommandNamedProcessGoesToItsHarness() {
        let cli = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let a = ProcessAttribution.attribute([cli], matchers: [Self.claude])
        #expect(a.byHarness["claude"] == [cli])
    }

    @Test func aScriptRunnerRunningTheHarnessScriptCounts() {
        let cli = Self.proc(10, 1, path: "/opt/homebrew/bin/node",
                            args: ["node", "/opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/claude"])
        let a = ProcessAttribution.attribute([cli], matchers: [Self.claude])
        #expect(a.byHarness["claude"] == [cli])
    }

    @Test func aProcessInTheHarnessHomeCounts() {
        let p = Self.proc(10, 1, path: Self.home + "/.codex/bin/codex-linux-arm64")
        let a = ProcessAttribution.attribute([p], matchers: [Self.codex])
        #expect(a.byHarness["codex"] == [p])
    }

    @Test func childrenAndGrandchildrenFollowTheRoot() {
        let cli = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let mcp = Self.proc(11, 10, path: "/usr/bin/python3", args: ["python3", "/x/mcp.py"])
        let grandchild = Self.proc(12, 11, path: "/usr/bin/python3", args: ["python3", "/x/worker.py"])
        let a = ProcessAttribution.attribute([grandchild, mcp, cli], matchers: [Self.claude])
        #expect(a.byHarness["claude"]?.count == 3)
        #expect(a.orphans.isEmpty)
    }

    @Test func anUnstarredHarnessProcessIsDropped() {
        // No matcher for cursor: its process shows nowhere.
        let cursor = Self.proc(10, 1, path: "/usr/local/bin/cursor-agent")
        let a = ProcessAttribution.attribute([cursor], matchers: [Self.claude])
        #expect(a.byHarness.isEmpty)
        #expect(a.orphans.isEmpty)
    }

    @Test func aNonRunnerOrphanIsDropped() {
        let db = Self.proc(10, 1, path: "/opt/homebrew/bin/postgres")
        let a = ProcessAttribution.attribute([db], matchers: [Self.claude])
        #expect(a.orphans.isEmpty)
    }

    @Test func aRunnerOrphanAndItsChildrenFormOneTree() {
        let npm = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "studio"])
        let node1 = Self.proc(11, 10, path: "/opt/homebrew/bin/node", args: ["node", "/x/studio.js"])
        let node2 = Self.proc(12, 11, path: "/opt/homebrew/bin/node", args: ["node", "/x/worker.js"])
        let a = ProcessAttribution.attribute([npm, node1, node2], matchers: [Self.claude])
        #expect(a.orphans.count == 1)
        #expect(a.orphans[0].root == npm)
        #expect(Set(a.orphans[0].descendants.map(\.pid)) == [11, 12])
    }

    @Test func theNearerRootWins() {
        // claude spawned codex: the codex process is codex's, not claude's.
        let cli = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let inner = Self.proc(11, 10, path: "/usr/local/bin/codex")
        let helper = Self.proc(12, 11, path: "/usr/bin/python3", args: ["python3", "/x/mcp.py"])
        let a = ProcessAttribution.attribute([cli, inner, helper], matchers: [Self.claude, Self.codex])
        #expect(a.byHarness["claude"] == [cli])
        #expect(a.byHarness["codex"]?.count == 2)
    }

    // MARK: Usage totals

    @Test func usageTotalsSumMemoryThreadsAndCPU() {
        let t0 = Self.now
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", mem: 100, threads: 3,
                             started: t0.addingTimeInterval(-3600))
        let helper = Self.proc(11, 10, path: "/x/helper", mem: 50, threads: 2,
                               started: t0.addingTimeInterval(-60))
        let usages = ComputeSummary.usages(byHarness: ["claude": [root, helper]],
                                           cpu: [10: 40, 11: 20], order: ["claude"])
        #expect(usages == [HarnessUsage(harness: "claude", processCount: 2, threads: 5,
                                      memoryBytes: 150, cpuPercent: 60,
                                      runningSince: t0.addingTimeInterval(-3600))])
    }

    // MARK: CPU meter

    @Test func theFirstSampleHasNoCPU() {
        var meter = CPUMeter()
        let cpu = meter.update([Self.proc(10, 1, path: "/x", cpu: 100)], at: Self.now)
        #expect(cpu.isEmpty)
    }

    @Test func cpuIsTheDeltaOverTheWallDelta() {
        var meter = CPUMeter()
        let t0 = Self.now
        _ = meter.update([Self.proc(10, 1, path: "/x", cpu: 0)], at: t0)
        // 2s of CPU over 4s of wall: 50%.
        let cpu = meter.update([Self.proc(10, 1, path: "/x", cpu: 2_000_000_000)],
                               at: t0.addingTimeInterval(4))
        #expect(cpu[10] == 50)
    }

    @Test func aReusedPidStartsOver() {
        var meter = CPUMeter()
        let t0 = Self.now
        _ = meter.update([Self.proc(10, 1, path: "/x", cpu: 0)], at: t0)
        let restarted = t0.addingTimeInterval(10)
        let cpu = meter.update([Self.proc(10, 1, path: "/x", cpu: 5_000_000_000, started: restarted)],
                               at: t0.addingTimeInterval(4))
        #expect(cpu[10] == nil)
    }

    @Test func busyNeedsA120SecondStreakAtEighty() {
        var meter = CPUMeter()
        var t = Self.now
        var cpuTime: UInt64 = 0
        _ = meter.update([Self.proc(10, 1, path: "/x", cpu: cpuTime)], at: t)
        // 30s at 100%: busy run is too short.
        t += 30; cpuTime += 30_000_000_000
        _ = meter.update([Self.proc(10, 1, path: "/x", cpu: cpuTime)], at: t)
        #expect(!meter.isBusy(10))
        // 60s idle: the run resets.
        t += 60
        _ = meter.update([Self.proc(10, 1, path: "/x", cpu: cpuTime)], at: t)
        // 130s at 100%: the streak covers the span.
        t += 130; cpuTime += 130_000_000_000
        _ = meter.update([Self.proc(10, 1, path: "/x", cpu: cpuTime)], at: t)
        #expect(meter.isBusy(10))
    }

    // MARK: Flags and cleanup

    static let oldStart = now.addingTimeInterval(-30 * 3600)
    static let youngStart = now.addingTimeInterval(-3600)

    @Test func anOldOrphanIsPreselectedForCleanup() {
        let tree = OrphanTree(root: Self.proc(10, 1, path: "/opt/homebrew/bin/npm",
                                              args: ["npm", "run", "studio"], started: Self.oldStart),
                              descendants: [])
        let flags: [Int32: Set<OrphanFlag>] = [10: ComputeSummary.flags(for: tree, limits: ComputeLimits(), now: Self.now)]
        let items = ComputeSummary.cleanup(byHarness: [:], orphans: [tree], orphanFlags: flags,
                                           processFlags: [:], matchers: [],
                                           limits: ComputeLimits(), now: Self.now)
        #expect(items.count == 1)
        #expect(items[0].preselected)
    }

    @Test func aYoungSmallOrphanIsNoCandidate() {
        let tree = OrphanTree(root: Self.proc(10, 1, path: "/opt/homebrew/bin/npm", started: Self.youngStart),
                              descendants: [])
        let flags: [Int32: Set<OrphanFlag>] = [10: ComputeSummary.flags(for: tree, limits: ComputeLimits(), now: Self.now)]
        #expect(flags[10] == [])
        let items = ComputeSummary.cleanup(byHarness: [:], orphans: [tree], orphanFlags: flags,
                                           processFlags: [:], matchers: [],
                                           limits: ComputeLimits(), now: Self.now)
        #expect(items.isEmpty)
    }

    @Test func anOverMemoryHelperIsPreselectedButNotTheRoot() {
        let over = UInt64(3) << 30
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", mem: over)
        let helper = Self.proc(11, 10, path: "/x/helper", mem: over)
        let flags: [Int32: Set<ProcessFlag>] = [10: [.overMemory], 11: [.overMemory]]
        let items = ComputeSummary.cleanup(byHarness: ["claude": [root, helper]], orphans: [],
                                           orphanFlags: [:], processFlags: flags,
                                           matchers: [Self.claude],
                                           limits: ComputeLimits(), now: Self.now)
        #expect(items.count == 2)
        let rootItem = items.first { $0.id == "process-10" }
        let helperItem = items.first { $0.id == "process-11" }
        #expect(rootItem?.preselected == false)
        #expect(rootItem?.reason.contains("itself") == true)
        #expect(helperItem?.preselected == true)
    }

    @Test func aBusyProcessIsListedButNotPreselected() {
        let busy = Self.proc(11, 10, path: "/x/helper")
        let flags: [Int32: Set<ProcessFlag>] = [11: [.busy]]
        let items = ComputeSummary.cleanup(byHarness: ["claude": [busy]], orphans: [],
                                           orphanFlags: [:], processFlags: flags,
                                           matchers: [Self.claude],
                                           limits: ComputeLimits(), now: Self.now)
        #expect(items.count == 1)
        #expect(items[0].preselected == false)
    }

    // MARK: Kill order

    @Test func killOrderIsDeepestDescendantsFirstRootLast() {
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"])
        let child = Self.proc(11, 10, path: "/opt/homebrew/bin/node")
        let grandchild = Self.proc(12, 11, path: "/opt/homebrew/bin/node")
        let samples = [root, child, grandchild]
        let tree = OrphanTree(root: root, descendants: [child, grandchild])
        let order = ComputeSummary.killOrder(for: .orphanTree(tree), in: samples,
                                             selfPID: 99, ancestors: [])
        #expect(order == [12, 11, 10])
    }

    @Test func killOrderNeverIncludesPidOneSelfOrAncestors() {
        // The "tree" is Quiver's own ancestry: it must refuse to signal any of it.
        let launchd = Self.proc(1, 0, path: "/sbin/launchd")
        let quiver = Self.proc(99, 1, path: "/Applications/Quiver.app/Contents/MacOS/Quiver")
        let helper = Self.proc(10, 99, path: "/x/helper")
        let item = CleanupItem.process(quiver, harness: "claude")
        let order = ComputeSummary.killOrder(for: item, in: [launchd, quiver, helper],
                                             selfPID: 99, ancestors: [1])
        #expect(order == [10])
    }

    // MARK: Limits persistence

    @Test func limitsRoundTripThroughDefaults() {
        let suite = "compute-test-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        var limits = ComputeLimits(defaults: defaults)
        #expect(limits.orphanAge == 24 * 3600)
        #expect(limits.memoryBytes == UInt64(2) << 30)
        limits.orphanAge = 72 * 3600
        limits.memoryBytes = UInt64(4) << 30
        limits.save(to: defaults)
        #expect(ComputeLimits(defaults: defaults) == limits)
    }

    // MARK: The model

    @MainActor @Test func theModelSortsRowsAndListsCleanup() async {
        final class Fake: ProcessSampling, ProcessSignaller, @unchecked Sendable {
            var table: [ProcessSample]
            var signalled: [(Int32, Int32)] = []
            init(_ table: [ProcessSample]) { self.table = table }
            func sample() -> [ProcessSample] { table }
            func signal(_ pid: Int32, _ sig: Int32) -> Bool { signalled.append((pid, sig)); return true }
        }
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/claude", mem: 100),
            Self.proc(11, 10, path: "/x/helper", mem: UInt64(3) << 30),
            // oldStart is anchored to Self.now, but the model compares
            // against the real clock, so this one needs real age.
            Self.proc(20, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "studio"],
                      started: .now.addingTimeInterval(-30 * 3600)),
        ])
        let suite = "compute-test-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let model = await ComputeModel(sampler: fake, matchers: { [Self.claude] },
                                       signaller: fake, defaults: defaults, selfPID: 99)
        await model.loadMatchers()
        await model.refresh()
        #expect(await model.usages.map(\.harness) == ["claude"])
        #expect(await model.processes["claude"]?.map(\.id) == [11, 10])
        #expect(await model.orphans.map(\.id) == [20])
        #expect(await model.notRunning == [])
        // The over-memory helper is a candidate; so is the old orphan.
        #expect(await Set(model.cleanup.map(\.id)) == ["process-11", "orphan-20"])
        await model.quit(.process(fake.table[1], harness: "claude"))
        #expect(fake.signalled.map(\.0) == [11])
        #expect(fake.signalled.map(\.1) == [SIGTERM])
    }

    // MARK: Sampler smoke test

    @Test func theSamplerSeesThisProcess() {
        let samples = ProcessSampler().sample()
        let me = samples.first { $0.pid == getpid() }
        #expect(me != nil)
        #expect(me?.threads ?? 0 >= 1)
        #expect(me?.memoryBytes ?? 0 > 0)
        #expect(me?.path.isEmpty == false)
        #expect(me?.startedAt ?? .distantFuture < .now)
    }
}
