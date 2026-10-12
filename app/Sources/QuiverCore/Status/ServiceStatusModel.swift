import Foundation
import Observation

/// The Service status page and the menu bar line.
@MainActor
@Observable
public final class ServiceStatusModel {
    public private(set) var statuses: [ServiceStatus]
    public private(set) var isLoading = false
    /// The built-in pages plus the ones the user added, in card order.
    public private(set) var services: [StatusService]

    private let service: ServiceStatusService
    private let store: any CustomStatusStoring
    private var generation = 0

    public init(service: ServiceStatusService, store: any CustomStatusStoring) {
        self.service = service
        self.store = store
        let all = StatusService.all + store.load()
        services = all
        statuses = all.map { ServiceStatus(service: $0, report: nil, checkedAt: nil, problem: nil, isStale: false) }
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

    /// Whether an address serves a readable status feed. The "Track a
    /// status page" sheet's Check button.
    public func probe(_ input: String) async -> ServiceStatusService.ProbeResult {
        await service.probe(input)
    }

    /// A page the user added. The card shows right away as unknown, then a
    /// load fills it in: the new id has no cache entry, so the page's own
    /// polling floors let it be fetched.
    @discardableResult
    public func add(name: String, pageURL: URL, group: StatusService.Group) async -> StatusService {
        let service = StatusService(
            id: StatusService.customID(for: name, taken: Set(services.map(\.id))),
            name: name, pageURL: pageURL,
            feed: .statuspage(pageURL.appending(path: "api/v2/summary.json")),
            group: group, isCustom: true)
        services.append(service)
        statuses.append(ServiceStatus(service: service, report: nil, checkedAt: nil, problem: nil, isStale: false))
        persist()
        await load(force: false)
        return service
    }

    /// Drop a page the user added; a built-in id is left alone.
    public func remove(id: String) {
        guard services.first(where: { $0.id == id })?.isCustom == true else { return }
        services.removeAll { $0.id == id }
        statuses.removeAll { $0.service.id == id }
        persist()
    }

    private func persist() {
        store.save(services.filter(\.isCustom))
    }

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
