import Foundation
import Testing
@testable import QuiverCore

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
        _ = GitHubHistory(url: old)
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
