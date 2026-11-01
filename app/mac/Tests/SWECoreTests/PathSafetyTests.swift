import Foundation
import Testing
@testable import SWECore

/// Path overrides come from the app's preferences, so any same-user
/// process can write them — the check is what stops such a write from
/// aiming SWE at a file a different user planted under /tmp or a
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

    /// Greptile: a clean-looking link under ~ can hop through a folder
    /// another user writes before reaching a good file — today /bin/ls,
    /// tomorrow a planted binary. Every hop's home has to pass, not just
    /// the written chain or the final target.
    @Test func aMiddleHopInAWorldWritableFolderIsRejected() throws {
        let temp = try TempDir()
        let shared = temp.url.appendingPathComponent("shared", isDirectory: true)
        try FileManager.default.createDirectory(at: shared, withIntermediateDirectories: true)
        let hop = shared.appendingPathComponent("hop")
        try FileManager.default.createSymbolicLink(atPath: hop.path, withDestinationPath: "/bin/ls")
        let link = temp.url.appendingPathComponent("swe")
        try FileManager.default.createSymbolicLink(atPath: link.path, withDestinationPath: hop.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o777], ofItemAtPath: shared.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: shared.path)
        }
        #expect(PathSafety.rejection(of: link.path) != nil)
    }

    /// A link chain that stays inside safe folders is still fine.
    @Test func aLinkChainThroughSafeFoldersIsTrusted() throws {
        let temp = try TempDir()
        let inner = temp.url.appendingPathComponent("inner")
        try FileManager.default.createSymbolicLink(atPath: inner.path, withDestinationPath: "/bin/ls")
        let outer = temp.url.appendingPathComponent("outer")
        try FileManager.default.createSymbolicLink(atPath: outer.path, withDestinationPath: inner.path)
        #expect(PathSafety.rejection(of: outer.path) == nil)
    }

    /// A link cycle has nothing to judge and nothing to loop on.
    @Test func aLinkCycleTerminates() throws {
        let temp = try TempDir()
        let a = temp.url.appendingPathComponent("a"), b = temp.url.appendingPathComponent("b")
        try FileManager.default.createSymbolicLink(atPath: a.path, withDestinationPath: b.path)
        try FileManager.default.createSymbolicLink(atPath: b.path, withDestinationPath: a.path)
        #expect(PathSafety.rejection(of: a.path) == nil)
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
