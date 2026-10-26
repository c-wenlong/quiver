import Foundation
import Testing
@testable import QuiverCore

/// Path overrides come from the app's preferences, so any same-user
/// process can write them — the check is what stops such a write from
/// aiming Quiver at a file a different user planted under /tmp or a
/// shared folder.
@Suite struct PathSafetyTests {
    @Test func aRelativePathIsRejected() {
        #expect(PathSafety.rejection(of: "bin/swe") != nil)
        #expect(PathSafety.rejection(of: "") != nil)
    }

    @Test func anythingUnderTmpIsRejected() throws {
        // /tmp itself is other-writable, so a file that does not exist yet
        // is just as unsafe: another user can create it first.
        #expect(PathSafety.rejection(of: "/tmp/quiver-safety-\(UUID().uuidString)") != nil)
    }

    @Test func aSystemToolIsTrusted() {
        #expect(PathSafety.rejection(of: "/bin/ls") == nil)
    }

    @Test func aMissingFileUnderHomeIsTrusted() {
        let path = ("~/quiver-safety-\(UUID().uuidString)/file" as NSString)
        #expect(PathSafety.rejection(of: path as String) == nil)
    }

    @Test func aFileInAWorldWritableFolderIsRejected() throws {
        let temp = try TempDir()
        let shared = temp.url.appendingPathComponent("shared", isDirectory: true)
        try FileManager.default.createDirectory(at: shared, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o777], ofItemAtPath: shared.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shared.path) }
        let file = shared.appendingPathComponent("swe")
        try Data().write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        // The file itself is private, but the folder lets another user
        // swap it — the ancestor check is what catches this.
        #expect(PathSafety.rejection(of: file.path) != nil)
    }

    @Test func aWorldWritableExecutableIsRejected() throws {
        let temp = try TempDir()
        let file = temp.url.appendingPathComponent("swe")
        try Data().write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o777], ofItemAtPath: file.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path) }
        #expect(PathSafety.rejection(of: file.path) != nil)
    }
}

@Suite struct SweLocationOverrideTests {
    @Test func aMissingOrUnsafeOverrideFallsBack() {
        // No bundled swe in the test bundle, so the fallback is the bare
        // "swe" PATH lookup.
        #expect(SweLocation.executable(override: nil, bundled: nil) == "swe")
        #expect(SweLocation.executable(override: "   ", bundled: nil) == "swe")
        #expect(SweLocation.executable(override: "/no/such/swe-\(UUID().uuidString)", bundled: nil) == "swe")
        #expect(SweLocation.executable(override: "/tmp/swe", bundled: nil) == "swe")
        #expect(SweLocation.executable(override: "relative/swe", bundled: nil) == "swe")
        #expect(SweLocation.executable(override: "/tmp/swe", bundled: "/bundled/swe") == "/bundled/swe")
    }

    @Test func anExecutableOverrideWins() {
        #expect(SweLocation.executable(override: "/bin/ls", bundled: nil) == "/bin/ls")
    }
}
