import Foundation
import Synchronization
import Testing
@testable import SWECore

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
        // Made now rather than by cfprefsd's first write, so a test can
        // watch it for the plist arriving.
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
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

/// A value a test shares with a closure it hands to the code under test
/// (a counter, a clock, a switch). Reads and writes go through a lock, so
/// a closure called off the test's thread is no data race.
final class Box<Value: Sendable>: Sendable {
    private let state: Mutex<Value>
    init(_ value: Value) { state = Mutex(value) }
    var value: Value {
        get { state.withLock { $0 } }
        set { state.withLock { $0 = newValue } }
    }
}

/// For a test that takes a permission away to force a read or write
/// error: root ignores file modes, so there it would pass or fail for the
/// wrong reason.
let skippedAsRoot = Testing.ConditionTrait.disabled(
    if: geteuid() == 0, "root ignores file permissions, so the error never comes")

extension AccountDirectory {
    /// A directory with no profiles whose path lookups must never run,
    /// for a home of `/Users/k`.
    static var bare: AccountDirectory {
        AccountDirectory(profiles: [], paths: { _ in fatalError("no profile paths in this test") },
                         home: URL(fileURLWithPath: "/Users/k"))
    }
}
