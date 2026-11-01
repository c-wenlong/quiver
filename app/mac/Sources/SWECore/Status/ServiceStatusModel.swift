import Foundation
import Observation

/// The Service status page and the menu bar line.
@MainActor
@Observable
public final class ServiceStatusModel {
    public private(set) var statuses: [ServiceStatus]
    public private(set) var isLoading = false
    /// The built-in pages — those the user did not remove, with their edits
    /// applied — followed by the ones the user added, in card order.
    public private(set) var services: [StatusService]

    private let service: ServiceStatusService
    private let store: any StatusServiceStoring
    private var prefs: StatusServicePrefs
    private var generation = 0

    public init(service: ServiceStatusService, store: any StatusServiceStoring) {
        self.service = service
        self.store = store
        let prefs = store.load()
        self.prefs = prefs
        let list = Self.list(prefs)
        services = list
        statuses = list.map { ServiceStatus(service: $0, report: nil, checkedAt: nil, problem: nil, isStale: false) }
    }

    /// The services the prefs describe: built-ins in catalog order minus
    /// the hidden ones, overrides applied, then the customs.
    private static func list(_ prefs: StatusServicePrefs) -> [StatusService] {
        StatusService.all
            .filter { !prefs.hidden.contains($0.id) }
            .map { $0.applying(prefs.overrides[$0.id]) }
            + prefs.custom
    }

    /// Rebuilds `services` from the prefs after a write, keeping each
    /// id's current reading (a page a load was out for keeps its row, a
    /// removed one stays gone) and pointing it at the edited service.
    private func resync() {
        services = Self.list(prefs)
        let kept = Dictionary(statuses.map { ($0.service.id, $0) }, uniquingKeysWith: { first, _ in first })
        statuses = services.map { service in
            var status = kept[service.id]
                ?? ServiceStatus(service: service, report: nil, checkedAt: nil, problem: nil, isStale: false)
            status.service = service
            return status
        }
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

    /// Whether the card is a built-in the user edited — the "Reset" menu
    /// item's condition.
    public func isEdited(_ id: String) -> Bool { prefs.overrides[id] != nil }

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
    public func add(name: String, pageURL: URL, feedURL: URL, group: StatusService.Group) async -> StatusService {
        let service = StatusService(
            id: StatusService.customID(for: name, taken: Set(services.map(\.id))),
            name: name, pageURL: pageURL,
            feed: .statuspage(feedURL),
            group: group, isCustom: true)
        prefs.custom.append(service)
        persist()
        resync()
        await load(force: false)
        return service
    }

    /// A catalogue row's one-click add. For a built-in's row it restores
    /// the catalog entry — a removed one comes back, an edited one loses
    /// its override — since the catalogue's `entry.id` is the built-in's.
    /// Anything else lands as a custom page, as `add` writes it.
    @discardableResult
    public func track(_ entry: StatusService) async -> StatusService {
        guard StatusService.all.contains(where: { $0.id == entry.id }) else {
            return await add(name: entry.name, pageURL: entry.pageURL, feedURL: entry.feedURL, group: entry.group)
        }
        await reset(id: entry.id)
        return entry
    }

    /// The sheet's Save on an existing card. A custom page is rewritten;
    /// a built-in's difference from the catalog is kept as an override, so
    /// a built-in edited back to its default reads as never touched. When
    /// the feed moved the poll cache is dropped, so the new page is read
    /// on the load this triggers.
    public func update(id: String, name: String, pageURL: URL, feedURL: URL?,
                       group: StatusService.Group) async {
        guard let current = services.first(where: { $0.id == id }) else { return }
        var edited = current
        edited.name = name
        edited.pageURL = pageURL
        edited.group = group
        if case .statuspage = current.feed, let feedURL { edited.feed = .statuspage(feedURL) }
        if current.isCustom {
            guard let index = prefs.custom.firstIndex(where: { $0.id == id }) else { return }
            prefs.custom[index] = edited
        } else if let base = StatusService.all.first(where: { $0.id == id }) {
            var override = StatusServicePrefs.Edit()
            if edited.name != base.name { override.name = name }
            if edited.pageURL != base.pageURL { override.pageURL = pageURL }
            if edited.group != base.group { override.group = group }
            // A page override without a feed would make `applying` derive
            // api/v2/summary.json — wrong for feeds filed elsewhere, like
            // Cursor's /api/compat — so the checked feed is kept with it.
            if case .statuspage(let url) = edited.feed,
               edited.feed != base.feed || override.pageURL != nil {
                override.feedURL = url
            }
            prefs.overrides[id] = override.isEmpty ? nil : override
        }
        persist()
        resync()
        if edited.feed != current.feed { await service.forget(id: id) }
        await load(force: false)
    }

    /// Drop a page: a custom one is deleted, a built-in one is hidden and
    /// its override let go so a later catalogue add restores the catalog's.
    /// The id's poll cache goes too, so a page re-added later cannot
    /// inherit the old reading.
    public func remove(id: String) async {
        guard let current = services.first(where: { $0.id == id }) else { return }
        if current.isCustom {
            prefs.custom.removeAll { $0.id == id }
        } else {
            prefs.hidden.insert(id)
            prefs.overrides.removeValue(forKey: id)
        }
        persist()
        resync()
        await service.forget(id: id)
    }

    /// Restore a built-in to the catalog's entry: the override goes, and a
    /// hidden one comes back at its catalog spot. The menu's "Reset" and
    /// the catalogue's Add on a built-in row both land here. A custom or
    /// unknown id is left alone.
    public func reset(id: String) async {
        guard StatusService.all.contains(where: { $0.id == id }) else { return }
        let changed = prefs.overrides.removeValue(forKey: id) != nil
        let hidden = prefs.hidden.remove(id) != nil
        if changed || hidden {
            persist()
            resync()
            await service.forget(id: id)
            await load(force: false)
        }
    }

    private func persist() {
        store.save(prefs)
    }

    public func load(force: Bool) async {
        generation += 1
        let mine = generation
        if statuses.allSatisfy({ $0.checkedAt == nil }) {
            statuses = rebuilt(from: await service.cached(for: services))
        }
        isLoading = true
        defer { if mine == generation { isLoading = false } }
        let fresh = await service.statuses(for: services, force: force)
        guard mine == generation else { return }
        statuses = rebuilt(from: fresh)
    }

    /// `results` mapped onto the services list as it stands now: a page
    /// added while the call was out keeps its row, a page removed while it
    /// was out stays gone.
    private func rebuilt(from results: [ServiceStatus]) -> [ServiceStatus] {
        let fetched = Dictionary(results.map { ($0.service.id, $0) }, uniquingKeysWith: { first, _ in first })
        let kept = Dictionary(statuses.map { ($0.service.id, $0) }, uniquingKeysWith: { first, _ in first })
        return services.map {
            fetched[$0.id] ?? kept[$0.id]
                ?? ServiceStatus(service: $0, report: nil, checkedAt: nil, problem: nil, isStale: false)
        }
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
