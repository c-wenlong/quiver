import Foundation

/// Files under `~/.swe` that only this user should read: session
/// titles, account names, pull request history. Their folder is 0700 and
/// the file 0600 from the moment it exists.
enum PrivateFile {
    /// Make `folder` (and any missing parents) and set it to 0700, even
    /// when it was already there: `createDirectory`'s attributes reach
    /// only a folder it creates, and older builds made these 0755.
    static func folder(_ folder: URL) throws {
        let manager = FileManager.default
        try manager.createDirectory(at: folder, withIntermediateDirectories: true,
                                    attributes: [.posixPermissions: 0o700])
        try? manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: folder.path)
    }

    /// Tighten what an older build left: take group and other access off
    /// `file` and its folder, each only when it is there. A store calls
    /// this when it opens, so an old file isn't readable by others until
    /// the next save. Only bits are removed, never added: a file its owner
    /// locked (mode 0) stays locked, and a store reading it still fails
    /// rather than reading as empty and overwriting it.
    static func tighten(_ file: URL) {
        removeGroupAndOther(file.deletingLastPathComponent().path)
        removeGroupAndOther(file.path)
    }

    private static func removeGroupAndOther(_ path: String) {
        let manager = FileManager.default
        guard let mode = (try? manager.attributesOfItem(atPath: path))?[.posixPermissions] as? Int,
              mode & 0o077 != 0 else { return }
        try? manager.setAttributes([.posixPermissions: mode & ~0o077], ofItemAtPath: path)
    }

    /// Write `data` to `url` atomically: created 0600 under a temporary
    /// name, then renamed into place, so it is never readable by others
    /// even for a moment, and a file an older build left 0644 is replaced
    /// by a private one.
    static func write(_ data: Data, to url: URL) throws {
        let folder = url.deletingLastPathComponent()
        try Self.folder(folder)
        let temporary = folder.appending(path: ".\(url.lastPathComponent).\(UUID().uuidString).tmp")
        guard FileManager.default.createFile(atPath: temporary.path, contents: data,
                                             attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown, userInfo: [NSFilePathErrorKey: url.path])
        }
        guard rename(temporary.path, url.path) == 0 else {
            let error = POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            try? FileManager.default.removeItem(at: temporary)
            throw error
        }
    }
}
