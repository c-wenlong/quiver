import Foundation

/// What makes a process or an orphan tree worth flagging, set on the
/// Compute page and kept in UserDefaults.
public struct ComputeLimits: Sendable, Equatable {
    /// An orphan older than this counts as old. Seconds.
    public var orphanAge: TimeInterval = 24 * 3600
    /// A process (or a whole orphan tree) above this is flagged. Bytes.
    public var memoryBytes: UInt64 = 2 << 30
    /// Quit orphans older than `orphanAge` without asking. The switch
    /// sends SIGTERM, as a clean-up Quit does.
    public var autoClean: Bool = false
    /// With `autoClean`, force quit an orphan still alive a grace after
    /// its quit signal. Only meaningful while `autoClean` is on.
    public var autoCleanForce: Bool = false

    /// Whole minutes under this key; a minute is the field's finest unit.
    public static let orphanAgeKey = "computeOrphanAgeMinutes"
    /// The pre-minutes key, read once for upgrade then removed on save.
    public static let legacyOrphanAgeKey = "computeOrphanAgeHours"
    public static let memoryLimitKey = "computeMemoryLimitGB"
    public static let autoCleanKey = "computeAutoClean"
    public static let autoCleanForceKey = "computeAutoCleanForce"

    /// The field's bounds: a minute up to a year.
    public static let orphanAgeRange: ClosedRange<TimeInterval> = 60...(365 * 86400)

    public init() {}

    public init(defaults: UserDefaults) {
        if let minutes = defaults.object(forKey: Self.orphanAgeKey) as? Int, minutes > 0 {
            orphanAge = TimeInterval(minutes) * 60
        } else if let hours = defaults.object(forKey: Self.legacyOrphanAgeKey) as? Int, hours > 0 {
            orphanAge = TimeInterval(hours) * 3600
        }
        orphanAge = min(max(orphanAge, Self.orphanAgeRange.lowerBound),
                        Self.orphanAgeRange.upperBound)
        if let gb = defaults.object(forKey: Self.memoryLimitKey) as? Int, gb > 0 {
            memoryBytes = UInt64(gb) << 30
        }
        autoClean = defaults.bool(forKey: Self.autoCleanKey)
        autoCleanForce = defaults.bool(forKey: Self.autoCleanForceKey)
    }

    public func save(to defaults: UserDefaults) {
        let clamped = min(max(orphanAge, Self.orphanAgeRange.lowerBound),
                          Self.orphanAgeRange.upperBound)
        defaults.set(Int(clamped / 60), forKey: Self.orphanAgeKey)
        defaults.removeObject(forKey: Self.legacyOrphanAgeKey)
        defaults.set(Int(memoryBytes >> 30), forKey: Self.memoryLimitKey)
        defaults.set(autoClean, forKey: Self.autoCleanKey)
        defaults.set(autoCleanForce, forKey: Self.autoCleanForceKey)
    }
}

/// The unit the orphan-age field shows a duration in.
public enum OrphanAgeUnit: String, CaseIterable, Sendable {
    case minute, hour, day

    public var seconds: TimeInterval {
        switch self {
        case .minute: 60
        case .hour: 3600
        case .day: 86400
        }
    }

    public var title: String {
        switch self {
        case .minute: "Min"
        case .hour: "Hour"
        case .day: "Day"
        }
    }

    /// What the field saves for `value` in `unit`: nil for zero or less
    /// (the field snaps back), otherwise clamped into the allowed range.
    public static func resolve(value: Int, unit: OrphanAgeUnit) -> TimeInterval? {
        guard value > 0 else { return nil }
        let range = ComputeLimits.orphanAgeRange
        return min(max(TimeInterval(value) * unit.seconds, range.lowerBound), range.upperBound)
    }

    /// The largest unit the duration divides evenly into, so 2880
    /// minutes shows as "2 Day" not "2880 Min". Rounds to whole
    /// minutes first, so nothing under a minute survives.
    public static func split(_ seconds: TimeInterval) -> (value: Int, unit: OrphanAgeUnit) {
        let minutes = max(1, Int((seconds / 60).rounded()))
        if minutes % (24 * 60) == 0 { return (minutes / (24 * 60), .day) }
        if minutes % 60 == 0 { return (minutes / 60, .hour) }
        return (minutes, .minute)
    }
}

