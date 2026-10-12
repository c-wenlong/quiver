import Darwin
import Foundation
import Observation

/// Sends a signal to a process. The real one calls `kill`; tests use a fake.
public protocol ProcessSignaller: Sendable {
    func signal(_ pid: Int32, _ sig: Int32) -> Bool
}

public struct SystemSignaller: ProcessSignaller {
    public init() {}
    public func signal(_ pid: Int32, _ sig: Int32) -> Bool { kill(pid, sig) == 0 }
}

/// What the Compute page shows: which starred harnesses are running, what
/// their processes cost, which helpers got orphaned, and the suggested
/// clean-up. Sampling runs off the main actor and only while the page
/// asks for it; the matcher list (which harnesses count, and how to tell
/// their processes) reloads only when `loadMatchers()` is called.
@MainActor
@Observable
public final class ComputeModel {
    /// A process in a harness's card: the sample, its CPU%, its flags.
    public struct ProcessRow: Identifiable, Sendable, Equatable {
        public var id: Int32 { sample.pid }
        public var sample: ProcessSample
        public var cpuPercent: Double?
        public var flags: Set<ProcessFlag>
        /// Whether it is the harness's own entry process (the app's
        /// executable or the CLI), not a helper.
        public var isMain: Bool
        /// Still there five seconds after a SIGTERM, so Force quit is offered.
        public var stillRunning: Bool
    }

    /// An orphan tree in the Orphaned card.
    public struct OrphanRow: Identifiable, Sendable, Equatable {
        public var id: Int32 { tree.id }
        public var tree: OrphanTree
        public var flags: Set<OrphanFlag>
        public var stillRunning: Bool
    }

    /// The whole page's numbers for the header line.
    public struct Totals: Sendable, Equatable {
        public var memoryBytes: UInt64 = 0
        public var cpuPercent = 0.0
        public var threads = 0
        public var processCount = 0
    }

    /// One per running starred harness, in the matchers' (swe's) order.
    public private(set) var usages: [HarnessUsage] = []
    /// A harness's processes, most memory first, keyed by harness id.
    public private(set) var processes: [String: [ProcessRow]] = [:]
    public private(set) var orphans: [OrphanRow] = []
    /// Starred harnesses with nothing running, in swe's order.
    public private(set) var notRunning: [String] = []
    public private(set) var cleanup: [CleanupItem] = []
    public private(set) var totals = Totals()
    public private(set) var error: String?

    /// The thresholds the flags and suggestions follow.
    public var limits: ComputeLimits {
        didSet {
            limits.save(to: defaults)
            republish()
        }
    }

    public private(set) var matchers: [HarnessMatcher] = []

    private let sampler: any ProcessSampling
    private let matcherLoader: @Sendable () async throws -> [HarnessMatcher]
    private let managedPIDLoader: @Sendable () async -> Set<Int32>
    private let signaller: any ProcessSignaller
    private let defaults: UserDefaults
    private let selfPID: Int32

    private var meter = CPUMeter()
    private var samples: [ProcessSample] = []
    private var cpu: [Int32: Double] = [:]
    private var attribution = Attribution(byHarness: [:], orphans: [])
    /// Pids launchd manages; refreshed with the matchers and once a minute.
    private var managedPIDs: Set<Int32> = []
    private var managedPIDsLoadedAt: Date?
    /// When each pid was last sent SIGTERM, for the stillRunning mark.
    private var signalledAt: [Int32: Date] = [:]
    /// Tickets refreshes so a slower earlier one can never republish.
    private var request = 0

    public init(sampler: any ProcessSampling,
                matchers: @escaping @Sendable () async throws -> [HarnessMatcher],
                managedPIDs: @escaping @Sendable () async -> Set<Int32> = { [] },
                signaller: any ProcessSignaller = SystemSignaller(),
                defaults: UserDefaults = .standard,
                selfPID: Int32 = getpid()) {
        self.sampler = sampler
        matcherLoader = matchers
        managedPIDLoader = managedPIDs
        self.signaller = signaller
        self.defaults = defaults
        self.selfPID = selfPID
        limits = ComputeLimits(defaults: defaults)
    }

