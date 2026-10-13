import Foundation
import Observation
import QuiverCore
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
    /// Set when macOS has notifications for Quiver turned off.
    private(set) var notificationsBlocked = false

    var notifyEnabled: Bool {
        didSet { UserDefaults.standard.set(notifyEnabled, forKey: "limitNotifications") }
    }

    @ObservationIgnored private var alerter: LimitAlerter
    @ObservationIgnored private let alertStateFile: URL
    @ObservationIgnored private let widgets: WidgetSnapshotStore
    @ObservationIgnored private var lastWidgetSnapshot: WidgetSnapshot?

    init(quiver: URL) {
        notifyEnabled = UserDefaults.standard.object(forKey: "limitNotifications") as? Bool ?? true
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
    /// summary's per-harness lines, `harnessColors` their hex colours.
    func widgetSnapshot(overview: OverviewModel?, selection: WidgetSelection,
                        harnessOrder: [String] = [], harnessColors: [String: String] = [:],
                        now: Date = .now) -> WidgetSnapshot {
        WidgetSnapshot.make(
            activity: overview?.activityRows ?? [], models: overview?.recentRows ?? [],
            limits: readings, selection: selection,
            harnessOrder: harnessOrder, harnessColors: harnessColors,
            now: now, calendar: .current)
    }

    /// Rewrite the widgets' file when what they show has changed, and ask
    /// WidgetKit to redraw.
    func writeWidgets(overview: OverviewModel?, selection: WidgetSelection,
                      harnessOrder: [String] = [], harnessColors: [String: String] = [:],
                      now: Date = .now) {
        let snapshot = widgetSnapshot(overview: overview, selection: selection,
                                      harnessOrder: harnessOrder, harnessColors: harnessColors,
                                      now: now)
        var comparable = snapshot
        comparable.generatedAt = lastWidgetSnapshot?.generatedAt ?? now
        guard comparable != lastWidgetSnapshot else { return }
        do {
            try widgets.write(snapshot)
            lastWidgetSnapshot = snapshot
            WidgetCenter.shared.reloadAllTimelines()
        } catch {
            // The widgets keep their last file; nothing else depends on it.
        }
    }

    func checkNotificationPermission() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        notificationsBlocked = settings.authorizationStatus == .denied
    }

    private func post(_ alert: LimitAlert, now: Date) {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        content.body = alert.body(now: now)
        content.threadIdentifier = "limits"
        // One identifier per window and kind: a repeat replaces, never stacks.
        let request = UNNotificationRequest(identifier: "\(alert.reading.id)|\(alert.kind.rawValue)",
                                            content: content, trigger: nil)
        Task {
            let center = UNUserNotificationCenter.current()
            let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
            notificationsBlocked = !granted
            guard granted else { return }
            try? await center.add(request)
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
