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

    /// The running per-harness history behind the trend card, in memory only.
    public private(set) var history = ComputeHistory()
    /// Whether this Mac has ever answered a GPU read; the GPU tab explains
    /// rather than drawing flat lines when it has not.
    public private(set) var gpuAvailable = false
    /// Whether this Mac's temperature sensors have ever answered.
    public private(set) var temperatureAvailable = false

    /// The Temperature tab's switched-on lines; kept in UserDefaults.
    public var temperatureLines: Set<ThermalComponent> {
        didSet { defaults.set(temperatureLines.sorted().map(\.rawValue), forKey: Self.temperatureLinesKey) }
    }

    public static let temperatureLinesKey = "computeTemperatureLines"

    /// Auto clean's grace: a force-cleaned orphan gets this long to exit
    /// after its SIGTERM before the SIGKILL follows. Ten seconds lets a
    /// dev server flush and close while still catching the stuck ones.
    public static let autoCleanGrace: TimeInterval = 10

    /// Runs with each tick's temperatures; AppModel points it at the fan
    /// threshold, so the rule works whether or not the page is open.
    public var onTemperature: (@MainActor (TemperatureReading) -> Void)?

    /// The thresholds the flags and suggestions follow.
    public var limits: ComputeLimits {
        didSet {
            limits.save(to: defaults)
            republish()
        }
    }

    /// How often the trend sampler ticks; kept in UserDefaults. Changing it
    /// keeps the points already recorded, since x is time.
    public var trendInterval: TrendInterval {
        didSet {
            trendInterval.save(to: defaults)
            // Invalidate every tick still in flight, then wake a parked
            // loop rather than let it sleep out the old interval.
            trendGeneration += 1
            if let task = trendTask {
                task.cancel()
                startTrendLoop()
            }
        }
    }

    public private(set) var matchers: [HarnessMatcher] = []

    private let sampler: any ProcessSampling
    private let gpuSampler: any GPUSampling
    private let temperatureSampler: any TemperatureSampling
    private let matcherLoader: @Sendable () async throws -> [HarnessMatcher]
    private let managedPIDLoader: @Sendable () async -> Set<Int32>
    private let signaller: any ProcessSignaller
    private let defaults: UserDefaults
    private let selfPID: Int32
    /// The wall clock, injectable for tests.
    private let clock: @Sendable () -> Date
    /// The trend loop's sleep, injectable for tests.
    private let trendSleep: @Sendable (Duration) async throws -> Void

    private var meter = CPUMeter()
    private var gpuMeter = GPUMeter()
    private var samples: [ProcessSample] = []
    /// When `samples` was captured, so a trend tick can reuse a fresh page
    /// sample rather than make the Mac answer twice for the same moment.
    private var sampledAt: Date?
    /// Handed to each `takeSample` in start order; `accept` ignores any
    /// sample older than the newest one already accepted.
    private var sampleSeq = 0
    private var lastAcceptedSeq = 0
    private var cpu: [Int32: Double] = [:]
    private var attribution = Attribution(byHarness: [:], orphans: [])
    /// Pids launchd manages; refreshed with the matchers and once a minute.
    private var managedPIDs: Set<Int32> = []
    private var managedPIDsLoadedAt: Date?
    /// When each pid was last sent SIGTERM, for the stillRunning mark —
    /// plus the process's start time, so a pid the OS has handed to a new
    /// process is not mistaken for the one that was signalled.
    private var signalledAt: [Int32: (at: Date, startedAt: Date)] = [:]
    /// Auto clean's own ledger, by orphan root pid: the tree as it was
    /// when SIGTERM went out, when it went out, and whether the grace
    /// escalation to SIGKILL already ran. The recorded start times do the
    /// same work as in `signalledAt`: a pid the OS recycled answers for a
    /// different process and is judged again on its own age.
    private var cleaned: [Int32: (tree: OrphanTree, at: Date, forced: Bool)] = [:]
    /// Tickets refreshes so a slower earlier one can never republish.
    private var request = 0
    private var trendTask: Task<Void, Never>?
    /// Bumped each time a trend loop replaces the last; a tick that
    /// outlives its loop must not write the meters or the history.
    private var trendGeneration = 0
    /// Whether the matcher list has ever loaded, so a trend tick does not
    /// respawn `swe list` every interval when nothing is starred.
    /// The starred list has been read at least once, so an empty one
    /// really means nothing is starred.
    public private(set) var matchersLoaded = false
    /// When a matcher load last failed; a tick may retry once a minute.
    private var matchersFailedAt: Date?

    public init(sampler: any ProcessSampling,
                matchers: @escaping @Sendable () async throws -> [HarnessMatcher],
                managedPIDs: @escaping @Sendable () async -> Set<Int32> = { [] },
                signaller: any ProcessSignaller = SystemSignaller(),
                gpu: any GPUSampling = GPUSampler(),
                temperature: any TemperatureSampling = MacTemperatureSampler(),
                clock: @escaping @Sendable () -> Date = { .now },
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                defaults: UserDefaults = .standard,
                selfPID: Int32 = getpid()) {
        self.sampler = sampler
        gpuSampler = gpu
        temperatureSampler = temperature
        matcherLoader = matchers
        managedPIDLoader = managedPIDs
        self.signaller = signaller
        self.clock = clock
        trendSleep = sleep
        self.defaults = defaults
        self.selfPID = selfPID
        limits = ComputeLimits(defaults: defaults)
        trendInterval = TrendInterval(defaults: defaults)
        temperatureLines = Self.loadTemperatureLines(defaults)
    }

    /// The saved lines, or the default four before the user has picked.
    /// Names this build does not know are skipped.
    static func loadTemperatureLines(_ defaults: UserDefaults) -> Set<ThermalComponent> {
        guard let saved = defaults.stringArray(forKey: temperatureLinesKey) else {
            return Set(ThermalComponent.shownByDefault)
        }
        return Set(saved.compactMap(ThermalComponent.init(rawValue:)))
    }

    /// Switch one Temperature line on or off.
    public func toggleTemperatureLine(_ component: ThermalComponent) {
        if temperatureLines.contains(component) {
            temperatureLines.remove(component)
        } else {
            temperatureLines.insert(component)
        }
    }

    /// Who to look for. Runs on page appear and on Refresh, not per tick.
    public func loadMatchers() async {
        managedPIDs = await managedPIDLoader()
        managedPIDsLoadedAt = .now
        do {
            matchers = try await matcherLoader()
            matchersLoaded = true
            matchersFailedAt = nil
            if error != nil { error = nil }
        } catch {
            matchersFailedAt = clock()
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
        let sampled = await takeSample()
        guard mine == request else { return }
        accept(sampled)
        republish(at: sampledAt ?? clock())
    }

    /// One process sample in capture order. The seq is handed out before
    /// the sample runs, so a slow sampler's stale result can be told from
    /// a faster sample that started later and already finished.
    private struct Sampled {
        var seq: Int
        var table: [ProcessSample]
        var at: Date
    }

    /// Take a process sample off the pool, stamped with its own wall time
    /// right after capture. `refresh()` and the trend tick both come
    /// through here so the shared meter state only ever moves forward.
    private func takeSample() async -> Sampled {
        sampleSeq += 1
        let seq = sampleSeq
        let sampler = self.sampler
        let table = await offMain { sampler.sample() }
        return Sampled(seq: seq, table: table, at: clock())
    }

    /// Feed the meters and the shared table only when `sampled` is newer
    /// than the last accepted sample. A slow sample finishing late must
    /// not move a CPU baseline or a busy streak back; the caller still
    /// uses the newest accepted state.
    private func accept(_ sampled: Sampled) {
        guard sampled.seq > lastAcceptedSeq else { return }
        lastAcceptedSeq = sampled.seq
        samples = sampled.table
        sampledAt = sampled.at
        cpu = meter.update(sampled.table, at: sampled.at)
        attribution = ProcessAttribution.attribute(sampled.table, matchers: matchers,
                                                   managedPIDs: managedPIDs)
        autoClean(at: sampled.at)
    }

    /// The auto clean switch, run over every accepted sample — the page's
    /// refreshes and the trend tick alike, so it works with the page
    /// closed. An orphan tree older than `limits.orphanAge` gets one
    /// SIGTERM, through the same kill plan and guards a manual Quit uses;
    /// a tree already signalled is left alone. With `autoCleanForce` a
    /// marked tree whose members are still alive `autoCleanGrace` later
    /// gets SIGKILL once. Marks prune once nothing in the tree answers
    /// the recorded start times, so a tree that died — or a pid the OS
    /// handed to somebody new — is forgotten.
    ///
    /// It waits for the matcher list to load: an empty one would read a
    /// harness's own helpers as orphans, and auto clean kills what it
    /// reads.
    private func autoClean(at now: Date) {
        guard limits.autoClean, matchersLoaded else { return }
        // Every pid a recorded tree held, so a member that outlived its
        // root and reparented into a new orphan is not signalled all
        // over again as the new tree's root.
        var marked: [Int32: Date] = [:]
        for mark in cleaned.values {
            marked[mark.tree.root.pid] = mark.tree.root.startedAt
            for descendant in mark.tree.descendants { marked[descendant.pid] = descendant.startedAt }
        }
        for tree in attribution.orphans
        where now.timeIntervalSince(tree.root.startedAt) > limits.orphanAge {
            guard cleaned[tree.root.pid]?.tree.root.startedAt != tree.root.startedAt,
                  marked[tree.root.pid] != tree.root.startedAt else { continue }
            signal([.orphanTree(tree)], force: false, in: samples)
            cleaned[tree.root.pid] = (tree: tree, at: now, forced: false)
        }
        let ancestors = selfAncestors(in: samples)
        for (pid, mark) in Array(cleaned) {
            let order = ComputeSummary.killOrder(for: .orphanTree(mark.tree), in: samples,
                                                 selfPID: selfPID, ancestors: ancestors)
            if order.isEmpty {
                cleaned[pid] = nil
            } else if limits.autoCleanForce, !mark.forced,
                      now.timeIntervalSince(mark.at) >= Self.autoCleanGrace {
                signal([.orphanTree(mark.tree)], force: true, in: samples)
                cleaned[pid] = (tree: mark.tree, at: mark.at, forced: true)
            }
        }
    }

    // MARK: Trends

    /// Starts the trend sampler, which then runs for as long as the app
    /// does: one tick now and one every `trendInterval`. AppModel calls it
    /// once at launch; the page's own three-second refresh is untouched.
    public func startTrends() {
        guard trendTask == nil else { return }
        startTrendLoop()
    }

    /// Spawn the loop for the current generation; ticks from an older
    /// one are dropped even if the cancelled task resumes first.
    private func startTrendLoop() {
        let generation = trendGeneration
        trendTask = Task { [weak self] in
            await self?.runTrendLoop(generation: generation)
        }
    }

    /// One tick, then sleep the interval, until cancelled or replaced.
    /// Tests call it with a sleep that throws once enough ticks have run.
    func runTrendLoop(generation: Int? = nil) async {
        let generation = generation ?? trendGeneration
        while !Task.isCancelled, generation == trendGeneration {
            await trendTick(at: clock(), generation: generation)
            guard !Task.isCancelled, generation == trendGeneration else { return }
            do {
                try await trendSleep(.seconds(trendInterval.seconds))
            } catch {
                return
            }
        }
    }

    /// The popup's Refresh on the Compute tab: restart the loop so it
    /// ticks now. A tick still in flight belongs to the old generation
    /// and is dropped before it touches the GPU meter or the history, so
    /// two ticks never race on the GPU baseline.
    public func tickTrendsNow() {
        trendGeneration += 1
        trendTask?.cancel()
        startTrendLoop()
    }

    /// Whether a tick from `generation` lost its loop: its task is dead
    /// or a newer loop has taken over, so it must not write shared state.
    private func replaced(_ generation: Int?) -> Bool {
        Task.isCancelled || (generation != nil && generation != trendGeneration)
    }

    /// One trend point per running harness, aggregated exactly the way the
    /// cards count. A tick whose last sample is still fresh (newer than
    /// half the interval) reuses it rather than sampling again, so the
    /// page being open never doubles the sampling cost.
    func trendTick(at now: Date, generation: Int? = nil) async {
        // Load the list once ever; a failed load may retry once a minute.
        if !matchersLoaded && (matchersFailedAt.map { now.timeIntervalSince($0) >= 60 } ?? true) {
            await loadMatchers()
            if replaced(generation) { return }
        }
        // Auto clean kills on what attribution says: a launchd-managed
        // service started after the last load would read as an orphan,
        // so the managed list keeps the refresh cadence Refresh uses
        // while the page stays closed.
        if limits.autoClean
            && (managedPIDsLoadedAt.map { now.timeIntervalSince($0) >= 60 } ?? true) {
            managedPIDs = await managedPIDLoader()
            managedPIDsLoadedAt = .now
            if replaced(generation) { return }
        }
        let freshEnough = sampledAt.map { now.timeIntervalSince($0) <= trendInterval.seconds / 2 } ?? false
        if !freshEnough {
            let sampled = await takeSample()
            // A stale finish is discarded inside; a tick whose loop was
            // replaced must not touch the meters or the history at all.
            if replaced(generation) { return }
            accept(sampled)
        }
        let gpu = gpuSampler, thermometer = temperatureSampler
        let (times, reading) = await offMain { (gpu.times(), thermometer.read()) }
        if replaced(generation) { return }
        let gpuAt = clock()
        if !times.isEmpty { gpuAvailable = true }
        let gpuPercent = gpuMeter.update(times, at: gpuAt)
        // Recorded even when nothing runs, so "now" moves past the last
        // harness that stopped.
        history.recordTick(sampledAt ?? now)
        if let reading {
            temperatureAvailable = true
            history.appendTemperature(TemperaturePoint(at: sampledAt ?? now, values: reading.values,
                                                       fans: reading.fans))
            onTemperature?(reading)
        }
        for (harness, procs) in attribution.byHarness where !procs.isEmpty {
            history.append(harness, TrendPoint(
                at: sampledAt ?? now,
                memoryBytes: procs.reduce(0) { $0 + $1.memoryBytes },
                cpuPercent: procs.reduce(0) { $0 + (cpu[$1.pid] ?? 0) },
                gpuPercent: times.isEmpty ? nil
                    : procs.reduce(0) { $0 + (gpuPercent[$1.pid] ?? 0) }))
        }
    }

    /// Send SIGTERM (SIGKILL when `force`) to an item, deepest child first,
    /// then take a fresh look. The kill plan is built on this call's own
    /// sample, not the shared table: a timer refresh landing meanwhile
    /// would drop this one's publish and leave it signalling a stale view.
    public func quit(_ item: CleanupItem, force: Bool = false) async {
        await quit([item], force: force)
    }

    /// The batch form: one fresh sample for every item's kill plan, so a
    /// clean-up confirm costs one table no matter how long the list is,
    /// then one refresh.
    public func quit(_ items: [CleanupItem], force: Bool = false) async {
        let sampler = self.sampler
        let fresh = await offMain { sampler.sample() }
        signal(items, force: force, in: fresh)
        await refresh()
    }

    /// Everything `harness` has running, as one batch. The set is
    /// attributed on this call's own fresh sample, so a process the
    /// harness spawned after the page's last refresh still quits. Helpers
    /// go first, the harness's own entry processes last: an app or CLI
    /// shuts its helpers down itself on the way out.
    public func quitHarness(_ harness: String, force: Bool = false) async {
        let sampler = self.sampler
        let fresh = await offMain { sampler.sample() }
        let found = ProcessAttribution.attribute(fresh, matchers: matchers,
                                                 managedPIDs: managedPIDs).byHarness[harness] ?? []
        let matcher = matchers.first { $0.harness == harness }
        let items = found
            .sorted { ($0.memoryBytes, $0.pid) > ($1.memoryBytes, $1.pid) }
            .map { sample in
                CleanupItem.process(sample, harness: harness,
                                    isMain: matcher.map { ProcessAttribution.isMainProcess(sample, of: $0) } ?? false)
            }
        signal(items.filter { !$0.isMain } + items.filter { $0.isMain },
               force: force, in: fresh, only: Set(found.map(\.pid)))
        await refresh()
    }

    /// SIGTERM (SIGKILL when `force`) to each item's kill plan over
    /// `fresh`; a pid two items share is signalled once. `only` caps the
    /// signalled pids to a set, so a whole-harness quit cannot reach a
    /// differently-attributed process that hangs under one of its own.
    private func signal(_ items: [CleanupItem], force: Bool, in fresh: [ProcessSample],
                        only allowed: Set<Int32>? = nil) {
        let ancestors = selfAncestors(in: fresh)
        let startedAt = Dictionary(fresh.map { ($0.pid, $0.startedAt) }, uniquingKeysWith: { a, _ in a })
        var seen: Set<Int32> = []
        for item in items {
            let order = ComputeSummary.killOrder(for: item, in: fresh,
                                                 selfPID: selfPID, ancestors: ancestors)
            for pid in order where !seen.contains(pid) && (allowed?.contains(pid) ?? true)
                && signaller.signal(pid, force ? SIGKILL : SIGTERM) {
                seen.insert(pid)
                if !force, let started = startedAt[pid] { signalledAt[pid] = (at: .now, startedAt: started) }
            }
        }
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
                    stillRunning: isStillRunning(sample.pid, startedAt: sample.startedAt, at: now))
            }.sorted { $0.sample.memoryBytes > $1.sample.memoryBytes }
        }
        processes = rows
        notRunning = order.filter { (attribution.byHarness[$0] ?? []).isEmpty }

        var orphanFlags: [Int32: Set<OrphanFlag>] = [:]
        orphans = attribution.orphans.map { tree in
            let flags = ComputeSummary.flags(for: tree, limits: limits, now: now)
            orphanFlags[tree.root.pid] = flags
            return OrphanRow(tree: tree, flags: flags,
                             stillRunning: isStillRunning(tree.root.pid, startedAt: tree.root.startedAt, at: now))
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
                                         matchers: matchers, limits: limits, now: now,
                                         managedPIDs: managedPIDs)

        let all = attribution.byHarness.values.flatMap { $0 } + attribution.orphans.flatMap { [$0.root] + $0.descendants }
        totals = Totals(
            memoryBytes: all.reduce(0) { $0 + $1.memoryBytes },
            cpuPercent: all.reduce(0) { $0 + (cpu[$1.pid] ?? 0) },
            threads: all.reduce(0) { $0 + $1.threads },
            processCount: all.count)

        // Dead pids forget they were signalled — and so does a pid whose
        // process changed, since its new owner was never signalled.
        let startedAt = Dictionary(samples.map { ($0.pid, $0.startedAt) }, uniquingKeysWith: { a, _ in a })
        signalledAt = signalledAt.filter { startedAt[$0.key] == $0.value.startedAt }
    }

    /// A pid counts as still running five seconds after its SIGTERM, but
    /// only for the process the signal went to: told apart from a pid the
    /// OS recycled onto a new process by its start time.
    private func isStillRunning(_ pid: Int32, startedAt: Date, at now: Date) -> Bool {
        guard let mark = signalledAt[pid], mark.startedAt == startedAt else { return false }
        return now.timeIntervalSince(mark.at) >= 5
    }
}
