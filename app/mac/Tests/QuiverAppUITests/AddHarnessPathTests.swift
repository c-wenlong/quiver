import Foundation
import Testing
@testable import QuiverApp

/// The Add harness sheet's "Not on PATH yet" hint checks the PATH it is
/// given (the login shell's, in the app), not the app's own launchd PATH.
struct AddHarnessPathTests {
    @Test func findsAnExecutableOnTheGivenPath() throws {
        let dir = try TempDir()
        // A name no real install has: the check also searches Homebrew's folders.
        let name = "quiver-test-\(UUID().uuidString)"
        let tool = dir.url.appending(path: name)
        try Data("#!/bin/sh\n".utf8).write(to: tool)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tool.path)
        #expect(AddHarnessSheet.onPath(name, path: "/nonexistent:\(dir.url.path)"))
        #expect(!AddHarnessSheet.onPath(name, path: "/usr/bin"))
    }

    /// A file that isn't executable is not a command.
    @Test func skipsAFileThatIsNotExecutable() throws {
        let dir = try TempDir()
        let name = "quiver-test-\(UUID().uuidString)"
        try Data("x".utf8).write(to: dir.url.appending(path: name))
        #expect(!AddHarnessSheet.onPath(name, path: dir.url.path))
    }
}