    /// Who to look for. Runs on page appear and on Refresh, not per tick.
    public func loadMatchers() async {
        managedPIDs = await managedPIDLoader()
        managedPIDsLoadedAt = .now
        do {
            matchers = try await matcherLoader()
            if error != nil { error = nil }
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// One sample, off the main actor, then a republish. A refresh that
    /// started earlier but finished later is dropped, so stale samples
    /// never overwrite a newer view.
    public func refresh() async {
        request += 1
        let mine = request
        if managedPIDsLoadedAt.map({ Date.now.timeIntervalSince($0) > 60 }) ?? true {
            managedPIDs = await managedPIDLoader()
            managedPIDsLoadedAt = .now
        }
        let sampler = self.sampler
        let fresh = await runOffPool { sampler.sample() }
        guard mine == request else { return }
        samples = fresh
        let now = Date.now
        cpu = meter.update(samples, at: now)
        attribution = ProcessAttribution.attribute(samples, matchers: matchers,
                                                   managedPIDs: managedPIDs)
        republish(at: now)
    }

    /// Send SIGTERM (SIGKILL when `force`) to an item, deepest child first,
    /// then take a fresh look. The kill plan is built on this call's own
    /// sample, not the shared table: a timer refresh landing meanwhile
    /// would drop this one's publish and leave it signalling a stale view.
    public func quit(_ item: CleanupItem, force: Bool = false) async {
        let sampler = self.sampler
        let fresh = await runOffPool { sampler.sample() }
        let order = ComputeSummary.killOrder(for: item, in: fresh,
                                             selfPID: selfPID, ancestors: selfAncestors(in: fresh))
        for pid in order where signaller.signal(pid, force ? SIGKILL : SIGTERM) {
            if !force { signalledAt[pid] = .now }
        }
        await refresh()
    }

    /// Which of an item's pids are still in the last sample; the clean-up
    /// sheet reads this after a quit to tell "Quit" from "Still running".
    public func alivePids(of item: CleanupItem) -> Set<Int32> {
        let order = ComputeSummary.killOrder(for: item, in: samples,
                                             selfPID: selfPID, ancestors: selfAncestors(in: samples))
        return Set(order.filter { pid in samples.contains { $0.pid == pid } })
    }

    /// The caller's chain of parents in `table`, so a kill plan can never
    /// include them.
    private func selfAncestors(in table: [ProcessSample]) -> Set<Int32> {
        let byPid = Dictionary(table.map { ($0.pid, $0) }, uniquingKeysWith: { first, _ in first })
        var ancestors: Set<Int32> = []
        var node = byPid[selfPID]
        for _ in 0..<64 {
            guard let parent = node.flatMap({ byPid[$0.ppid] }), parent.pid != node?.pid else { break }
            ancestors.insert(parent.pid)
            node = parent
        }
        return ancestors
    }

    /// Republish every list from the last sample, with the limits as they
    /// now stand: this is what makes a changed threshold reflag at once.
    private func republish(at now: Date = .now) {
        let order = matchers.map(\.harness)
        usages = ComputeSummary.usages(byHarness: attribution.byHarness, cpu: cpu, order: order)

        var rows: [String: [ProcessRow]] = [:]
        for (harness, samples) in attribution.byHarness {
            let matcher = matchers.first { $0.harness == harness }
            rows[harness] = samples.map { sample in
                ProcessRow(
                    sample: sample,
                    cpuPercent: cpu[sample.pid],
                    flags: ComputeSummary.flags(for: sample, busy: meter.isBusy(sample.pid), limits: limits),
                    isMain: matcher.map { ProcessAttribution.isMainProcess(sample, of: $0) } ?? false,
                    stillRunning: isStillRunning(sample.pid, at: now))
            }.sorted { $0.sample.memoryBytes > $1.sample.memoryBytes }
        }
        processes = rows
        notRunning = order.filter { (attribution.byHarness[$0] ?? []).isEmpty }

        var orphanFlags: [Int32: Set<OrphanFlag>] = [:]
        orphans = attribution.orphans.map { tree in
            let flags = ComputeSummary.flags(for: tree, limits: limits, now: now)
            orphanFlags[tree.root.pid] = flags
            return OrphanRow(tree: tree, flags: flags,
                             stillRunning: isStillRunning(tree.root.pid, at: now))
        }
        .sorted { $0.tree.memoryBytes > $1.tree.memoryBytes }

        var processFlags: [Int32: Set<ProcessFlag>] = [:]
        for rows in processes.values {
            for row in rows { processFlags[row.sample.pid] = row.flags }
        }
        cleanup = ComputeSummary.cleanup(byHarness: attribution.byHarness,
                                         orphans: attribution.orphans,
                                         orphanFlags: orphanFlags,
                                         processFlags: processFlags,
                                         matchers: matchers, limits: limits, now: now)

        let all = attribution.byHarness.values.flatMap { $0 } + attribution.orphans.flatMap { [$0.root] + $0.descendants }
        totals = Totals(
            memoryBytes: all.reduce(0) { $0 + $1.memoryBytes },
            cpuPercent: all.reduce(0) { $0 + (cpu[$1.pid] ?? 0) },
            threads: all.reduce(0) { $0 + $1.threads },
            processCount: all.count)

        // Dead pids forget they were signalled.
        let alive = Set(samples.map(\.pid))
        signalledAt = signalledAt.filter { alive.contains($0.key) }
    }

    /// A pid counts as still running five seconds after its SIGTERM.
    private func isStillRunning(_ pid: Int32, at now: Date) -> Bool {
        guard let at = signalledAt[pid] else { return false }
        return now.timeIntervalSince(at) >= 5
    }
}
