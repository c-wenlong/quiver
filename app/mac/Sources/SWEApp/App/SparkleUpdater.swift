import Observation
import SWECore
import Sparkle

/// Sparkle's updater. Only a prod build has a feed to check (dev updates go
/// through Settings > Rebuild), so the controller is created lazily by
/// `start()` once the app has launched and the channel is known prod.
///
/// `canCheckForUpdates` and `automaticallyChecksForUpdates` mirror the
/// SPUUpdater's properties into @Observable storage: Sparkle flips them
/// behind SwiftUI's back, and a plain passthrough leaves the Settings
/// switch and button frozen at launch values.
@MainActor
@Observable
final class AppUpdater {
    static let shared = AppUpdater()

    private(set) var canCheckForUpdates = false
    /// Mirrored both ways: the Settings switch writes through to the
    /// updater, `start()` seeds it from the user's last choice.
    var automaticallyChecksForUpdates = false {
        didSet {
            controller?.updater.automaticallyChecksForUpdates = automaticallyChecksForUpdates
        }
    }

    private var controller: SPUStandardUpdaterController?
    /// updaterDelegate is a weak reference, so the delegate object needs
    /// holding here or the update-cycle callback never arrives.
    private var delegate: Delegate?

    private init() {}

    /// Start scheduled checks. A no-op for a dev build: its Info.plist has
    /// no SUFeedURL/SUPublicEDKey, and dev machines update with Rebuild.
    func start() {
        guard controller == nil,
              AppModel.shared.updates.build.channel == .prod else { return }
        let delegate = Delegate()
        delegate.onCycleFinished = { [weak self] in self?.refreshState() }
        controller = SPUStandardUpdaterController(
            startingUpdater: true, updaterDelegate: delegate, userDriverDelegate: nil)
        self.delegate = delegate
        refreshState()
    }

    func checkForUpdates() {
        controller?.checkForUpdates(nil)
        refreshState()
    }

    private func refreshState() {
        guard let updater = controller?.updater else { return }
        canCheckForUpdates = updater.canCheckForUpdates
        automaticallyChecksForUpdates = updater.automaticallyChecksForUpdates
    }

    /// Lets the UI notice when an update cycle ends — automatic or manual —
    /// and `canCheckForUpdates` goes back to true.
    private final class Delegate: NSObject, SPUUpdaterDelegate {
        var onCycleFinished: (@MainActor () -> Void)?

        func updater(_ updater: SPUUpdater,
                     didFinishUpdateCycleFor updateCheck: SPUUpdateCheck,
                     error: (any Error)?) {
            let onCycleFinished = self.onCycleFinished
            Task { @MainActor in onCycleFinished?() }
        }
    }
}
