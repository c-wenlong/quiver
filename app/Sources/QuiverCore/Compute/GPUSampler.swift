import Darwin
import Foundation
import IOKit

/// Cumulative GPU time per pid, in nanoseconds. The real one walks IOKit;
/// tests use a table.
public protocol GPUSampling: Sendable {
    func times() -> [Int32: UInt64]
}

/// Turns each tick's cumulative GPU time into a percentage, like
/// CPUMeter: delta time over delta wall, so it can pass 100. A pid gets
/// none on its first sighting, or when its counter went backwards (the
/// process exited and its user client reset).
public struct GPUMeter: Sendable {
    private var previous: [Int32: (nanos: UInt64, wall: Date)] = [:]

    public init() {}

    @discardableResult
    public mutating func update(_ times: [Int32: UInt64], at now: Date) -> [Int32: Double] {
        var percent: [Int32: Double] = [:]
        var next: [Int32: (nanos: UInt64, wall: Date)] = [:]
        for (pid, nanos) in times {
            next[pid] = (nanos, now)
            guard let old = previous[pid], nanos >= old.nanos else { continue }
            let wall = now.timeIntervalSince(old.wall)
            guard wall > 0 else { continue }
            percent[pid] = Double(nanos - old.nanos) / (wall * 1e9) * 100
        }
        previous = next
        return percent
    }
}

/// Per-process GPU time from IOKit. Every IOAccelerator service has a
/// user-client child per client, whose properties name the client
/// ("pid 402, WindowServer") and report an AppUsage list of usage dicts
/// with `accumulatedGPUTime` in nanoseconds. A Mac (or a sandbox) that
/// answers none of this returns an empty map, and the GPU tab says so
/// rather than drawing flat lines.
public struct GPUSampler: GPUSampling {
    public init() {}

    public func times() -> [Int32: UInt64] {
        var times: [Int32: UInt64] = [:]
        var services: io_iterator_t = 0
        guard IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOAccelerator"),
                                           &services) == KERN_SUCCESS else { return [:] }
        defer { IOObjectRelease(services) }
        while true {
            let service = IOIteratorNext(services)
            guard service != 0 else { break }
            defer { IOObjectRelease(service) }
            var children: io_iterator_t = 0
            guard IORegistryEntryGetChildIterator(service, kIOServicePlane, &children) == KERN_SUCCESS
            else { continue }
            defer { IOObjectRelease(children) }
            while true {
                let child = IOIteratorNext(children)
                guard child != 0 else { break }
                defer { IOObjectRelease(child) }
                var raw: Unmanaged<CFMutableDictionary>?
                guard IORegistryEntryCreateCFProperties(child, &raw, kCFAllocatorDefault, 0) == KERN_SUCCESS,
                      let properties = raw?.takeRetainedValue() as? [String: Any],
                      let sample = Self.clientSample(properties)
                else { continue }
                times[sample.pid, default: 0] += sample.nanos
            }
        }
        return times
    }

    /// "pid 402, WindowServer" -> 402. Anything else answers nil.
    static func creatorPID(_ creator: String) -> Int32? {
        guard creator.hasPrefix("pid ") else { return nil }
        let digits = creator.dropFirst("pid ".count).prefix(while: \.isNumber)
        return Int32(digits)
    }

    /// One user client's contribution to the pid map, or nil when its
    /// creator names no real pid or its AppUsage has no readable counter:
    /// a client without a counter is skipped, not counted as idle.
    static func clientSample(_ properties: [String: Any]) -> (pid: Int32, nanos: UInt64)? {
        guard let creator = properties["IOUserClientCreator"] as? String,
              let pid = creatorPID(creator), pid > 1,
              let nanos = gpuTime(properties["AppUsage"]) else { return nil }
        return (pid, nanos)
    }

    /// The total GPU time one user client reports: AppUsage is a list of
    /// per-service dicts, each with `accumulatedGPUTime` in nanoseconds.
    /// Nil when not one dict carries a readable counter; an explicit 0
    /// counts as a reading.
    static func gpuTime(_ appUsage: Any?) -> UInt64? {
        guard let entries = appUsage as? [[String: Any]] else { return nil }
        var total: UInt64 = 0
        var seen = false
        for entry in entries {
            guard let nanos = (entry["accumulatedGPUTime"] as? NSNumber)?.uint64Value
            else { continue }
            seen = true
            total += nanos
        }
        return seen ? total : nil
    }
}
