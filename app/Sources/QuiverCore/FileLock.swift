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
