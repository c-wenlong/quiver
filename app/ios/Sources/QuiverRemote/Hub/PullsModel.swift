import Foundation
import Observation

/// The pull-requests page's data: one fetch per open, pull to refresh.
/// Kept out of `AppModel`'s 15 s poll — PRs change slowly, and the hub
/// already serves them from its one-minute `gh` cache.
@MainActor @Observable
final class PullsModel {
    private(set) var repos: [PullRepo] = []
    private(set) var machine: String?
    private(set) var machineId: String?
    private(set) var envBlocked: String?
    private(set) var login: String?
    private(set) var error: String?
    private(set) var loaded = false

    func load(client: HubClient?, refresh: Bool = false) async {
        guard let client else { return }
        do {
            let body = try await client.pulls(refresh: refresh)
            repos = body.repos
            machine = body.machine
            machineId = body.machineId
            envBlocked = body.envBlocked
            login = body.github?.login
            error = nil
        } catch let e as HubError {
            error = e.message
        } catch {
            self.error = error.localizedDescription
        }
        loaded = true
    }

}

/// Grouping and counting for the pulls page, pure so tests can feed it
/// hub rows without an actor.
enum PullGrouping {
    /// The repo sections as the page draws them under `filter`: an
    /// errored repo keeps its slot with the error to show; a quiet one
    /// (no matching rows) drops out entirely.
    static func sections(of repos: [PullRepo], filter: PullFilter) -> [PullSection] {
        repos.compactMap { repo in
            guard repo.status == "ok" else {
                return PullSection(name: repo.name, rows: nil,
                                   error: repo.error ?? repo.status ?? "unknown error")
            }
            let rows = (repo.pulls ?? []).filter(filter.includes)
            return rows.isEmpty ? nil : PullSection(name: repo.name, rows: rows, error: nil)
        }
    }

    /// `filter`'s matching count across every repo — the filter chips.
    static func count(_ repos: [PullRepo], filter: PullFilter) -> Int {
        repos.reduce(0) { sum, repo in
            sum + (repo.pulls ?? []).filter(filter.includes).count
        }
    }
}

/// One section of the pulls page: a pinned repo's matching rows, or
/// the error that dimmed it.
struct PullSection: Identifiable {
    let name: String
    let rows: [PullRow]?
    let error: String?
    var id: String { name }
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
