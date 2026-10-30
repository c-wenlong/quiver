import Foundation
@testable import QuiverCore

/// A `UserDefaults` suite of its own for one test, kept out of
/// `~/Library/Preferences`. Its name is an absolute path in this test
/// process's own temp folder, so cfprefsd writes it there as
/// `<suite>.plist` instead of beside the user's real preferences. The
/// plist goes when the last reference goes, and the folder when the
/// process exits. Hand it to whatever takes `defaults:`; the model
/// holding it keeps it alive for as long as it is used.
///
/// A named suite (`UserDefaults(suiteName: "x-uuid")`) can't be cleaned up
/// reliably: `removePersistentDomain(forName:)` leaves an empty plist, and
/// cfprefsd can write a deleted file back seconds later.
final class TestDefaults: UserDefaults, @unchecked Sendable {
    let suite: String

    init(_ prefix: String = "quiver-test") {
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

extension APIKeysModel {
    /// A model that reads nothing from the machine running the tests: the
    /// shell-sourcing check looks in `home` (a temp folder with no startup
    /// file) as zsh with an empty process environment, and the check salt
    /// comes from a suite of its own. The real defaults would read the
    /// developer's `~/.zshrc` and `$ZDOTDIR`.
    @MainActor
    static func isolated(store: APIKeysStore, shell: ShellEnvironmentReading,
                         checker: APIKeyChecker = APIKeyChecker(transport: FakeTransport()),
                         checkCache: APIKeyCheckStore,
                         defaults: UserDefaults = TestDefaults("keys"),
                         home: URL) -> APIKeysModel {
        APIKeysModel(store: store, shell: shell, checker: checker, checkCache: checkCache,
                     defaults: defaults, shellName: "/bin/zsh", home: home, environment: [:])
    }
}
