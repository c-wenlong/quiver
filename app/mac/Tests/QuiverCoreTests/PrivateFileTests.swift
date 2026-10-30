import Foundation
import Testing
@testable import QuiverCore

/// A transport the test never reaches: the store only opens.
private struct NoHTTP: HTTPTransport {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        throw URLError(.notConnectedToInternet)
    }
}

/// Files only this user should read are 0600 in a 0700 folder, and ones
/// an older build left 0644 / 0755 are tightened.
@Suite struct PrivateFileTests {
    static func mode(_ url: URL) throws -> Int {
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        return (attributes[.posixPermissions] as? NSNumber)?.intValue ?? -1
    }

    private func looseFolder(_ dir: TempDir, _ name: String) throws -> URL {
        let folder = dir.path(name)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o755])
        return folder
    }

    @Test func aWriteReplacesALooseFileWithAPrivateOne() throws {
        let dir = try TempDir()
        let folder = try looseFolder(dir, "data")
        let url = folder.appending(path: "x.json")
        try Data("old".utf8).write(to: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: url.path)
        try PrivateFile.write(Data("new".utf8), to: url)
        #expect(try String(contentsOf: url, encoding: .utf8) == "new")
        #expect(try Self.mode(url) == 0o600)
        #expect(try Self.mode(folder) == 0o700)
        #expect(try FileManager.default.contentsOfDirectory(atPath: folder.path) == ["x.json"])
    }

    @Test func harnessAccountNamesArePrivate() throws {
        let dir = try TempDir()
        let file = try looseFolder(dir, "accounts").appending(path: "harness-names.json")
        try HarnessAccountNameStore(file: file).setName("Work", for: "claude")
        #expect(try Self.mode(file) == 0o600)
        #expect(try Self.mode(file.deletingLastPathComponent()) == 0o700)
    }

    @Test func gitHubHistoryIsPrivateAndTightensAnOldDatabase() throws {
        let dir = try TempDir()
        let fresh = dir.path("fresh/github-history.db")
        _ = GitHubHistory(url: fresh)
        #expect(try Self.mode(fresh) == 0o600)
        #expect(try Self.mode(fresh.deletingLastPathComponent()) == 0o700)

        let old = try looseFolder(dir, "cache").appending(path: "github-history.db")
        // Kept open, so SQLite doesn't remove the -wal on a clean close
        // before its mode is loosened below.
        let first = GitHubHistory(url: old)
        try withExtendedLifetime(first) {
            for path in [old.path, old.path + "-wal"] {
                try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: path)
            }
            try FileManager.default.setAttributes([.posixPermissions: 0o755],
                                                  ofItemAtPath: old.deletingLastPathComponent().path)
            let reopened = GitHubHistory(url: old)
            #expect(reopened.openError == nil)
            #expect(try Self.mode(old) == 0o600)
            #expect(try Self.mode(URL(fileURLWithPath: old.path + "-wal")) == 0o600)
            #expect(try Self.mode(old.deletingLastPathComponent()) == 0o700)
        }
    }

    /// Tightening only takes access away: a file its owner locked stays
    /// locked (a store must still fail to read it, not overwrite it), and
    /// a read-only one stays read-only.
    @Test func tighteningNeverAddsAccess() throws {
        let dir = try TempDir()
        let folder = dir.path("private")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        for (before, after) in [(0o000, 0o000), (0o400, 0o400), (0o644, 0o600), (0o640, 0o600), (0o604, 0o600)] {
            let file = folder.appending(path: "f\(before)")
            try Data("x".utf8).write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: before], ofItemAtPath: file.path)
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path) }
            PrivateFile.tighten(file)
            #expect(try Self.mode(file) == after, "\(String(before, radix: 8))")
        }
        // The folder too: only group and other bits come off.
        for (before, after) in [(0o500, 0o500), (0o755, 0o700), (0o711, 0o700)] {
            let locked = dir.path("folder\(before)")
            try FileManager.default.createDirectory(at: locked, withIntermediateDirectories: true)
            try FileManager.default.setAttributes([.posixPermissions: before], ofItemAtPath: locked.path)
            defer { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: locked.path) }
            PrivateFile.tighten(locked.appending(path: "store.json"))
            #expect(try Self.mode(locked) == after, "folder \(String(before, radix: 8))")
        }
    }

    /// An old loose file is tightened when its store opens, not only at
    /// the next save.
    @Test func storesTightenOldFilesWhenTheyOpen() async throws {
        let dir = try TempDir()
        let names = try looseFolder(dir, "accounts").appending(path: "harness-names.json")
        let devin = try looseFolder(dir, "devin-cloud")
        let sessions = devin.appending(path: "sessions.json")
        for file in [names, sessions] {
            try Data("{}".utf8).write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: file.path)
        }
        _ = HarnessAccountNameStore(file: names)
        _ = DevinCloudSync(directory: devin, credentials: { nil }, transport: NoHTTP())
        for file in [names, sessions] {
            #expect(try Self.mode(file) == 0o600)
            #expect(try Self.mode(file.deletingLastPathComponent()) == 0o700)
        }
    }
}
