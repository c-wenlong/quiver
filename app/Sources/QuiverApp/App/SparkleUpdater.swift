import QuiverCore
import Sparkle

/// Sparkle's updater. Only a prod build has a feed to check (dev updates go
/// through Settings > Rebuild), so the controller is created lazily by
/// `start()` once the app has launched and the channel is known prod.
@MainActor
final class AppUpdater {
    static let shared = AppUpdater()
    private var controller: SPUStandardUpdaterController?

    private init() {}

    /// Start scheduled checks. A no-op for a dev build: its Info.plist has
    /// no SUFeedURL/SUPublicEDKey, and dev machines update with Rebuild.
    func start() {
        guard controller == nil,
              AppModel.shared.updates.build.channel == .prod else { return }
        controller = SPUStandardUpdaterController(
            startingUpdater: true, updaterDelegate: nil, userDriverDelegate: nil)
    }

    /// nil until `start()` ran on a prod build; Settings gates its
    /// controls on the same condition, so callers never see nil there.
    private var updater: SPUUpdater? { controller?.updater }

    var canCheckForUpdates: Bool { updater?.canCheckForUpdates ?? false }

    var automaticallyChecksForUpdates: Bool {
        get { updater?.automaticallyChecksForUpdates ?? false }
        set { updater?.automaticallyChecksForUpdates = newValue }
    }

    func checkForUpdates() {
        controller?.checkForUpdates(nil)
    }
}
