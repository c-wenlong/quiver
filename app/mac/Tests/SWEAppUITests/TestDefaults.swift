import Foundation

/// A `UserDefaults` suite of its own, written under this test process's
/// temp folder instead of `~/Library/Preferences`. The same helper as
/// `SWECoreTests/TestSupport.swift`; test targets can't share sources.
final class TestDefaults: UserDefaults, @unchecked Sendable {
    let suite: String

    init(_ prefix: String = "quiver-app-test") {
        suite = Self.folder.appending(path: "\(prefix)-\(UUID().uuidString)").path
        super.init(suiteName: suite)!
    }

    deinit { try? FileManager.default.removeItem(atPath: suite + ".plist") }

    /// One folder per test process, removed when it exits; anything
    /// cfprefsd writes later lands in the temp folder, never in Preferences.
    static let folder: URL = {
        let folder = FileManager.default.temporaryDirectory
            .appending(path: "quiver-test-defaults-\(getpid())", directoryHint: .isDirectory)
        atexit { try? FileManager.default.removeItem(at: TestDefaults.folder) }
        return folder
    }()
}
