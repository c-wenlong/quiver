import Foundation

/// A `UserDefaults` suite of its own that deletes itself (domain and
/// plist) when the last reference goes. The same helper as
/// `QuiverCoreTests/TestSupport.swift`; test targets can't share sources.
final class TestDefaults: UserDefaults, @unchecked Sendable {
    let suite: String

    init(_ prefix: String = "quiver-app-test") {
        suite = "\(prefix)-\(UUID().uuidString)"
        super.init(suiteName: suite)!
    }

    deinit {
        // Flush what the suite wrote, then delete its file. Emptying the
        // domain (removePersistentDomain) instead makes cfprefsd write an
        // empty plist back some time later, which outlives the test.
        CFPreferencesAppSynchronize(suite as CFString)
        try? FileManager.default.removeItem(at: FileManager.default.homeDirectoryForCurrentUser
            .appending(path: "Library/Preferences/\(suite).plist"))
    }
}
