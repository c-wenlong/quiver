import Foundation
import Observation
import OSLog
import SWECore
import UserNotifications
import WidgetKit

/// Every limit the app knows, kept current for the menu bar, and the side
/// effects of a change: limit notifications and the widgets' snapshot file.
@MainActor
@Observable
final class LimitsMonitor {
    /// Most used first.
    private(set) var readings: [LimitReading] = []
    /// What the status item shows.
    private(set) var tightest: LimitReading?
    /// Set when macOS has notifications for SWE turned off.
    private(set) var notificationsBlocked = false

    /// Off until the user turns it on: notifications are opt-in.
    var notifyEnabled: Bool {
        didSet { UserDefaults.standard.set(notifyEnabled, forKey: "limitNotifications") }
    }

    /// The "Refresh only what failed for" window, in minutes: inside it a
    /// Refresh press re-checks only the usage that failed.
    var refreshFreshMinutes: Int {
        didSet {
            let clamped = RefreshFreshWindow.minutes(refreshFreshMinutes)
            if clamped != refreshFreshMinutes { refreshFreshMinutes = clamped }
            UserDefaults.standard.set(clamped, forKey: "refreshFreshMinutes")
        }
    }
    /// The same window in seconds, for `UsageService` and swe's `--cache-ttl`.
    var refreshFreshSeconds: TimeInterval {
        RefreshFreshWindow.seconds(refreshFreshMinutes)
    }

    @ObservationIgnored private var alerter: LimitAlerter
    @ObservationIgnored private let alertStateFile: URL
    @ObservationIgnored private let widgets: WidgetSnapshotStore
    @ObservationIgnored private var lastWidgetSnapshot: WidgetSnapshot?

    init(quiver: URL) {
        notifyEnabled = UserDefaults.standard.object(forKey: "limitNotifications") as? Bool ?? false
        refreshFreshMinutes = RefreshFreshWindow.minutes(
            UserDefaults.standard.object(forKey: "refreshFreshMinutes") as? Int
                ?? RefreshFreshWindow.defaultMinutes)
        alertStateFile = quiver.appending(path: "cache/app-limit-alerts.json")
        widgets = WidgetSnapshotStore(file: WidgetSnapshotStore.defaultFile(home: quiver.deletingLastPathComponent()))
        let saved = (try? Data(contentsOf: alertStateFile))
            .flatMap { try? JSONDecoder().decode(LimitAlerter.State.self, from: $0) }
        alerter = LimitAlerter(state: saved ?? .init())
    }

    /// Take in the latest readings: recompute the tightest, notify about
    /// crossings and resets, and save what was notified.
    func update(_ all: [LimitReading], now: Date = .now) {
        let sorted = LimitReading.byPressure(all, now: now)
        if sorted != readings { readings = sorted }
        let tight = LimitReading.tightest(all, now: now)
        if tight != tightest { tightest = tight }

        let before = alerter.state
        let alerts = alerter.evaluate(all, now: now)
        if alerter.state != before { saveAlertState() }
        // Evaluated even when switched off, so turning it on later doesn't
        // announce every window that crossed in the meantime.
        if notifyEnabled { alerts.forEach { post($0, now: now) } }
    }

    /// The snapshot the widgets would show now, under `selection`. The
    /// Desktop widgets tab previews this, so it matches what a settings
    /// change writes. `harnessOrder` is the Settings order for the
    /// summary's per-harness lines, `harnessColors` their hex colours,
    /// `compute` the Compute widget's already-filtered section.
    func widgetSnapshot(overview: OverviewModel?, selection: WidgetSelection,
                        harnessOrder: [String] = [], harnessColors: [String: String] = [:],
                        compute: WidgetSnapshot.ComputeSection? = nil,
                        now: Date = .now) -> WidgetSnapshot {
        WidgetSnapshot.make(
            activity: overview?.activityRows ?? [], models: overview?.recentRows ?? [],
            limits: readings, compute: compute, selection: selection,
            harnessOrder: harnessOrder, harnessColors: harnessColors,
            now: now, calendar: .current)
    }

    /// The closest two WidgetKit pushes may sit. The file takes every
    /// write, but each `reloadAllTimelines` spends a little of the day's
    /// reload budget: one per change spent it inside a few hours, and the
    /// widgets froze on their last timeline entry ("45m ago" forever).
    /// The timeline's own reads keep the widgets fresh between pushes.
    static let reloadFloor: TimeInterval = 15 * 60
    @ObservationIgnored private var lastWidgetReloadAt: Date?

