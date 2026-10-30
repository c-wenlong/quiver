import Foundation

/// Files under `~/.quiver` that only this user should read: session
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

    /// Tighten what an older build left: `file` to 0600 and its folder to
    /// 0700, each only when it is there. A store calls this when it opens,
    /// so an old file isn't readable by others until the next save.
    static func tighten(_ file: URL) {
        let manager = FileManager.default
        let folder = file.deletingLastPathComponent().path
        if manager.fileExists(atPath: folder) {
            try? manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: folder)
        }
        if manager.fileExists(atPath: file.path) {
            try? manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        }
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
