import Foundation
import Observation

/// The pull-requests page's data: one fetch per open, pull to refresh.
/// Kept out of `AppModel`'s 15 s poll — PRs change slowly, and the hub
/// already serves them from its one-minute `gh` cache.
@MainActor @Observable
final class PullsModel {
    /// The page's filter chip, kept here so it outlives the view.
    var filter: PullFilter = .all
    private(set) var repos: [PullRepo] = []
    private(set) var machine: String?
    private(set) var machineId: String?
    private(set) var envBlocked: String?
    private(set) var login: String?
    private(set) var error: String?
    private(set) var loaded = false
    /// Bumped per load: an on-open fetch and a pull-to-refresh can
    /// overlap, and only the newer one's reply may land.
    @ObservationIgnored private var generation = 0

    func load(client: HubClient?, refresh: Bool = false) async {
        guard let client else { return }
        generation += 1
        let gen = generation
        do {
            let body = try await client.pulls(refresh: refresh)
            guard gen == generation else { return }
            repos = body.repos
            machine = body.machine
            machineId = body.machineId
            envBlocked = body.envBlocked
            login = body.github?.login
            error = nil
        } catch is CancellationError {
            // The page went away mid-fetch: nothing failed.
            return
        } catch let e as HubError {
            guard gen == generation else { return }
            error = e.message
        } catch {
            guard gen == generation else { return }
            self.error = error.localizedDescription
        }
        loaded = true
    }

}

extension PullRepo: RepoListing {
    var rows: [PullRow] { pulls ?? [] }
}

typealias PullSection = RepoSection<PullRow>

/// The pulls page's sections and chip counts under its filter.
enum PullGrouping {
    static func sections(of repos: [PullRepo], filter: PullFilter) -> [PullSection] {
        RepoGrouping.sections(of: repos, where: filter.includes)
    }

    static func count(_ repos: [PullRepo], filter: PullFilter) -> Int {
        RepoGrouping.count(repos, where: filter.includes)
    }
}

/// The pulls page's row filters: every open PR, the ones the hub's
/// login authored, the ones it was asked to review.
enum PullFilter: String, CaseIterable, Identifiable {
    case all, mine, reviewRequested

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .mine: "Mine"
        case .reviewRequested: "Review requested"
        }
    }

    func includes(_ pr: PullRow) -> Bool {
        switch self {
        case .all: true
        case .mine: pr.mine == true
        case .reviewRequested: pr.reviewRequested == true
        }
    }
}
