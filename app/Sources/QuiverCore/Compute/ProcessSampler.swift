import Darwin
import Foundation

/// Something that can list the user's running processes. The real one is
/// `ProcessSampler`; tests use a table.
public protocol ProcessSampling: Sendable {
    func sample() -> [ProcessSample]
}

/// Reads the user's processes straight out of libproc: no subprocess, no
/// shell. Anything a process does not answer (it exited mid-sample, a field
/// is unreadable) costs that field or that process, never the sample.
///
/// Call it off the main actor: it makes a handful of syscalls per process.
public struct ProcessSampler: ProcessSampling, Sendable {
    public init() {}

    public func sample() -> [ProcessSample] {
        let count = proc_listallpids(nil, 0)
        guard count > 0 else { return [] }
        var pids = [pid_t](repeating: 0, count: Int(count))
        let filled = proc_listallpids(&pids, count * Int32(MemoryLayout<pid_t>.size))
        guard filled > 0 else { return [] }

        let uid = getuid()
        var samples: [ProcessSample] = []
        samples.reserveCapacity(Int(filled))
        for pid in pids.prefix(Int(filled)) where pid > 1 {
            guard let info = bsdInfo(of: pid), info.pbi_uid == uid else { continue }
            samples.append(sample(pid, info: info))
        }
        return samples
    }

    private func sample(_ pid: Int32, info bsd: proc_bsdinfo) -> ProcessSample {
        var task = proc_taskinfo()
        let taskSize = Int32(MemoryLayout<proc_taskinfo>.size)
        let threads = proc_pidinfo(pid, PROC_PIDTASKINFO, 0, &task, taskSize) == taskSize
            ? Int(task.pti_threadnum) : 0
        let fp = footprint(of: pid)
        return ProcessSample(
            pid: pid,
            ppid: Int32(bsd.pbi_ppid),
            path: path(of: pid),
            arguments: arguments(of: pid),
            cwd: cwd(of: pid),
            startedAt: Date(timeIntervalSince1970: TimeInterval(bsd.pbi_start_tvsec)
                            + TimeInterval(bsd.pbi_start_tvusec) / 1_000_000),
            threads: threads,
            memoryBytes: fp.memory,
            cpuTimeNanos: fp.cpu)
    }

    /// mach absolute time -> nanoseconds, read once for every sample.
    private static let timebase: mach_timebase_info_data_t = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return info
    }()

    /// ri_phys_footprint, plus ri_user_time + ri_system_time converted from
    /// mach absolute time units to nanoseconds.
    private func footprint(of pid: Int32) -> (memory: UInt64, cpu: UInt64) {
        var info = rusage_info_v4()
        // proc_pid_rusage's rusage_info_t is a void *, so Swift wants the
        // struct's address rebound to a pointer-to-pointer. The call still
        // fills `info` in place; it never writes a pointer back.
        let answered = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) {
                proc_pid_rusage(pid, RUSAGE_INFO_V4, $0)
            }
        }
        guard answered >= 0 else { return (0, 0) }
        let timebase = Self.timebase
        let units = info.ri_user_time + info.ri_system_time
        let nanos = units * UInt64(timebase.numer) / UInt64(max(timebase.denom, 1))
        return (info.ri_phys_footprint, nanos)
    }

    private func bsdInfo(of pid: Int32) -> proc_bsdinfo? {
        var info = proc_bsdinfo()
        let size = Int32(MemoryLayout<proc_bsdinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size else { return nil }
        return info
    }

    private func path(of pid: Int32) -> String {
        // PROC_PIDPATHINFO_MAXSIZE is 4 * MAXPATHLEN, a macro expression
        // Swift does not import.
        var buffer = [UInt8](repeating: 0, count: 4096)
        guard proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 else { return "" }
        return String(decoding: buffer.prefix { $0 != 0 }, as: UTF8.self)
    }

    private func cwd(of pid: Int32) -> String? {
        var info = proc_vnodepathinfo()
        let size = Int32(MemoryLayout<proc_vnodepathinfo>.size)
        guard proc_pidinfo(pid, PROC_PIDVNODEPATHINFO, 0, &info, size) == size else { return nil }
        let cwd = withUnsafeBytes(of: &info.pvi_cdir.vip_path) { buffer in
            String(decoding: buffer.prefix { $0 != 0 }, as: UTF8.self)
        }
        return cwd.isEmpty ? nil : cwd
    }

    /// KERN_PROCARGS2: an int32 argc, the exec path, NUL padding, then argc
    /// NUL-separated argv strings (followed by the environment, which this
    /// does not read).
    private func arguments(of pid: Int32) -> [String] {
        var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
        var size = 0
        guard sysctl(&mib, 3, nil, &size, nil, 0) == 0, size > MemoryLayout<Int32>.size else { return [] }
        var buffer = [UInt8](repeating: 0, count: size)
        guard sysctl(&mib, 3, &buffer, &size, nil, 0) == 0 else { return [] }
        let argc = Int(buffer.withUnsafeBytes { $0.load(as: Int32.self) })
        var i = MemoryLayout<Int32>.size
        while i < size && buffer[i] != 0 { i += 1 }   // exec path
        while i < size && buffer[i] == 0 { i += 1 }   // padding
        var arguments: [String] = []
        for _ in 0..<min(argc, 256) where i < size {
            let end = buffer[i...].firstIndex(of: 0) ?? size
            arguments.append(String(decoding: buffer[i..<end], as: UTF8.self))
            i = end + 1
        }
        return arguments
    }
}
