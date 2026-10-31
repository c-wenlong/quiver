import Darwin
import Foundation

/// The argv of a running process, read straight out of the kernel. Shared by
/// `ProcessSampler` (system stats) and the launch path (telling one desktop
/// app instance from another by its `--user-data-dir`).
public enum ProcessArguments {
    /// KERN_PROCARGS2 for `pid`; an empty array when the process exits
    /// mid-read or its arguments are unreadable, never an error.
    public static func of(pid: Int32) -> [String] {
        var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
        var size = 0
        guard sysctl(&mib, 3, nil, &size, nil, 0) == 0,
              size > MemoryLayout<Int32>.size else { return [] }
        var buffer = [UInt8](repeating: 0, count: size)
        guard sysctl(&mib, 3, &buffer, &size, nil, 0) == 0 else { return [] }
        return parse(buffer)
    }

    /// The KERN_PROCARGS2 buffer layout: an int32 argc, the exec path, NUL
    /// padding, then argc NUL-separated argv strings (followed by the
    /// environment, which this does not read).
    public static func parse(_ buffer: [UInt8]) -> [String] {
        let size = buffer.count
        guard size > MemoryLayout<Int32>.size else { return [] }
        let argc = Int(buffer.withUnsafeBytes { $0.loadUnaligned(as: Int32.self) })
        // A negative count would make the range below trap.
        guard argc > 0 else { return [] }
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
