import Foundation
import Synchronization
import Testing
@testable import SWECore

/// Fake process tables; nothing real is sampled or signalled except in the
/// sampler smoke test.
@Suite struct ComputeTests {
    static let home = "/Users/test"
    static let now = Date(timeIntervalSince1970: 1_800_000_000)

    /// A fake process. `path` doubles as the name source. Orphan roots
    /// need a real cwd: "/" or none reads as a launchd service.
    static func proc(_ pid: Int32, _ ppid: Int32, path: String, args: [String] = [],
                     cwd: String? = nil, mem: UInt64 = 1 << 20, cpu: UInt64 = 0,
                     threads: Int = 1, started: Date = now) -> ProcessSample {
        ProcessSample(pid: pid, ppid: ppid, path: path, arguments: args, cwd: cwd,
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
        let npm = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "studio"],
                            cwd: "/Users/test/proj")
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

    @Test func versionedPythonStillCountsAsARunner() {
        // "python3.12" orphaned under pid 1 is an orphan tree root.
        let py = Self.proc(10, 1, path: "/usr/bin/python3.12", args: ["python3.12", "/x/serve.py"],
                           cwd: "/Users/test/proj")
        let a = ProcessAttribution.attribute([py], matchers: [])
        #expect(a.orphans.map(\.id) == [10])

        // The framework's "Python" running the harness script is a root.
        let framework = Self.proc(11, 1,
                                  path: "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python",
                                  args: ["Python", "/opt/homebrew/bin/claude"])
        let b = ProcessAttribution.attribute([framework], matchers: [Self.claude])
        #expect(b.byHarness["claude"] == [framework])
    }

    @Test func launchdServicesAreNotOrphans() {
        // A user LaunchAgent also reports ppid 1 under a runner, but its
        // working directory is "/" (or unreadable), or launchd lists it.
        let daemon = Self.proc(10, 1, path: "/usr/bin/node", args: ["node", "/x/agent.js"],
                               cwd: "/")
        let noCwd = Self.proc(11, 1, path: "/usr/bin/node", args: ["node", "/x/other.js"])
        let server = Self.proc(12, 1, path: "/usr/bin/node", args: ["node", "/x/serve.js"],
                               cwd: "/Users/test/proj")
        let a = ProcessAttribution.attribute([daemon, noCwd, server], matchers: [])
        #expect(a.orphans.map(\.id) == [12])
        let b = ProcessAttribution.attribute([server], matchers: [], managedPIDs: [12])
        #expect(b.orphans.isEmpty)
    }

    // MARK: Main process

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

    // MARK: Main process

    @Test func theBundleExecutableIsMainButNestedHelpersAreNot() {
        let main = Self.proc(10, 1, path: "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT")
        let renderer = Self.proc(11, 10,
                                 path: "/Applications/ChatGPT.app/Contents/Frameworks/Codex (Renderer).app/Contents/MacOS/Codex (Renderer)")
        // Both attribute to codex, but only the bundle executable is main.
        #expect(ProcessAttribution.isRoot(main, of: Self.codex))
        #expect(ProcessAttribution.isMainProcess(main, of: Self.codex))
        #expect(ProcessAttribution.isRoot(renderer, of: Self.codex))
        #expect(!ProcessAttribution.isMainProcess(renderer, of: Self.codex))
        // Home-folder processes are helpers, never main.
        let homeProc = Self.proc(12, 1, path: Self.home + "/.codex/bin/x")
        #expect(ProcessAttribution.isRoot(homeProc, of: Self.codex))
        #expect(!ProcessAttribution.isMainProcess(homeProc, of: Self.codex))
    }

