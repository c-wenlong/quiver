import Foundation
import Testing

/// The helpers other tests lean on to stay off the real home.
@Suite struct TestSupportTests {
    @Test func aScratchSuiteLeavesNoPlistBehind() {
        var suite = ""
        do {
            let defaults = TestDefaults("support")
            suite = defaults.suite
            defaults.set(42, forKey: "answer")
            #expect(UserDefaults(suiteName: suite)?.integer(forKey: "answer") == 42)
        }
        let file = FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Preferences/\(suite).plist")
        #expect(!FileManager.default.fileExists(atPath: file.path))
    }
}