/// Turns each sample's cumulative CPU time into a percentage, like
/// Activity Monitor: over all cores, so it can pass 100. Tracks "busy":
/// a run of samples at or above 80% going back at least `busySpan`.
public struct CPUMeter: Sendable {
    /// How hard a process must run, and for how long, to count as busy.
    public static let busyPercent = 80.0
    public static let busySpan: TimeInterval = 120

    private struct Previous {
        var cpu: UInt64
        var wall: Date
        var startedAt: Date
        /// When the current at-or-above-80% run began, measured in wall time.
        var busySince: Date?
    }

    private var previous: [Int32: Previous] = [:]

    public init() {}

    /// CPU% per pid since the last sample. A pid gets none on its first
    /// sighting, or when its startedAt moved (the pid was reused).
    @discardableResult
    public mutating func update(_ samples: [ProcessSample], at now: Date) -> [Int32: Double] {
        var percent: [Int32: Double] = [:]
        var next: [Int32: Previous] = [:]
        for sample in samples {
            var entry = Previous(cpu: sample.cpuTimeNanos, wall: now, startedAt: sample.startedAt)
            if let old = previous[sample.pid], old.startedAt == sample.startedAt {
                let used = sample.cpuTimeNanos >= old.cpu ? sample.cpuTimeNanos - old.cpu : 0
                let wall = now.timeIntervalSince(old.wall)
                if wall > 0 {
                    let p = Double(used) / (wall * 1e9) * 100
                    percent[sample.pid] = p
                    entry.busySince = p >= Self.busyPercent ? (old.busySince ?? old.wall) : nil
                }
            }
            next[sample.pid] = entry
        }
        previous = next
        return percent
    }

    /// Busy is a streak, not a spike: the last run of >=80% samples covers
    /// at least `busySpan` of wall time.
    public func isBusy(_ pid: Int32) -> Bool {
        guard let entry = previous[pid], let since = entry.busySince else { return false }
        return entry.wall.timeIntervalSince(since) >= Self.busySpan
    }
}

/// What one harness's processes cost the Mac right now.
public struct HarnessUsage: Sendable, Equatable, Identifiable {
    public var id: String { harness }
    public var harness: String
    public var processCount: Int
    public var threads: Int
    public var memoryBytes: UInt64
    /// 0 when nothing has been sampled long enough to tell.
    public var cpuPercent: Double
    /// When the oldest process started.
    public var runningSince: Date
}

public enum ProcessFlag: String, Sendable, Equatable { case overMemory, busy }
public enum OrphanFlag: String, Sendable, Equatable { case old, overMemory }

/// One thing Clean up suggests quitting.
public struct CleanupItem: Sendable, Equatable, Identifiable {
    public enum Kind: Sendable, Equatable {
        case orphanTree(OrphanTree)
        case process(ProcessSample, harness: String)
    }

    public var id: String {
        switch kind {
        case .orphanTree(let tree): "orphan-\(tree.root.pid)"
        case .process(let sample, _): "process-\(sample.pid)"
        }
    }

    public var kind: Kind
    /// Why it was flagged, worded for the clean-up list.
    public var reason: String
    /// Ticked in the sheet by default.
    public var preselected: Bool
    /// The harness's own entry process (the app's executable or the CLI):
    /// quitting it alone is enough, it shuts its own helpers down.
    public var isMain: Bool

    public init(kind: Kind, reason: String, preselected: Bool, isMain: Bool = false) {
        self.kind = kind
        self.reason = reason
        self.preselected = preselected
        self.isMain = isMain
    }

    /// The group the clean-up sheet files it under: the harness a
    /// process belongs to, nil for an orphan tree.
    public var harness: String? {
        switch kind {
        case .orphanTree: nil
        case .process(_, let harness): harness
        }
    }

    /// A single process to quit, for the row menu rather than the sheet.
    public static func process(_ sample: ProcessSample, harness: String, isMain: Bool = false) -> CleanupItem {
        CleanupItem(kind: .process(sample, harness: harness), reason: "",
                    preselected: false, isMain: isMain)
    }

    public static func orphanTree(_ tree: OrphanTree) -> CleanupItem {
        CleanupItem(kind: .orphanTree(tree), reason: "", preselected: false)
    }
}

