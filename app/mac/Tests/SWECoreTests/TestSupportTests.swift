import Foundation
import Testing

/// The helpers other tests lean on to stay off the real home.
@Suite struct TestSupportTests {
    @Test(.timeLimit(.minutes(1))) func aScratchSuiteIsWrittenUnderTheTempFolder() async {
        let defaults = TestDefaults("support")
        defaults.set(42, forKey: "answer")
        #expect(UserDefaults(suiteName: defaults.suite)?.integer(forKey: "answer") == 42)
        // The plist lands in this process's temp folder, not beside the
        // user's own preferences. cfprefsd writes it in its own time (a
        // flush doesn't wait for the file under load), so wait for it.
        let file = URL(filePath: defaults.suite + ".plist")
        #expect(file.deletingLastPathComponent().path == TestDefaults.folder.path)
        let appeared = await fileAppears(file.lastPathComponent, in: TestDefaults.folder)
        // Held to here: its deinit deletes the plist being waited for.
        withExtendedLifetime(defaults) {}
        #expect(appeared)
        let preferences = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Preferences/\(file.lastPathComponent)")
        #expect(!FileManager.default.fileExists(atPath: preferences.path))
    }
}
