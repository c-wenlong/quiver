import Foundation
import Testing
@testable import QuiverApp

/// The Add harness sheet's "Not on PATH yet" hint checks the PATH it is
/// given (the login shell's, in the app), not the app's own launchd PATH.
struct AddHarnessPathTests {
    @Test func findsAnExecutableOnTheGivenPath() throws {
        let dir = try TempDir()
        let tool = dir.url.appending(path: "mycli")
        try Data("#!/bin/sh\n".utf8).write(to: tool)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tool.path)
        #expect(AddHarnessSheet.onPath("mycli", path: "/nonexistent:\(dir.url.path)"))
        #expect(!AddHarnessSheet.onPath("mycli", path: "/usr/bin"))
    }

    /// A file that isn't executable is not a command.
    @Test func skipsAFileThatIsNotExecutable() throws {
        let dir = try TempDir()
        try Data("x".utf8).write(to: dir.url.appending(path: "notes"))
        #expect(!AddHarnessSheet.onPath("notes", path: dir.url.path))
    }
}
