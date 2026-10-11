import Foundation
import Observation

/// The Service status page and the menu bar line.
@MainActor
@Observable
public final class ServiceStatusModel {
    public private(set) var statuses: [ServiceStatus]
    public private(set) var isLoading = false

    private let service: ServiceStatusService
    private let services: [StatusService]
    private var generation = 0

    public init(service: ServiceStatusService, services: [StatusService] = StatusService.all) {
        self.service = service
        self.services = services
        statuses = services.map { ServiceStatus(service: $0, report: nil, checkedAt: nil, problem: nil, isStale: false) }
    }

    /// Statuses grouped for the page, in catalog order.
    public var groups: [(group: StatusService.Group, statuses: [ServiceStatus])] {
        StatusService.Group.allCases.compactMap { group in
            let members = statuses.filter { $0.service.group == group }
            return members.isEmpty ? nil : (group, members)
        }
    }

    /// The most recent answer from any page.
    public var lastChecked: Date? { statuses.compactMap(\.checkedAt).max() }

    public var menuLine: String? { ServiceStatusSummary.menuLine(statuses) }
    /// The services `menuLine` names, worst first.
    public var affected: [StatusService] { ServiceStatusSummary.affected(statuses).map(\.service) }
    public var worst: ServiceHealth? { ServiceStatusSummary.worst(statuses) }

    public func load(force: Bool) async {
        generation += 1
        let mine = generation
        if statuses.allSatisfy({ $0.checkedAt == nil }) {
            statuses = await service.cached(for: services)
        }
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        let fresh = await service.statuses(for: services, force: force)
        guard mine == generation else { return }
        statuses = fresh
    }

    /// Check on a timer until the calling task is cancelled. The service's
    /// own five-minute floor still applies, so a shorter interval only
    /// re-reads the cache.
    public func keepFresh(every interval: Duration = .seconds(300)) async {
        while !Task.isCancelled {
            await load(force: false)
            do {
                try await Task.sleep(for: interval)
            } catch {
                return
            }
        }
    }
}