/// Totals, flags, clean-up candidates and kill plans over an attribution.
public enum ComputeSummary {
    /// One usage per harness in `order` (swe's, so the page lists them as
    /// swe does), skipping harnesses with no processes.
    public static func usages(byHarness: [String: [ProcessSample]], cpu: [Int32: Double],
                              order: [String]) -> [HarnessUsage] {
        order.compactMap { harness in
            guard let samples = byHarness[harness], !samples.isEmpty else { return nil }
            return HarnessUsage(
                harness: harness,
                processCount: samples.count,
                threads: samples.reduce(0) { $0 + $1.threads },
                memoryBytes: samples.reduce(0) { $0 + $1.memoryBytes },
                cpuPercent: samples.reduce(0) { $0 + (cpu[$1.pid] ?? 0) },
                runningSince: samples.map(\.startedAt).min() ?? .now)
        }
    }

    public static func flags(for sample: ProcessSample, busy: Bool, limits: ComputeLimits) -> Set<ProcessFlag> {
        var flags: Set<ProcessFlag> = []
        if sample.memoryBytes > limits.memoryBytes { flags.insert(.overMemory) }
        if busy { flags.insert(.busy) }
        return flags
    }

    public static func flags(for tree: OrphanTree, limits: ComputeLimits, now: Date) -> Set<OrphanFlag> {
        var flags: Set<OrphanFlag> = []
        if now.timeIntervalSince(tree.root.startedAt) > limits.orphanAge { flags.insert(.old) }
        if tree.memoryBytes > limits.memoryBytes { flags.insert(.overMemory) }
        return flags
    }