    @Test func anOverMemoryHelperAppIsPreselectedAndQuitsWithItsChildren() {
        let over = UInt64(3) << 30
        let renderer = Self.proc(10, 1,
                                 path: "/Applications/ChatGPT.app/Contents/Frameworks/Codex (Renderer).app/Contents/MacOS/Codex (Renderer)",
                                 mem: over)
        let child = Self.proc(11, 10, path: "/x/worker")
        let flags: [Int32: Set<ProcessFlag>] = [10: [.overMemory]]
        let items = ComputeSummary.cleanup(byHarness: ["codex": [renderer]], orphans: [],
                                           orphanFlags: [:], processFlags: flags,
                                           matchers: [Self.codex], limits: ComputeLimits(),
                                           now: Self.now)
        #expect(items.count == 1)
        #expect(items[0].preselected)
        #expect(!items[0].isMain)
        let order = ComputeSummary.killOrder(for: items[0], in: [renderer, child],
                                             selfPID: 99, ancestors: [])
        #expect(order == [11, 10])
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

    @Test func aYoungSmallOrphanIsListedButNotPreselected() {
        let tree = OrphanTree(root: Self.proc(10, 1, path: "/opt/homebrew/bin/npm", started: Self.youngStart),
                              descendants: [])
        let flags: [Int32: Set<OrphanFlag>] = [10: ComputeSummary.flags(for: tree, limits: ComputeLimits(), now: Self.now)]
        #expect(flags[10] == [])
        let items = ComputeSummary.cleanup(byHarness: [:], orphans: [tree], orphanFlags: flags,
                                           processFlags: [:], matchers: [],
                                           limits: ComputeLimits(), now: Self.now)
        #expect(items.count == 1)
        #expect(items[0].preselected == false)
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
        // The "tree" is SWE's own ancestry: it must refuse to signal any of it.
        let launchd = Self.proc(1, 0, path: "/sbin/launchd")
        let quiver = Self.proc(99, 1, path: "/Applications/SWE.app/Contents/MacOS/SWE")
        let helper = Self.proc(10, 99, path: "/x/helper")
        let item = CleanupItem.process(quiver, harness: "claude")
        let order = ComputeSummary.killOrder(for: item, in: [launchd, quiver, helper],
                                             selfPID: 99, ancestors: [1])
        #expect(order == [10])
    }

    @Test func aHarnessRootQuitsAlone() {
        // The app or CLI shuts its own helpers down; only the root is signalled.
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let child = Self.proc(11, 10, path: "/x/helper")
        let grandchild = Self.proc(12, 11, path: "/x/worker")
        let item = CleanupItem(kind: .process(root, harness: "claude"), reason: "",
                               preselected: false, isMain: true)
        let order = ComputeSummary.killOrder(for: item, in: [root, child, grandchild],
                                             selfPID: 99, ancestors: [])
        #expect(order == [10])
    }

    @Test func aReusedRootPidIsNotSignalled() {
        // The process the page showed has exited; pid 10 is somebody else now.
        let shown = Self.proc(10, 1, path: "/x/helper", started: Self.youngStart)
        let live = Self.proc(10, 1, path: "/x/other")
        let order = ComputeSummary.killOrder(for: .process(shown, harness: "claude"),
                                             in: [live], selfPID: 99, ancestors: [])
        #expect(order == [])
    }

    @Test func aReusedTreeMemberIsDroppedButTheRestStand() {
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", started: Self.oldStart)
        let child = Self.proc(11, 10, path: "/x/child", started: Self.oldStart)
        let stale = Self.proc(12, 11, path: "/x/stale", started: Self.oldStart)
        let tree = OrphanTree(root: root, descendants: [child, stale])
        // pid 12 still exists, but as a different process owned elsewhere:
        // its startedAt differs and it does not hang under the root.
        let live = [root, child, Self.proc(12, 1, path: "/x/reused")]
        let order = ComputeSummary.killOrder(for: .orphanTree(tree), in: live,
                                             selfPID: 99, ancestors: [])
        #expect(order == [11, 10])
    }

    @Test func aWorkerSpawnedAfterTheRowWasBuiltStillGetsSignalled() {
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", started: Self.oldStart)
        let recorded = Self.proc(11, 10, path: "/x/worker", started: Self.oldStart)
        let tree = OrphanTree(root: root, descendants: [recorded])
        // pid 12 hangs under the root in the current sample only.
        let live = [root, recorded, Self.proc(12, 10, path: "/x/new", started: Self.oldStart)]
        let order = ComputeSummary.killOrder(for: .orphanTree(tree), in: live,
                                             selfPID: 99, ancestors: [])
        #expect(Set(order.dropLast()) == [11, 12])
        #expect(order.last == 10)
    }

    @Test func aSurvivingChildIsSignalledWhenTheRootIsGone() {
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", started: Self.oldStart)
        let child = Self.proc(11, 10, path: "/x/worker", started: Self.oldStart)
        let tree = OrphanTree(root: root, descendants: [child])
        // The root exited; the child was reparented to launchd but is the
        // same process it was (same pid, same startedAt).
        let live = [Self.proc(11, 1, path: "/x/worker", started: Self.oldStart)]
        let order = ComputeSummary.killOrder(for: .orphanTree(tree), in: live,
                                             selfPID: 99, ancestors: [])
        #expect(order == [11])
    }

    // MARK: Limits persistence

    @Test func limitsRoundTripThroughDefaults() {
        let defaults = TestDefaults("compute-test")
        var limits = ComputeLimits(defaults: defaults)
        #expect(limits.orphanAge == 24 * 3600)
        #expect(limits.memoryBytes == UInt64(2) << 30)
        #expect(!limits.autoClean && !limits.autoCleanForce)
        limits.orphanAge = 72 * 3600
        limits.memoryBytes = UInt64(4) << 30
        limits.autoClean = true
        limits.autoCleanForce = true
        limits.save(to: defaults)
        #expect(ComputeLimits(defaults: defaults) == limits)
    }

    // MARK: Auto clean

    /// A mutable table plus a signalled log, as the other model tests use.
    private final class Fake: ProcessSampling, ProcessSignaller, @unchecked Sendable {
        var table: [ProcessSample]
        var signalled: [(Int32, Int32)] = []
        init(_ table: [ProcessSample]) { self.table = table }
        func sample() -> [ProcessSample] { table }
        func signal(_ pid: Int32, _ sig: Int32) -> Bool { signalled.append((pid, sig)); return true }
    }

    /// Answers each sample with the next table, then keeps answering with
    /// the last, so a test can change the process table between calls.
    private final class Flip: ProcessSampling, ProcessSignaller, @unchecked Sendable {
        let tables: [[ProcessSample]]
        private var calls = 0
        private let lock = NSLock()
        var signalled: [(Int32, Int32)] = []
        init(_ tables: [[ProcessSample]]) { self.tables = tables }
        func sample() -> [ProcessSample] {
            lock.withLock {
                calls += 1
                return tables[min(calls, tables.count) - 1]
            }
        }
        func signal(_ pid: Int32, _ sig: Int32) -> Bool { signalled.append((pid, sig)); return true }
    }

    /// A mutable `now` for the model's clock, the trend tests' shape.
    private final class Clock: @unchecked Sendable {
        var now: Date
        init(_ now: Date) { self.now = now }
    }

    @MainActor
    private func autoCleanModel(_ fake: Fake,
                                clock: @escaping @Sendable () -> Date = { .now },
                                defaults: UserDefaults? = nil) async -> ComputeModel {
        let model = ComputeModel(sampler: fake, matchers: { [] },
                                 signaller: fake,
                                 clock: clock,
                                 defaults: defaults ?? TestDefaults("compute-test"),
                                 selfPID: 99)
        await model.loadMatchers()
        return model
    }

    @MainActor @Test func autoCleanSignalsAnOldOrphanOnce() async {
        let started = Date.now.addingTimeInterval(-30 * 3600)
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj", started: started),
            Self.proc(11, 10, path: "/opt/homebrew/bin/node", started: started),
        ])
        let model = await autoCleanModel(fake)
        model.limits.autoClean = true
        await model.refresh()
        // Deepest first, then the root — the same plan a manual Quit uses.
        #expect(fake.signalled.map(\.0) == [11, 10])
        #expect(Set(fake.signalled.map(\.1)) == [SIGTERM])
        // A second pass signals nothing: the tree is marked already.
        await model.refresh()
        #expect(fake.signalled.count == 2)
    }

    @MainActor @Test func autoCleanOffLeavesOrphansAlone() async {
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj",
                      started: Date.now.addingTimeInterval(-30 * 3600)),
        ])
        let model = await autoCleanModel(fake)
        await model.refresh()
        #expect(fake.signalled.isEmpty)
    }

    @MainActor @Test func autoCleanLeavesAYoungOrphanAlone() async {
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj",
                      started: Date.now.addingTimeInterval(-3600)),
        ])
        let model = await autoCleanModel(fake)
        model.limits.autoClean = true
        await model.refresh()
        #expect(fake.signalled.isEmpty)
    }

    @MainActor @Test func autoCleanForceEscalatesAfterTheGrace() async {
        let clock = Clock(.now)
        let started = clock.now.addingTimeInterval(-30 * 3600)
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj", started: started),
            Self.proc(11, 10, path: "/opt/homebrew/bin/node", started: started),
        ])
        let model = await autoCleanModel(fake, clock: { clock.now })
        model.limits.autoClean = true
        model.limits.autoCleanForce = true
        await model.refresh()
        #expect(Set(fake.signalled.map(\.1)) == [SIGTERM])
        // The tree ignores the quit signal; the grace passes, then SIGKILL.
        clock.now = clock.now.addingTimeInterval(ComputeModel.autoCleanGrace + 1)
        await model.refresh()
        #expect(fake.signalled.map(\.1) == [SIGTERM, SIGTERM, SIGKILL, SIGKILL])
        #expect(fake.signalled.map(\.0) == [11, 10, 11, 10])
        // Forced once: a stubborn process is not killed over and over.
        clock.now = clock.now.addingTimeInterval(60)
        await model.refresh()
        #expect(fake.signalled.count == 4)
    }

    @MainActor @Test func autoCleanWithoutForceNeverEscalates() async {
        let clock = Clock(.now)
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj",
                      started: clock.now.addingTimeInterval(-30 * 3600)),
        ])
        let model = await autoCleanModel(fake, clock: { clock.now })
        model.limits.autoClean = true
        await model.refresh()
        clock.now = clock.now.addingTimeInterval(60)
        await model.refresh()
        #expect(fake.signalled.map(\.1) == [SIGTERM])
    }

    @MainActor @Test func autoCleanForceLeavesADeadTreeAndAReusedPidAlone() async {
        let clock = Clock(.now)
        let started = clock.now.addingTimeInterval(-30 * 3600)
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj", started: started),
        ])
        let model = await autoCleanModel(fake, clock: { clock.now })
        model.limits.autoClean = true
        model.limits.autoCleanForce = true
        await model.refresh()
        #expect(fake.signalled.map(\.0) == [10])
        // The tree exited and the OS recycled pid 10 for a brand-new
        // process: the recorded mark does not carry over to it, and its
        // own age is young, so nothing more is signalled.
        fake.table = [Self.proc(10, 1, path: "/opt/homebrew/bin/node",
                                args: ["node", "server.js"], cwd: "/Users/test/new",
                                started: clock.now.addingTimeInterval(-60))]
        clock.now = clock.now.addingTimeInterval(ComputeModel.autoCleanGrace + 1)
        await model.refresh()
        #expect(fake.signalled.count == 1)
    }

    @MainActor @Test func autoCleanForceStillKillsASurvivingChild() async {
        let clock = Clock(.now)
        let started = clock.now.addingTimeInterval(-30 * 3600)
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"],
                      cwd: "/Users/test/proj", started: started),
            Self.proc(11, 10, path: "/opt/homebrew/bin/node", started: started),
        ])
        let model = await autoCleanModel(fake, clock: { clock.now })
        model.limits.autoClean = true
        model.limits.autoCleanForce = true
        await model.refresh()
        // The root died on SIGTERM; the child reparented to launchd and
        // is now its own orphan root — but it still sits inside the
        // recorded tree's mark, so no second SIGTERM goes out and the
        // mark's grace escalation reaches it instead.
        fake.table = [Self.proc(11, 1, path: "/opt/homebrew/bin/node",
                                cwd: "/Users/test/proj", started: started)]
        clock.now = clock.now.addingTimeInterval(ComputeModel.autoCleanGrace + 1)
        await model.refresh()
        #expect(fake.signalled.map(\.1) == [SIGTERM, SIGTERM, SIGKILL])
    }

    // MARK: The model

    @MainActor @Test func theModelSortsRowsAndListsCleanup() async {
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/claude", mem: 100),
            Self.proc(11, 10, path: "/x/helper", mem: UInt64(3) << 30),
            // oldStart is anchored to Self.now, but the model compares
            // against the real clock, so this one needs real age.
            Self.proc(20, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "studio"],
                      cwd: "/Users/test/proj", started: .now.addingTimeInterval(-30 * 3600)),
        ])
        let defaults = TestDefaults("compute-test")
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

    @MainActor @Test func aSlowerEarlierRefreshCannotOverwriteANewerOne() async {
        // The first sample blocks until the test releases it; the second
        // returns at once. The parked one must not publish over the new.
        final class Gated: ProcessSampling, @unchecked Sendable {
            let tables: [[ProcessSample]]
            let entered = DispatchSemaphore(value: 0)
            let release = DispatchSemaphore(value: 0)
            private var calls = 0
            private let lock = NSLock()
            init(_ tables: [[ProcessSample]]) { self.tables = tables }
            func sample() -> [ProcessSample] {
                lock.lock()
                calls += 1
                let n = calls
                lock.unlock()
                if n == 1 { entered.signal(); waitForRelease() }
                return tables[min(n, tables.count) - 1]
            }
            /// Sync wrappers, callable from the async test.
            func waitForEntry() { entered.wait() }
            func waitForRelease() { release.wait() }
        }
        let first = [Self.proc(10, 1, path: "/opt/homebrew/bin/claude")]
        let second = [Self.proc(11, 1, path: "/opt/homebrew/bin/claude")]
        let gated = Gated([first, second])
        let model = ComputeModel(sampler: gated, matchers: { [Self.claude] },
                                 signaller: SystemSignaller(), defaults: TestDefaults("compute-test"), selfPID: 99)
        await model.loadMatchers()
        let slow = Task { await model.refresh() }
        // Wait off the main actor and off the cooperative pool, so the
        // refresh can reach its sample call.
        await offMain { gated.waitForEntry() }
        await model.refresh()
        gated.release.signal()
        await slow.value
        #expect(model.processes["claude"]?.map(\.id) == [11])
    }

    @MainActor @Test func aQuitPlansOnItsOwnSampleNotThePublishedTable() async {
        // Table A is what the page showed; when quit() samples, pid 10 has
        // been reused by a different process, so nothing may be signalled.
        let shown = Self.proc(10, 1, path: "/x/worker", cwd: "/Users/test/proj")
        let reused = Self.proc(10, 1, path: "/x/reused", started: Self.youngStart)
        let sampler = Flip([[shown], [reused]])
        let model = ComputeModel(sampler: sampler, matchers: { [Self.claude] },
                                 signaller: sampler, defaults: TestDefaults("compute-test"), selfPID: 99)
        await model.loadMatchers()
        await model.refresh()
        await model.quit(.process(shown, harness: "claude"))
        #expect(sampler.signalled.isEmpty)
    }

    @MainActor @Test func aRecycledPidIsNotMarkedStillRunning() async {
        // SIGTERM went to the process that was pid 10. The pid is then
        // recycled onto a different process, which was never signalled
        // and must not inherit the still-running mark.
        let signalled = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.youngStart)
        let recycled = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.now)
        let kept = Self.proc(11, 1, path: "/opt/homebrew/bin/claude", started: Self.youngStart)
        // refresh sees the originals; quit samples them again and
        // signals; the refresh quit ends with shows pid 10 recycled.
        let fake = Flip([[signalled, kept], [signalled, kept], [recycled, kept]])
        // Every read of the clock moves it ten seconds, so by the
        // sample after the signal the five-second mark has passed: a
        // matching process reports still running, the recycled pid does
        // not. The mark is stamped by the same clock it is read against.
        let ticks = Mutex(0)
        let model = ComputeModel(sampler: fake, matchers: { [Self.claude] }, signaller: fake,
                                 clock: {
                                     Self.now.addingTimeInterval(
                                         Double(ticks.withLock { $0 += 1; return $0 }) * 10)
                                 },
                                 defaults: TestDefaults("compute-test"), selfPID: 99)
        await model.loadMatchers()
        await model.refresh()
        await model.quit([.process(signalled, harness: "claude"),
                          .process(kept, harness: "claude")])
        #expect(fake.signalled.map(\.0) == [10, 11])
        #expect(model.processes["claude"]?.first { $0.sample.pid == 10 }?.stillRunning == false)
        #expect(model.processes["claude"]?.first { $0.sample.pid == 11 }?.stillRunning == true)
    }

    @MainActor @Test func quitHarnessSignalsTheWholeHarnessHelpersFirst() async {
        let fake = Fake([
            Self.proc(10, 1, path: "/opt/homebrew/bin/claude"),
            Self.proc(11, 10, path: "/x/helper"),
            Self.proc(12, 11, path: "/x/worker"),
            // claude spawned codex: it is codex's card's business, and
            // claude's quit must not reach across the attribution.
            Self.proc(20, 11, path: "/usr/local/bin/codex"),
            Self.proc(21, 20, path: "/x/codex-helper"),
        ])
        let model = ComputeModel(sampler: fake, matchers: { [Self.claude, Self.codex] },
                                 signaller: fake, defaults: TestDefaults("compute-test"), selfPID: 99)
        await model.loadMatchers()
        await model.refresh()
        await model.quitHarness("claude")
        // Helpers go before the entry process; codex's tree is not claude's.
        #expect(fake.signalled.map(\.0) == [12, 11, 10])
        #expect(Set(fake.signalled.map(\.1)) == [SIGTERM])
    }

    @MainActor @Test func quitHarnessCatchesAProcessSpawnedAfterTheRefresh() async {
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let helper = Self.proc(11, 10, path: "/x/helper")
        // The page's last sample predates the helper; quit's own has it.
        let fake = Flip([[root], [root, helper]])
        let model = ComputeModel(sampler: fake, matchers: { [Self.claude] },
                                 signaller: fake, defaults: TestDefaults("compute-test"), selfPID: 99)
        await model.loadMatchers()
        await model.refresh()
        await model.quitHarness("claude")
        #expect(fake.signalled.map(\.0) == [11, 10])
    }

    @MainActor @Test func quitHarnessForceKillsAndAnIdleHarnessSignalsNothing() async {
        let fake = Fake([Self.proc(10, 1, path: "/opt/homebrew/bin/claude")])
        let model = ComputeModel(sampler: fake, matchers: { [Self.claude, Self.codex] },
                                 signaller: fake, defaults: TestDefaults("compute-test"), selfPID: 99)
        await model.loadMatchers()
        await model.quitHarness("codex")
        #expect(fake.signalled.isEmpty)
        await model.quitHarness("claude", force: true)
        #expect(fake.signalled.map(\.0) == [10])
        #expect(fake.signalled.map(\.1) == [SIGKILL])
    }

    // MARK: Earlier-launch leftovers

    static let appMain = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"
    static let appHelper = "/Applications/ChatGPT.app/Contents/Helpers/Crashpad"

    private static func cleanup(_ byHarness: [String: [ProcessSample]],
                                orphans: [OrphanTree] = [],
                                orphanFlags: [Int32: Set<OrphanFlag>] = [:],
                                processFlags: [Int32: Set<ProcessFlag>] = [:],
                                matchers: [HarnessMatcher],
                                managedPIDs: Set<Int32> = []) -> [CleanupItem] {
        ComputeSummary.cleanup(byHarness: byHarness, orphans: orphans,
                               orphanFlags: orphanFlags, processFlags: processFlags,
                               matchers: matchers, limits: ComputeLimits(),
                               now: Self.now, managedPIDs: managedPIDs)
    }

    @Test func aBundleHelperFromAnEarlierLaunchIsPreselected() {
        let main = Self.proc(10, 1, path: Self.appMain, started: Self.youngStart)
        let helper = Self.proc(11, 1, path: Self.appHelper,
                               started: Self.youngStart.addingTimeInterval(-4 * 86400))
        let items = Self.cleanup(["codex": [main, helper]], matchers: [Self.codex])
        #expect(items.count == 1)
        #expect(items[0].preselected)
        #expect(items[0].reason == "Left over from an earlier Codex launch")
        #expect(items[0].harness == "codex")
    }

    @Test func aBundleHelperStartedWithThisLaunchIsNoCandidate() {
        let main = Self.proc(10, 1, path: Self.appMain, started: Self.youngStart)
        let helper = Self.proc(11, 1, path: Self.appHelper,
                               started: Self.youngStart.addingTimeInterval(-30))
        #expect(Self.cleanup(["codex": [main, helper]], matchers: [Self.codex]).isEmpty)
    }

    @Test func aManagedBundleHelperIsNoCandidate() {
        let main = Self.proc(10, 1, path: Self.appMain, started: Self.youngStart)
        let helper = Self.proc(11, 1, path: Self.appHelper,
                               started: Self.youngStart.addingTimeInterval(-4 * 86400))
        #expect(Self.cleanup(["codex": [main, helper]], matchers: [Self.codex],
                             managedPIDs: [11]).isEmpty)
    }

    @Test func theAppMainProcessIsNeverAnEarlierLaunchCandidate() {
        let oldMain = Self.proc(10, 1, path: Self.appMain,
                                started: Self.youngStart.addingTimeInterval(-4 * 86400))
        let main = Self.proc(11, 1, path: Self.appMain, started: Self.youngStart)
        #expect(Self.cleanup(["codex": [oldMain, main]], matchers: [Self.codex]).isEmpty)
    }

    @Test func aBundleHelperAfterTheAppQuitIsPreselected() {
        let helper = Self.proc(11, 1, path: Self.appHelper, started: Self.oldStart)
        let items = Self.cleanup(["codex": [helper]], matchers: [Self.codex])
        #expect(items.count == 1)
        #expect(items[0].preselected)
        #expect(items[0].reason == "Left over after Codex quit")
    }

    @Test func aProcessOutsideTheAppBundleIsNoEarlierLaunchCandidate() {
        let main = Self.proc(10, 1, path: Self.appMain, started: Self.youngStart)
        let stray = Self.proc(11, 1, path: "/usr/bin/codex",
                              started: Self.youngStart.addingTimeInterval(-4 * 86400))
        #expect(Self.cleanup(["codex": [main, stray]], matchers: [Self.codex]).isEmpty)
    }

    // MARK: Duplicate servers

    private static func dupCopies(_ pids: [Int32], parent: Int32 = 10,
                                  path: String = "/x/mcp-server",
                                  args: [String] = ["mcp-server", "--stdio"],
                                  cwd: String? = "/Users/test/proj",
                                  started: Date = oldStart) -> [ProcessSample] {
        pids.enumerated().map { i, pid in
            Self.proc(pid, parent, path: path, args: args, cwd: cwd,
                      started: started.addingTimeInterval(TimeInterval(i) * 60))
        }
    }

    @Test func theOldestDuplicatesArePreselectedAndTheNewestIsKept() {
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        let copies = Self.dupCopies([11, 12, 13])
        let items = Self.cleanup(["claude": [parent] + copies], matchers: [Self.claude])
        // pid 13 started last of the three, so it stays.
        #expect(items.map(\.id).sorted() == ["process-11", "process-12"])
        #expect(items.allSatisfy { $0.preselected })
        #expect(items.allSatisfy { $0.reason.contains("Duplicate of a newer copy") })
    }

    @Test func youngDuplicateCopiesAreNoCandidates() {
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let copies = Self.dupCopies([11, 12], started: Self.youngStart)
        #expect(Self.cleanup(["claude": [parent] + copies], matchers: [Self.claude]).isEmpty)
    }

    @Test func copiesInDifferentWorkingDirectoriesAreNoCandidates() {
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        let copies = [Self.proc(11, 10, path: "/x/mcp-server", args: ["mcp-server"],
                                cwd: "/a", started: Self.oldStart),
                      Self.proc(12, 10, path: "/x/mcp-server", args: ["mcp-server"],
                                cwd: "/b", started: Self.oldStart)]
        #expect(Self.cleanup(["claude": [parent] + copies], matchers: [Self.claude]).isEmpty)
    }

    @Test func copiesWithNoWorkingDirectoryAreNoCandidates() {
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        let copies = Self.dupCopies([11, 12], cwd: nil)
        #expect(Self.cleanup(["claude": [parent] + copies], matchers: [Self.claude]).isEmpty)
    }

    @Test func shellCopiesAreNoCandidates() {
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        let copies = Self.dupCopies([11, 12], path: "/bin/zsh", args: ["zsh"])
        #expect(Self.cleanup(["claude": [parent] + copies], matchers: [Self.claude]).isEmpty)
    }

    @Test func chromiumHelpersAreNoCandidates() {
        let parent = Self.proc(10, 1, path: Self.appMain, started: Self.oldStart)
        let copies = Self.dupCopies([11, 12], parent: 10, path: Self.appHelper,
                                    args: ["Crashpad", "--type=renderer"])
        #expect(Self.cleanup(["codex": [parent] + copies], matchers: [Self.codex]).isEmpty)
    }

    @Test func aBusyDuplicateIsNotSuggestedAsADuplicate() {
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        let copies = Self.dupCopies([11, 12])
        let items = Self.cleanup(["claude": [parent] + copies], processFlags: [11: [.busy]],
                                 matchers: [Self.claude])
        // 12 is kept as the newest; 11 is busy, so no duplicate suggestion.
        let item11 = items.first { $0.id == "process-11" }
        #expect(items.allSatisfy { !$0.reason.contains("Duplicate") })
        #expect(item11 == nil || item11?.preselected == false)
    }

    // MARK: Merging and grouping

    @Test func aDuplicateOverMemoryMergesIntoOneItemWithBothReasons() {
        let over = UInt64(3) << 30
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        var copies = Self.dupCopies([11, 12])
        copies[0] = Self.proc(11, 10, path: "/x/mcp-server", args: ["mcp-server", "--stdio"],
                              cwd: "/Users/test/proj", mem: over, started: Self.oldStart)
        let items = Self.cleanup(["claude": [parent] + copies], processFlags: [11: [.overMemory]],
                                 matchers: [Self.claude])
        let item = items.first { $0.id == "process-11" }
        #expect(item != nil)
        #expect(item?.reason.contains("Duplicate of a newer copy") == true)
        #expect(item?.reason.contains("Using") == true)
        #expect(item?.reason.contains("; ") == true)
        #expect(item?.preselected == true)
    }

    @Test func aChildOfACandidateIsNotListedSeparately() {
        // 11 is an old duplicate; 13 is its over-memory child: quitting 11's
        // tree already takes 13, so the child is not its own row.
        let over = UInt64(3) << 30
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude", started: Self.oldStart)
        let copies = Self.dupCopies([11, 12])
        let child = Self.proc(13, 11, path: "/x/worker", mem: over)
        let items = Self.cleanup(["claude": [parent] + copies + [child]], processFlags: [13: [.overMemory]],
                                 matchers: [Self.claude])
        #expect(items.map(\.id) == ["process-11"])
    }

    @Test func anUnflaggedOrphanIsListedButNotPreselected() {
        let tree = OrphanTree(root: Self.proc(20, 1, path: "/opt/homebrew/bin/npm",
                                              started: Self.youngStart),
                              descendants: [])
        let items = Self.cleanup([:], orphans: [tree], matchers: [])
        #expect(items.count == 1)
        #expect(items[0].preselected == false)
        #expect(items[0].reason.hasPrefix("Orphaned, running for "))
        #expect(items[0].harness == nil)
    }

    @Test func groupsFollowMatcherOrderWithOrphansFirst() {
        let codexHelper = Self.proc(30, 1, path: Self.appHelper,
                                    started: Self.oldStart)
        let claudeHelper = Self.proc(20, 10, path: "/x/helper", mem: UInt64(3) << 30)
        let claudeParent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let orphan = OrphanTree(root: Self.proc(40, 1, path: "/opt/homebrew/bin/npm",
                                                started: Self.youngStart),
                                descendants: [])
        // claude is listed before codex in the table; matchers order codex first.
        let items = Self.cleanup(["claude": [claudeParent, claudeHelper],
                                  "codex": [codexHelper]],
                                 orphans: [orphan],
                                 processFlags: [20: [.overMemory]],
                                 matchers: [Self.codex, Self.claude])
        #expect(items.map(\.harness) == [nil, "codex", "claude"])
    }

    @Test func anUntickedAncestorDoesNotCoverAPreselectedChild() {
        // 11 is busy only (unticked); its over-memory child must still be
        // listed and ticked: quitting 11 was never suggested.
        let over = UInt64(3) << 30
        let parent = Self.proc(10, 1, path: "/opt/homebrew/bin/claude")
        let helper = Self.proc(11, 10, path: "/x/helper")
        let child = Self.proc(12, 11, path: "/x/worker", mem: over)
        let items = Self.cleanup(["claude": [parent, helper, child]],
                                 processFlags: [11: [.busy], 12: [.overMemory]],
                                 matchers: [Self.claude])
        let helperItem = items.first { $0.id == "process-11" }
        let childItem = items.first { $0.id == "process-12" }
        #expect(helperItem?.preselected == false)
        #expect(childItem?.preselected == true)
    }

    // MARK: Path resolution

    @Test func aDeletedBinaryFallsBackToAnAbsoluteArgv0() {
        #expect(ProcessSampler.resolvedPath(native: "",
                                            arguments: ["/Applications/X.app/Contents/MacOS/x"])
                == "/Applications/X.app/Contents/MacOS/x")
    }

    @Test func aRelativeArgv0IsNoPath() {
        #expect(ProcessSampler.resolvedPath(native: "", arguments: ["npm", "run"]) == "")
    }

    @Test func noArgumentsIsNoPath() {
        #expect(ProcessSampler.resolvedPath(native: "", arguments: []) == "")
    }

    @Test func aNativePathBeatsArgv0() {
        #expect(ProcessSampler.resolvedPath(native: "/real/bin",
                                            arguments: ["/other/bin"]) == "/real/bin")
    }

    // MARK: Batch quit

    @MainActor @Test func aBatchQuitSamplesOnceAndSignalsEveryPidOnce() async {
        final class Counting: ProcessSampling, ProcessSignaller, @unchecked Sendable {
            var table: [ProcessSample]
            var calls = 0
            var signalled: [Int32] = []
            init(_ table: [ProcessSample]) { self.table = table }
            func sample() -> [ProcessSample] { calls += 1; return table }
            func signal(_ pid: Int32, _ sig: Int32) -> Bool { signalled.append(pid); return true }
        }
        let root = Self.proc(10, 1, path: "/opt/homebrew/bin/npm", args: ["npm", "run", "x"])
        let sharedChild = Self.proc(11, 10, path: "/opt/homebrew/bin/node")
        let other = Self.proc(12, 1, path: "/x/worker")
        let fake = Counting([root, sharedChild, other])
        let defaults = TestDefaults("compute-test")
        let model = await ComputeModel(sampler: fake, matchers: { [Self.claude] },
                                       signaller: fake, defaults: defaults, selfPID: 99)
        await model.loadMatchers()
        await model.refresh()
        let callsBefore = fake.calls
        let tree = OrphanTree(root: root, descendants: [sharedChild])
        // The two items share pid 11 between their kill plans; it is
        // signalled once.
        await model.quit([.orphanTree(tree), .process(sharedChild, harness: "claude")])
        // One fresh sample for the kill plans, one for the refresh after.
        #expect(fake.calls - callsBefore == 2)
        #expect(fake.signalled.sorted() == [10, 11])
    }

    // MARK: Orphan age field

    @Test func splitPicksTheLargestUnitThatDividesEvenly() {
        #expect(OrphanAgeUnit.split(90 * 60) == (90, .minute))
        #expect(OrphanAgeUnit.split(120 * 60) == (2, .hour))
        #expect(OrphanAgeUnit.split(2 * 86400) == (2, .day))
        #expect(OrphanAgeUnit.split(86400 + 3600) == (25, .hour))
    }

    @Test func resolveSavesWhatWasTypedInTheChosenUnit() {
        // "60" saved as minutes, then the unit switched to Hour: 60 hours.
        #expect(OrphanAgeUnit.resolve(value: 60, unit: .minute) == 3600)
        #expect(OrphanAgeUnit.resolve(value: 60, unit: .hour) == TimeInterval(60 * 3600))
        #expect(OrphanAgeUnit.resolve(value: 3, unit: .day) == TimeInterval(3 * 86400))
    }

    @Test func resolveRejectsZeroAndClampsTheTop() {
        #expect(OrphanAgeUnit.resolve(value: 0, unit: .hour) == nil)
        #expect(OrphanAgeUnit.resolve(value: -5, unit: .minute) == nil)
        #expect(OrphanAgeUnit.resolve(value: 400, unit: .day) == ComputeLimits.orphanAgeRange.upperBound)
    }

    @Test func theLegacyHoursKeyStillLoads() {
        let defaults = TestDefaults("compute-test")
        defaults.set(6, forKey: ComputeLimits.legacyOrphanAgeKey)
        #expect(ComputeLimits(defaults: defaults).orphanAge == 6 * 3600)
    }

    @Test func theMinutesKeyWinsOverTheLegacyHoursKey() {
        let defaults = TestDefaults("compute-test")
        defaults.set(90, forKey: ComputeLimits.orphanAgeKey)
        defaults.set(6, forKey: ComputeLimits.legacyOrphanAgeKey)
        #expect(ComputeLimits(defaults: defaults).orphanAge == 5400)
    }

    @Test func savingRemovesTheLegacyHoursKey() {
        let defaults = TestDefaults("compute-test")
        defaults.set(6, forKey: ComputeLimits.legacyOrphanAgeKey)
        ComputeLimits(defaults: defaults).save(to: defaults)
        #expect(defaults.object(forKey: ComputeLimits.legacyOrphanAgeKey) == nil)
        #expect(defaults.integer(forKey: ComputeLimits.orphanAgeKey) == 360)
    }

    @Test func theOrphanAgeClampsToAMinuteAndAYear() {
        let defaults = TestDefaults("compute-test")
        var limits = ComputeLimits()
        limits.orphanAge = 5
        limits.save(to: defaults)
        #expect(defaults.integer(forKey: ComputeLimits.orphanAgeKey) == 1)
        limits.orphanAge = 800 * 86400
        limits.save(to: defaults)
        #expect(defaults.integer(forKey: ComputeLimits.orphanAgeKey) == 365 * 24 * 60)
    }

    // MARK: Figures

    @Test func noMemoryReadsAsZeroKB() {
        #expect(ComputeSummary.bytes(0) == "0 KB")
        #expect(ComputeSummary.bytes(5 << 20) != "0 KB")
    }

    /// The figures follow the Mac's locale; only the words are pinned.
    @Test func oneThreadIsSingular() {
        #expect(ComputeSummary.threads(1) == "1 thread")
        #expect(ComputeSummary.threads(28) == "28 threads")
    }

    @Test func theTotalsLineCountsProcessesInWords() {
        let zero = ComputeSummary.percent(0)
        #expect(ComputeSummary.totalsLine(.init()) == "0 KB across 0 processes · CPU \(zero)% · 0 threads")
        let one = ComputeModel.Totals(memoryBytes: 5 << 20, cpuPercent: 12.4, threads: 9, processCount: 1)
        #expect(ComputeSummary.totalsLine(one)
                == "\(ComputeSummary.bytes(5 << 20)) across 1 process · CPU \(ComputeSummary.percent(12.4))% · 9 threads")
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
