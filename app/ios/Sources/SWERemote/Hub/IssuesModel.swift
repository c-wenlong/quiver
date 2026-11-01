import Foundation
import Observation

/// The issues page's data: one fetch per open, pull to refresh.
/// Kept out of `AppModel`'s 15 s poll — issues change slowly, and the
/// hub already serves them from its one-minute `gh` cache.
@MainActor @Observable
final class IssuesModel {
    /// The page's filter chip, kept here so it outlives the view.
    var filter: IssueFilter = .all
    private(set) var repos: [IssueRepo] = []
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
            let body = try await client.issues(refresh: refresh)
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

    /// The Closed chip's list (`GET /v1/issues?state=closed`), fetched
    /// on the chip's first pick and refreshed like the open one after.
    /// Kept beside `repos`, not inside it, so switching back costs
    /// nothing and the counts stay right.
    private(set) var closedRepos: [IssueRepo] = []
    private(set) var closedLoaded = false
    /// The closed fetch's own failure: the open list's `error` would say
    /// the open rows are stale when they aren't, and the reverse.
    private(set) var closedError: String?
    /// Owns the closed fetch's ordering, as `generation` owns the open
    /// one's: an open pull-to-refresh must not eat a closed reply.
    @ObservationIgnored private var closedGeneration = 0

    func loadClosed(client: HubClient?, refresh: Bool = false) async {
        guard let client else { return }
        closedGeneration += 1
        let gen = closedGeneration
        do {
            let body = try await client.issues(refresh: refresh, state: "closed")
            guard gen == closedGeneration else { return }
            closedRepos = body.repos
            machine = body.machine
            machineId = body.machineId
            envBlocked = body.envBlocked
            login = body.github?.login
            closedError = nil
        } catch is CancellationError {
            // The page went away mid-fetch: nothing failed.
            return
        } catch let e as HubError {
            guard gen == closedGeneration else { return }
            closedError = e.message
        } catch {
            guard gen == closedGeneration else { return }
            closedError = error.localizedDescription
        }
        closedLoaded = true
    }

}

extension IssueRepo: RepoListing {
    var rows: [IssueRow] { issues ?? [] }
}

typealias IssueSection = RepoSection<IssueRow>

/// The issues page's sections and chip counts under its filter.
enum IssueGrouping {
    static func sections(of repos: [IssueRepo], filter: IssueFilter) -> [IssueSection] {
        RepoGrouping.sections(of: repos, where: filter.includes)
    }

    static func count(_ repos: [IssueRepo], filter: IssueFilter) -> Int {
        RepoGrouping.count(repos, where: filter.includes)
    }
}

/// The issues page's row filters: every open issue, the ones the
/// hub's `gh` login wrote, or the closed ones the `?state=closed`
/// read brought back.
enum IssueFilter: String, CaseIterable, Identifiable {
    case all, mine, closed

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: "All"
        case .mine: "Mine"
        case .closed: "Closed"
        }
    }

    func includes(_ issue: IssueRow) -> Bool {
        switch self {
        case .all: true
        case .mine: issue.mine == true
        case .closed: issue.state == "closed"
        }
    }
}

/// `swe://issue/<owner>/<repo>/<n>` — the link an issue's Copy link
/// writes and `AppModel.handle` routes onto the Issues stack. Links
/// written as `quiver://` before the rename open the same way.
struct IssueLink: Equatable {
    /// `owner/repo`, as the hub's routes and list sections name it.
    let repo: String
    let number: Int

    init(repo: String, number: Int) {
        self.repo = repo
        self.number = number
    }

    /// `swe://issue/o/r/5` → `o/r` + 5; anything else is nil.
    init?(_ url: URL) {
        guard ["swe", "quiver"].contains(url.scheme), url.host == "issue" else { return nil }
        // pathComponents keeps the leading "/", so it drops out here.
        let parts = url.pathComponents.filter { $0 != "/" }
        guard parts.count == 3, let number = Int(parts[2]), number >= 1 else { return nil }
        self.init(repo: "\(parts[0])/\(parts[1])", number: number)
    }

    /// The link back. Owner/repo names are URL-safe (alphanumeric,
    /// `-`, `_`, `.`), so the path needs no encoding.
    var url: URL? { URL(string: "swe://issue/\(repo)/\(number)") }
}
