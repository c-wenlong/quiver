import Foundation
import Observation
import QuiverCore
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

    init(quiver: URL, defaults: UserDefaults = .standard,
         post: (([ServiceStatusAlert]) async -> Void)? = nil) {
        self.defaults = defaults
        self.post = post ?? { alerts in await Self.postToSystem(alerts) }
        bannersEnabled = defaults.object(forKey: Self.defaultsKey) as? Bool ?? false
        stateFile = quiver.appending(path: "cache/service-status-notified.json")
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
    func observe(_ statuses: [ServiceStatus]) async {
        let before = state
        let alerts = ServiceStatusAlerts.step(&state, statuses: statuses, notify: bannersEnabled)
        if state != before { save() }
        if !alerts.isEmpty { await post(alerts) }
    }

    private static func postToSystem(_ alerts: [ServiceStatusAlert]) async {
        let center = UNUserNotificationCenter.current()
        guard (try? await center.requestAuthorization(options: [.alert, .sound])) == true else { return }
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
