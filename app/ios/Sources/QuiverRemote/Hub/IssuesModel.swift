import Foundation
import Observation

/// The issues page's data: one fetch per open, pull to refresh.
/// Kept out of `AppModel`'s 15 s poll — issues change slowly, and the
/// hub already serves them from its one-minute `gh` cache.
@MainActor @Observable
final class IssuesModel {
    private(set) var repos: [IssueRepo] = []
    private(set) var machine: String?
    private(set) var machineId: String?
    private(set) var envBlocked: String?
    private(set) var login: String?
    private(set) var error: String?
    private(set) var loaded = false

    func load(client: HubClient?, refresh: Bool = false) async {
        guard let client else { return }
        do {
            let body = try await client.issues(refresh: refresh)
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

/// Grouping and counting for the issues page, pure so tests can feed it
/// hub rows without an actor.
enum IssueGrouping {
    /// The repo sections as the page draws them under `filter`: an
    /// errored repo keeps its slot with the error to show; a quiet one
    /// (no matching rows) drops out entirely.
    static func sections(of repos: [IssueRepo], filter: IssueFilter) -> [IssueSection] {
        repos.compactMap { repo in
            guard repo.status == "ok" else {
                return IssueSection(name: repo.name, rows: nil,
                                    error: repo.error ?? repo.status ?? "unknown error")
            }
            let rows = (repo.issues ?? []).filter(filter.includes)
            return rows.isEmpty ? nil : IssueSection(name: repo.name, rows: rows, error: nil)
        }
    }

    /// `filter`'s matching count across every repo — the filter chips.
    static func count(_ repos: [IssueRepo], filter: IssueFilter) -> Int {
        repos.reduce(0) { sum, repo in
            sum + (repo.issues ?? []).filter(filter.includes).count
        }
    }
}

/// One section of the issues page: a pinned repo's matching rows, or
/// the error that dimmed it.
struct IssueSection: Identifiable {
    let name: String
    let rows: [IssueRow]?
    let error: String?
    var id: String { name }
}

/// The issues page's row filters: every open issue, or the ones the
/// hub's `gh` login wrote.
enum IssueFilter: String, CaseIterable, Identifiable {
    case all, mine

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .mine: "Mine"
        }
    }

    func includes(_ issue: IssueRow) -> Bool {
        switch self {
        case .all: true
        case .mine: issue.mine == true
        }
    }
}
