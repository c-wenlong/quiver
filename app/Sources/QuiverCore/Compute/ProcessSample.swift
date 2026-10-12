import Foundation

/// One running process at a moment in time, read by `ProcessSampler`.
public struct ProcessSample: Sendable, Equatable, Identifiable {
    public var id: Int32 { pid }
    public var pid: Int32
    public var ppid: Int32
    /// The executable path, "" when it could not be read.
    public var path: String
    /// The process's argv, [] when it could not be read.
    public var arguments: [String]
    /// Its working directory, nil when it could not be read.
    public var cwd: String?
    public var startedAt: Date
    public var threads: Int
    /// Physical footprint, what Activity Monitor calls Memory.
    public var memoryBytes: UInt64
    /// User plus system CPU time, in nanoseconds.
    public var cpuTimeNanos: UInt64

    public init(pid: Int32, ppid: Int32, path: String = "", arguments: [String] = [],
                cwd: String? = nil, startedAt: Date, threads: Int = 0,
                memoryBytes: UInt64 = 0, cpuTimeNanos: UInt64 = 0) {
        self.pid = pid
        self.ppid = ppid
        self.path = path
        self.arguments = arguments
        self.cwd = cwd
        self.startedAt = startedAt
        self.threads = threads
        self.memoryBytes = memoryBytes
        self.cpuTimeNanos = cpuTimeNanos
    }

    /// The basename of the executable path, else of argv[0], else the pid.
    public var name: String {
        if !path.isEmpty { return (path as NSString).lastPathComponent }
        if let first = arguments.first, !first.isEmpty {
            return (first as NSString).lastPathComponent
        }
        return "pid \(pid)"
    }

    /// The name a script runner answers to: lowercased, with versioned
    /// Python builds ("python3.12", the framework's "Python") folded to
    /// "python".
    public var runnerName: String {
        let lower = name.lowercased()
        return lower.hasPrefix("python") ? "python" : lower
    }

    /// The full command line, when argv could be read.
    public var commandLine: String {
        arguments.isEmpty ? path : arguments.joined(separator: " ")
    }
}
