import Foundation

/// An exclusive `flock` held until `release()`, shared by every process that
/// locks the same file.
final class FileLock {
    private var fd: Int32

    init(at url: URL) throws {
        fd = open(url.path, O_RDWR | O_CREAT, 0o600)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        guard flock(fd, LOCK_EX) == 0 else {
            let code = errno
            close(fd)
            fd = -1
            throw POSIXError(POSIXErrorCode(rawValue: code) ?? .EIO)
        }
    }

    func release() {
        guard fd >= 0 else { return }
        flock(fd, LOCK_UN)
        close(fd)
        fd = -1
    }

    deinit { release() }
}

extension FileLock {
    /// Runs `body` holding an exclusive lock on `<url>.lock`, after creating
    /// `url`'s folder (0700). For a read-change-write another running copy
    /// may do at once: an atomic rename alone would let it write back the
    /// file it read before this save. If the lock can't be taken, `body`
    /// still runs, unlocked, rather than dropping the write.
    static func whileHeld(beside url: URL, _ body: () -> Void) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700])
        let lock = try? FileLock(at: url.appendingPathExtension("lock"))
        defer { lock?.release() }
        body()
    }
}
