import Foundation
import Observation
import SWECore
import UserNotifications

/// Banners when a model provider or agent platform starts or stops being
/// affected, read from the Service status poll. The rule and the
/// delivered set live in `ServiceStatusAlerts`; this owns the switch, the
/// state file and the poll loop, the way `GitHubNotifier` does for GitHub.
@MainActor
@Observable
final class ServiceStatusNotifier {
    /// Off until switched on in Settings. Off still records incidents, so
    /// switching on during one doesn't banner it late.
    var bannersEnabled: Bool {
        didSet { defaults.set(bannersEnabled, forKey: Self.defaultsKey) }
    }

    static let defaultsKey = "serviceStatusNotifications"

    @ObservationIgnored private let defaults: UserDefaults
    @ObservationIgnored private let stateFile: URL
    @ObservationIgnored private var state: ServiceStatusAlertState
    /// Sends the alerts; `postToSystem` in the app, a capture in tests.
    @ObservationIgnored private let post: ([ServiceStatusAlert]) async -> Void
    /// Whether macOS will show a banner (asking the first time); a test
    /// answers it directly.
    @ObservationIgnored private let authorized: () async -> Bool

    init(swe: URL, defaults: UserDefaults = .standard,
         authorized: (() async -> Bool)? = nil,
         post: (([ServiceStatusAlert]) async -> Void)? = nil) {
        self.defaults = defaults
        self.post = post ?? { alerts in await Self.postToSystem(alerts) }
        self.authorized = authorized ?? {
            (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])) == true
        }
        bannersEnabled = defaults.object(forKey: Self.defaultsKey) as? Bool ?? false
        stateFile = swe.appending(path: "cache/service-status-notified.json")
        state = (try? Data(contentsOf: stateFile))
            .flatMap { try? JSONDecoder().decode(ServiceStatusAlertState.self, from: $0) }
            ?? ServiceStatusAlertState()
    }

    /// Replaces `ServiceStatusModel.keepFresh` in the app: load, look,
    /// sleep. The service's five-minute floor still applies to the load.
    func keepFresh(model: ServiceStatusModel, every interval: Duration = .seconds(300)) async {
        while !Task.isCancelled {
            await model.load(force: false)
            await observe(model.statuses)
            do { try await Task.sleep(for: interval) } catch { return }
        }
    }

    /// One poll's statuses: advance the state, banner what changed, save.
    /// With banners blocked in System Settings the poll records silently,
    /// as with the switch off: an incident that couldn't banner must not
    /// owe a "back to normal" banner later.
    func observe(_ statuses: [ServiceStatus]) async {
        let notify = bannersEnabled ? await authorized() : false
        let before = state
        let alerts = ServiceStatusAlerts.step(&state, statuses: statuses, notify: notify)
        if state != before { save() }
        if !alerts.isEmpty { await post(alerts) }
    }

    private static func postToSystem(_ alerts: [ServiceStatusAlert]) async {
        let center = UNUserNotificationCenter.current()
        for alert in alerts {
            let content = UNMutableNotificationContent()
            content.title = ServiceStatusAlerts.title(alert)
            content.body = ServiceStatusAlerts.body(alert)
            content.sound = .default
            content.threadIdentifier = "service-status"
            content.userInfo = ["serviceStatus": true]
            // One banner per service: its resolve replaces its start.
            let request = UNNotificationRequest(
                identifier: "service-\(alert.serviceID)", content: content, trigger: nil)
            try? await center.add(request)
        }
    }

    private func save() {
        do {
            try FileManager.default.createDirectory(
                at: stateFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(state).write(to: stateFile, options: .atomic)
        } catch {
            // Worst case a relaunch repeats a banner.
        }
    }
}