    /// What Clean up suggests, in sheet group order: the orphan trees
    /// first, then each harness in the matchers' (swe's) order, and inside
    /// a group the preselected candidates before the rest, most memory
    /// first. One item per pid: a process several rules hit keeps every
    /// reason and is preselected when any rule preselects it. A candidate
    /// hanging under another candidate is dropped: quitting the
    /// ancestor's tree covers it.
    public static func cleanup(byHarness: [String: [ProcessSample]], orphans: [OrphanTree],
                               orphanFlags: [Int32: Set<OrphanFlag>],
                               processFlags: [Int32: Set<ProcessFlag>],
                               matchers: [HarnessMatcher], limits: ComputeLimits,
                               now: Date, managedPIDs: Set<Int32> = []) -> [CleanupItem] {
        /// A process candidate as the rules merge it.
        struct Candidate {
            var sample: ProcessSample
            var reasons: [String] = []
            var preselected = false
            var isMain = false
        }

        /// Every orphan tree is listed: the flagged ones preselected, the
        /// rest left to the user.
        var items: [CleanupItem] = orphans.map { tree in
            let flags = orphanFlags[tree.root.pid] ?? []
            var reasons: [String] = []
            if flags.contains(.old) {
                reasons.append("Running for \(duration(now.timeIntervalSince(tree.root.startedAt)))")
            }
            if flags.contains(.overMemory) {
                reasons.append("Using \(bytes(tree.memoryBytes))")
            }
            if reasons.isEmpty {
                return CleanupItem(kind: .orphanTree(tree),
                                   reason: "Orphaned, running for \(duration(now.timeIntervalSince(tree.root.startedAt)))",
                                   preselected: false)
            }
            return CleanupItem(kind: .orphanTree(tree),
                               reason: reasons.joined(separator: "; "), preselected: true)
        }
        items.sort { $0.preselected != $1.preselected
            ? $0.preselected : memory(of: $0) > memory(of: $1) }

        /// Shells: copies of a prompt are not duplicate servers.
        let shells: Set<String> = ["zsh", "bash", "sh", "fish", "dash", "tcsh", "login", "nu"]
        struct DuplicateKey: Hashable {
            var ppid: Int32
            var path: String
            var arguments: [String]
            var cwd: String
        }

        for matcher in matchers {
            guard let samples = byHarness[matcher.harness], !samples.isEmpty else { continue }
            let harness = matcher.harness
            let name = UsageFormat.harness(harness)
            var candidates: [Int32: Candidate] = [:]
            func add(_ sample: ProcessSample, _ reason: String, preselected: Bool, isMain: Bool) {
                var entry = candidates[sample.pid] ?? Candidate(sample: sample)
                entry.reasons.append(reason)
                entry.preselected = entry.preselected || preselected
                entry.isMain = entry.isMain || isMain
                candidates[sample.pid] = entry
            }

            // Leftovers from an earlier launch of the app: helpers hung on
            // launchd out of the bundle, started before this launch's main
            // process (or with no main running at all).
            if let appPath = matcher.appPath, !appPath.isEmpty {
                let macos = appPath + "/Contents/MacOS"
                let launch = samples
                    .filter { ($0.path as NSString).deletingLastPathComponent == macos }
                    .map(\.startedAt).min()
                for p in samples where p.ppid == 1
                    && p.path.hasPrefix(appPath + "/")
                    && !ProcessAttribution.isMainProcess(p, of: matcher)
                    && !managedPIDs.contains(p.pid)
                    && (launch.map { p.startedAt < $0.addingTimeInterval(-60) } ?? true) {
                    add(p, launch == nil
                        ? "Left over after \(name) quit"
                        : "Left over from an earlier \(name) launch",
                        preselected: true, isMain: false)
                }
            }

            // Duplicate servers: identical children of the same harness
            // parent, where only the newest copy is wanted.
            let pids = Set(samples.map(\.pid))
            var groups: [DuplicateKey: [ProcessSample]] = [:]
            for p in samples where pids.contains(p.ppid)
                && p.cwd != nil
                && !shells.contains(p.runnerName)
                && !p.arguments.contains(where: { $0.hasPrefix("--type=") })
                && !ProcessAttribution.isMainProcess(p, of: matcher) {
                groups[DuplicateKey(ppid: p.ppid, path: p.path,
                                    arguments: p.arguments, cwd: p.cwd!), default: []].append(p)
            }
            for members in groups.values where members.count > 1 {
                let sorted = members.sorted {
                    ($0.startedAt, $0.pid) > ($1.startedAt, $1.pid)
                }
                for p in sorted.dropFirst()
                where now.timeIntervalSince(p.startedAt) > limits.orphanAge
                    && !(processFlags[p.pid]?.contains(.busy) ?? false) {
                    add(p, "Duplicate of a newer copy, started "
                        + duration(now.timeIntervalSince(p.startedAt)) + " ago",
                        preselected: true, isMain: false)
                }
            }

            // Cost and busy flags, as before.
            for sample in samples {
                let flags = processFlags[sample.pid] ?? []
                guard !flags.isEmpty else { continue }
                let isMain = ProcessAttribution.isMainProcess(sample, of: matcher)
                if flags.contains(.overMemory) {
                    if isMain {
                        add(sample, "This is \(name) itself; quitting closes it", preselected: false, isMain: true)
                    } else {
                        add(sample, "Using \(bytes(sample.memoryBytes))", preselected: true, isMain: false)
                    }
                }
                if flags.contains(.busy) {
                    add(sample, "Busy for over two minutes", preselected: false, isMain: isMain)
                }
            }

            // A candidate whose ancestor is also a ticked non-main
            // candidate goes: the ancestor's tree quit covers it. An
            // unticked ancestor (busy only, say) covers nothing, so the
            // child stays listed.
            let byPid = Dictionary(samples.map { ($0.pid, $0) }, uniquingKeysWith: { first, _ in first })
            var covered: Set<Int32> = []
            for (pid, _) in candidates {
                var node = byPid[pid]
                while let current = node {
                    guard let parent = byPid[current.ppid], parent.pid != current.pid else { break }
                    if let ancestor = candidates[parent.pid], !ancestor.isMain, ancestor.preselected {
                        covered.insert(pid)
                        break
                    }
                    node = parent
                }
            }
            for pid in covered { candidates[pid] = nil }

            let group = candidates.values.map { entry in
                CleanupItem(kind: .process(entry.sample, harness: harness),
                            reason: entry.reasons.joined(separator: "; "),
                            preselected: entry.preselected, isMain: entry.isMain)
            }
            .sorted { $0.preselected != $1.preselected
                ? $0.preselected : memory(of: $0) > memory(of: $1) }
            items.append(contentsOf: group)
        }
        return items
    }

    /// The memory figure a group's sort goes by.
    private static func memory(of item: CleanupItem) -> UInt64 {
        switch item.kind {
        case .orphanTree(let tree): tree.memoryBytes
        case .process(let sample, _): sample.memoryBytes
        }
    }

