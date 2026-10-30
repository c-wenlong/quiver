import Foundation
import Testing

/// The helpers other tests lean on to stay off the real home.
@Suite struct TestSupportTests {
    @Test func aScratchSuiteIsWrittenUnderTheTempFolder() {
        let defaults = TestDefaults("support")
        defaults.set(42, forKey: "answer")
        #expect(UserDefaults(suiteName: defaults.suite)?.integer(forKey: "answer") == 42)
        // Flushed, the plist is on disk, and it is in this process's temp
        // folder rather than beside the user's own preferences.
        CFPreferencesAppSynchronize(defaults.suite as CFString)
        let file = URL(filePath: defaults.suite + ".plist")
        #expect(file.deletingLastPathComponent().path == TestDefaults.folder.path)
        #expect(FileManager.default.fileExists(atPath: file.path))
        let preferences = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Preferences/\(file.lastPathComponent)")
        #expect(!FileManager.default.fileExists(atPath: preferences.path))
    }
}
