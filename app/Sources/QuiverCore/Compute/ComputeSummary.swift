import Foundation

/// What makes a process or an orphan tree worth flagging, set on the
/// Compute page and kept in UserDefaults.
public struct ComputeLimits: Sendable, Equatable {
    /// An orphan older than this counts as old. Seconds.
    public var orphanAge: TimeInterval = 24 * 3600
    /// A process (or a whole orphan tree) above this is flagged. Bytes.
    public var memoryBytes: UInt64 = 2 << 30

    public static let orphanAgeKey = "computeOrphanAgeHours"
    public static let memoryLimitKey = "computeMemoryLimitGB"

    public init() {}

    public init(defaults: UserDefaults) {
        if let hours = defaults.object(forKey: Self.orphanAgeKey) as? Int, hours > 0 {
            orphanAge = TimeInterval(hours) * 3600
        }
        if let gb = defaults.object(forKey: Self.memoryLimitKey) as? Int, gb > 0 {
            memoryBytes = UInt64(gb) << 30
        }
    }

    public func save(to defaults: UserDefaults) {
        defaults.set(Int(orphanAge / 3600), forKey: Self.orphanAgeKey)
        defaults.set(Int(memoryBytes >> 30), forKey: Self.memoryLimitKey)
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

    public init(kind: Kind, reason: String, preselected: Bool) {
        self.kind = kind
        self.reason = reason
        self.preselected = preselected
    }

    /// A single process to quit, for the row menu rather than the sheet.
    public static func process(_ sample: ProcessSample, harness: String) -> CleanupItem {
        CleanupItem(kind: .process(sample, harness: harness), reason: "", preselected: false)
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

    /// What Clean up suggests. Old or over-memory orphan trees come
    /// preselected; an over-memory process does too unless it is a harness
    /// root (quitting that closes the app or CLI itself); busy processes
    /// are listed but left to the user.
    public static func cleanup(byHarness: [String: [ProcessSample]], orphans: [OrphanTree],
                               orphanFlags: [Int32: Set<OrphanFlag>],
                               processFlags: [Int32: Set<ProcessFlag>],
                               matchers: [HarnessMatcher], limits: ComputeLimits,
                               now: Date) -> [CleanupItem] {
        var items: [CleanupItem] = []
        for tree in orphans {
            let flags = orphanFlags[tree.root.pid] ?? []
            var reasons: [String] = []
            if flags.contains(.old) {
                reasons.append("Parent gone for \(duration(now.timeIntervalSince(tree.root.startedAt)))")
            }
            if flags.contains(.overMemory) {
                reasons.append("Using \(bytes(tree.memoryBytes))")
            }
            if !reasons.isEmpty {
                items.append(CleanupItem(kind: .orphanTree(tree),
                                         reason: reasons.joined(separator: "; "), preselected: true))
            }
        }
        for (harness, samples) in byHarness.sorted(by: { $0.key < $1.key }) {
            guard let matcher = matchers.first(where: { $0.harness == harness }) else { continue }
            for sample in samples.sorted(by: { $0.memoryBytes > $1.memoryBytes }) {
                let flags = processFlags[sample.pid] ?? []
                guard !flags.isEmpty else { continue }
                var reasons: [String] = []
                var preselected = false
                if flags.contains(.overMemory) {
                    if ProcessAttribution.isRoot(sample, of: matcher) {
                        reasons.append("This is \(UsageFormat.harness(harness)) itself; quitting closes it")
                    } else {
                        reasons.append("Using \(bytes(sample.memoryBytes))")
                        preselected = true
                    }
                }
                if flags.contains(.busy) {
                    reasons.append("Busy for over two minutes")
                }
                items.append(CleanupItem(kind: .process(sample, harness: harness),
                                         reason: reasons.joined(separator: "; "),
                                         preselected: preselected))
            }
        }
        return items
    }

    /// The order to signal an item's processes in: every descendant
    /// deepest-first, then the root, so children do not die as zombies
    /// waiting on a parent that already went. Never pid 1, the caller,
    /// or the caller's ancestors: Quiver can never quit itself.
    public static func killOrder(for item: CleanupItem, in samples: [ProcessSample],
                                 selfPID: Int32, ancestors: Set<Int32>) -> [Int32] {
        let byPid = Dictionary(samples.map { ($0.pid, $0) }, uniquingKeysWith: { first, _ in first })
        var children: [Int32: [ProcessSample]] = [:]
        for p in samples { children[p.ppid, default: []].append(p) }
        let root: ProcessSample
        let members: [ProcessSample]
        switch item.kind {
        case .orphanTree(let tree):
            root = tree.root
            members = tree.descendants
        case .process(let sample, _):
            root = sample
            members = descendants(of: sample.pid, under: children)
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
        let ordered = members.sorted { (depth[$0.pid] ?? 0) > (depth[$1.pid] ?? 0) } + [root]
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

    /// Physical footprint the way Activity Monitor shows it.
    public static func bytes(_ count: UInt64) -> String {
        let formatter = ByteCountFormatter()
        formatter.countStyle = .memory
        return formatter.string(fromByteCount: Int64(count))
    }
}
