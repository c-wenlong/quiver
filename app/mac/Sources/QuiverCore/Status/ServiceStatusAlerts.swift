import Foundation

/// One change worth a notification: a provider started or stopped being
/// affected.
public struct ServiceStatusAlert: Sendable, Equatable {
    public enum Kind: String, Sendable { case started, resolved }

    public var kind: Kind
    public var serviceID: String
    public var serviceName: String
    /// The state that triggered a `started` alert; operational for `resolved`.
    public var health: ServiceHealth
    /// The incident's name: the new one for `started`, the one bannered
    /// last for `resolved`. Nil when the page reports no incident.
    public var incident: String?
    public var pageURL: URL
}

/// Which provider incidents have been announced, and which providers are
/// down as far as a notification has said. Persisted so a relaunch during
/// an incident stays quiet. `swe remote serve` keeps the same state in
/// Python (`quiver.status.alerts`); keep the two in step.
public struct ServiceStatusAlertState: Codable, Equatable, Sendable {
    /// "service@incident" keys already announced, oldest first. A service
    /// affected with no open incident is keyed "service@status".
    public var delivered: [String] = []
    /// Services a `started` alert went out for and no `resolved` has
    /// followed, with the incident it named.
    public var down: [String: String] = [:]
    /// The first poll ran. It only records what is already open.
    public var primed = false

    public init() {}

    public init(from decoder: any Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        delivered = try box.decodeIfPresent([String].self, forKey: .delivered) ?? []
        down = try box.decodeIfPresent([String: String].self, forKey: .down) ?? [:]
        primed = try box.decodeIfPresent(Bool.self, forKey: .primed) ?? false
    }

    /// Plenty for a handful of providers' incidents; the oldest go first.
    static let cap = 200
}

/// The notification rule. Only the two provider groups notify, from a
/// minor incident up (the menu line's bar for them). Scheduled
/// maintenance never does. A service with no reading is left as it was:
/// an unreachable page is not an outage, and not a recovery either.
public enum ServiceStatusAlerts {
    /// Advance `state` by one poll and return what to announce. With
    /// `notify` false (the switch is off) the incidents are still
    /// recorded, so turning it on later doesn't announce old news.
    public static func step(_ state: inout ServiceStatusAlertState, statuses: [ServiceStatus],
                            notify: Bool) -> [ServiceStatusAlert] {
        let announce = notify && state.primed
        var alerts: [ServiceStatusAlert] = []
        for status in statuses where status.service.group.isProvider {
            guard let report = status.report else { continue }
            let id = status.service.id
            let generic = "\(id)@status"
            if report.health >= ServiceStatusSummary.threshold(for: status.service.group) {
                let incidents = report.incidents.filter { !$0.isMaintenance }
                let keyed = incidents.isEmpty
                    ? [(generic, report.summary)]
                    : incidents.map { ("\(id)@\($0.id)", Optional($0.name)) }
                let fresh = keyed.filter { !state.delivered.contains($0.0) }
                for (key, _) in fresh { state.delivered.append(key) }
                guard let first = fresh.first, announce else { continue }
                state.down[id] = first.1 ?? ""
                alerts.append(ServiceStatusAlert(
                    kind: .started, serviceID: id, serviceName: status.service.name,
                    health: report.health, incident: first.1, pageURL: status.service.pageURL))
            } else {
                // An incident-less blip has no id to remember, so its key
                // clears once the page is back to normal.
                state.delivered.removeAll { $0 == generic }
                guard let incident = state.down.removeValue(forKey: id), notify else { continue }
                alerts.append(ServiceStatusAlert(
                    kind: .resolved, serviceID: id, serviceName: status.service.name,
                    health: report.health, incident: incident.isEmpty ? nil : incident,
                    pageURL: status.service.pageURL))
            }
        }
        // A page the user stopped tracking can't resolve; let it go quietly.
        let tracked = Set(statuses.map(\.service.id))
        state.down = state.down.filter { tracked.contains($0.key) }
        if state.delivered.count > ServiceStatusAlertState.cap {
            state.delivered.removeFirst(state.delivered.count - ServiceStatusAlertState.cap)
        }
        state.primed = true
        return alerts
    }

    /// "Claude: minor incident" / "Claude is back to normal".
    public static func title(_ alert: ServiceStatusAlert) -> String {
        switch alert.kind {
        case .started: "\(alert.serviceName): \(alert.health.title.lowercased())"
        case .resolved: "\(alert.serviceName) is back to normal"
        }
    }

    /// The incident's name, or a fallback when the page named none.
    public static func body(_ alert: ServiceStatusAlert) -> String {
        switch alert.kind {
        case .started: alert.incident ?? "The status page reports a problem."
        case .resolved: alert.incident.map { "Resolved: \($0)" } ?? "The status page reports no open incident."
        }
    }
}
