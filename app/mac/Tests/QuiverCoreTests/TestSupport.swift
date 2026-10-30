import Foundation
@testable import QuiverCore

/// A `UserDefaults` suite of its own (`<prefix>-<uuid>`) that deletes
/// itself when the last reference goes, so no test leaves a plist in
/// `~/Library/Preferences`. Hand it to whatever takes `defaults:`; the
/// model holding it keeps it alive for as long as it is used.
final class TestDefaults: UserDefaults, @unchecked Sendable {
    let suite: String

    init(_ prefix: String = "quiver-test") {
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