    /// Whether a write should also ask WidgetKit to redraw: on the first
    /// write, on a forced one (a settings change the user just made), or
    /// once the floor has passed since the last push.
    func shouldPushWidgets(force: Bool, now: Date) -> Bool {
        if force { return true }
        guard let last = lastWidgetReloadAt else { return true }
        return now.timeIntervalSince(last) >= Self.reloadFloor
    }

    /// Rewrite the widgets' file when what they show has changed, and ask
    /// WidgetKit to redraw, throttled so the reload budget lasts the day.
    func writeWidgets(overview: OverviewModel?, selection: WidgetSelection,
                      harnessOrder: [String] = [], harnessColors: [String: String] = [:],
                      compute: WidgetSnapshot.ComputeSection? = nil,
                      force: Bool = false, now: Date = .now) {
        let snapshot = widgetSnapshot(overview: overview, selection: selection,
                                      harnessOrder: harnessOrder, harnessColors: harnessColors,
                                      compute: compute, now: now)
        var comparable = snapshot
        comparable.generatedAt = lastWidgetSnapshot?.generatedAt ?? now
        guard comparable != lastWidgetSnapshot else { return }
        do {
            try widgets.write(snapshot)
            lastWidgetSnapshot = snapshot
            if shouldPushWidgets(force: force, now: now) {
                WidgetCenter.shared.reloadAllTimelines()
                lastWidgetReloadAt = now
            }
        } catch {
            // The widgets keep their last file; nothing else depends on it.
        }
    }

    func checkNotificationPermission() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        notificationStatus = settings.authorizationStatus
        notificationsBlocked = settings.authorizationStatus == .denied
    }

    /// macOS's answer for SWE, as of the last check. The setup window
    /// shows Allow, Allowed or Open System Settings from it.
    private(set) var notificationStatus: UNAuthorizationStatus = .notDetermined

    /// Ask macOS now, from a button, rather than at the first 90% crossing
    /// with no context. Asks only once; after a no, System Settings is the
    /// only way back.
    func requestNotifications() async {
        do {
            _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
            notificationRequestError = nil
        } catch {
            Self.log.error("Notification request failed: \(error.localizedDescription, privacy: .public)")
            notificationRequestError = error.localizedDescription
        }
        await checkNotificationPermission()
    }

    /// Why the last request to macOS failed, or nil if it ran. A no from the
    /// user is not an error: that shows up as a `.denied` status instead.
    private(set) var notificationRequestError: String?

    @ObservationIgnored
    private static let log = Logger(subsystem: "com.cwenlong.swe", category: "notifications")

    /// A sample of what a limit notification looks like.
    func sendTestNotification() async {
        await requestNotifications()
        guard notificationStatus == .authorized || notificationStatus == .provisional else { return }
        let content = UNMutableNotificationContent()
        content.title = "Claude is at 90% of its 5-hour limit"
        content.body = "This is a test. SWE tells you like this when a limit nears or resets."
        content.sound = .default
        content.threadIdentifier = "limits"
        try? await UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "test", content: content, trigger: nil))
    }

    private func post(_ alert: LimitAlert, now: Date) {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = alert.body(now: now)
        // As the test notification plays it, and as the authorization
        // below asks to.
        content.sound = .default
        content.threadIdentifier = "limits"
        // One identifier per window and kind: a repeat replaces, never stacks.
        let request = UNNotificationRequest(identifier: "\(alert.reading.id)|\(alert.kind.rawValue)",
                                            content: content, trigger: nil)
        Task {
            let center = UNUserNotificationCenter.current()
            var granted = false
            do {
                granted = try await center.requestAuthorization(options: [.alert, .sound])
            } catch {
                Self.log.error("Alert authorization failed: \(error.localizedDescription, privacy: .public)")
            }
            notificationsBlocked = !granted
            guard granted else { return }
            do {
                try await center.add(request)
            } catch {
                Self.log.error("Posting alert failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    private func saveAlertState() {
        do {
            try FileManager.default.createDirectory(at: alertStateFile.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try JSONEncoder().encode(alerter.state).write(to: alertStateFile, options: .atomic)
        } catch {
            // Worst case a relaunch repeats a notification.
        }
    }
}