    /// The order to signal an item's processes in: a harness's own entry
    /// process goes alone (the app or CLI shuts its helpers down itself);
    /// anything else goes descendants deepest-first, then the root, so
    /// children do not die as zombies waiting on a parent that already
    /// went. Never pid 1, the caller, or the caller's ancestors: Quiver
    /// can never quit itself. A pid the item recorded is only signalled
    /// when the current sample still has it with the same startedAt: a
    /// pid that died and got reused is somebody else's process now. A
    /// tree's membership comes from the current sample, so a worker the
    /// root spawned after the page listed it is still quit, and so is a
    /// recorded child that outlived its root.
    public static func killOrder(for item: CleanupItem, in samples: [ProcessSample],
                                 selfPID: Int32, ancestors: Set<Int32>) -> [Int32] {
        let byPid = Dictionary(samples.map { ($0.pid, $0) }, uniquingKeysWith: { first, _ in first })
        var children: [Int32: [ProcessSample]] = [:]
        for p in samples { children[p.ppid, default: []].append(p) }
        let root: ProcessSample
        let members: [ProcessSample]
        let rootIsLive: Bool
        switch item.kind {
        case .orphanTree(let tree):
            root = tree.root
            rootIsLive = byPid[root.pid]?.startedAt == root.startedAt
            let survivors = tree.descendants.filter { byPid[$0.pid]?.startedAt == $0.startedAt }
            members = (rootIsLive ? descendants(of: root.pid, under: children) : [])
                + survivors + survivors.flatMap { descendants(of: $0.pid, under: children) }
        case .process(let sample, _):
            root = sample
            rootIsLive = byPid[root.pid]?.startedAt == root.startedAt
            members = rootIsLive && !item.isMain ? descendants(of: root.pid, under: children) : []
        }
        // Depth is the distance down from the root within this item's
        // tree, so deeper members are signalled first.
        var depth: [Int32: Int] = [root.pid: 0]
        for member in members {
            var node = member
            var d = 0
            while node.pid != root.pid, d < 64, let parent = byPid[node.ppid] {
                node = parent
                d += 1
            }
            depth[member.pid] = d
        }
        let ordered = members.sorted { (depth[$0.pid] ?? 0) > (depth[$1.pid] ?? 0) }
            + (rootIsLive ? [root] : [])
        var out: [Int32] = []
        var seen: Set<Int32> = []
        for p in ordered {
            guard p.pid > 1, p.pid != selfPID, !ancestors.contains(p.pid), !seen.contains(p.pid) else { continue }
            out.append(p.pid)
            seen.insert(p.pid)
        }
        return out
    }

    /// Every process under `pid`, walking the sample's own ppid links.
    private static func descendants(of pid: Int32, under children: [Int32: [ProcessSample]]) -> [ProcessSample] {
        var out: [ProcessSample] = []
        var frontier: [Int32] = [pid]
        var visited: Set<Int32> = [pid]
        for _ in 0..<64 where !frontier.isEmpty {
            let next = frontier.flatMap { children[$0] ?? [] }.filter { visited.insert($0.pid).inserted }
            out.append(contentsOf: next)
            frontier = next.map(\.pid)
        }
        return out
    }

    /// "45m", "3h 12m", "3d 6h".
    public static func duration(_ interval: TimeInterval) -> String {
        let minutes = Int(interval / 60)
        if interval >= 86400 { return "\(minutes / 1440)d \(minutes % 1440 / 60)h" }
        if interval >= 3600 { return "\(minutes / 60)h \(minutes % 60)m" }
        return "\(max(minutes, 0))m"
    }

    /// Physical footprint the way Activity Monitor shows it, except that
    /// nothing reads "0 KB": the formatter's own "Zero KB" sat oddly in
    /// a line of figures.
    public static func bytes(_ count: UInt64) -> String {
        if count == 0 { return "0 KB" }
        let formatter = ByteCountFormatter()
        formatter.countStyle = .memory
        return formatter.string(fromByteCount: Int64(count))
    }

    /// The line over the trend card: every starred harness's processes
    /// added up.
    public static func totalsLine(_ totals: ComputeModel.Totals) -> String {
        let count = totals.processCount
        return "\(bytes(totals.memoryBytes)) across \(count) \(count == 1 ? "process" : "processes") "
            + "· CPU \(percent(totals.cpuPercent))% · \(threads(totals.threads))"
    }

    /// "1 thread", "28 threads".
    public static func threads(_ count: Int) -> String {
        "\(count) \(count == 1 ? "thread" : "threads")"
    }

    /// Whole percent once it is two digits, one decimal below it.
    public static func percent(_ percent: Double) -> String {
        percent.formatted(.number.precision(.fractionLength(percent >= 10 ? 0 : 1)))
    }
}
